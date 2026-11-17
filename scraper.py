"""
SA Hybrid & Electric Car Aggregator - Scraper

Scrapes cars.co.za and autotrader.co.za search-result pages (NOT homepages)
for hybrid/electric vehicle listings and writes cars.json for index.html.

Key fixes over the old version:
  * Points at real search-result URLs with fuel-type filters applied.
  * Handles bot protection / blocked responses (403/503, challenge pages).
  * Parses listing cards from the DOM in addition to JSON-LD blocks.
  * Only keeps hybrid/electric vehicles (Petrol/Diesel are filtered out).
  * Never overwrites a good cars.json with an empty result set.
  * Retries + polite delays between requests/pages.

Usage:
    pip install playwright && playwright install chromium
    python scraper.py            # scrape and write cars.json
    python scraper.py --dry-run  # scrape but don't touch cars.json
"""

import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Search-result URLs (filtered to hybrid/electric only). Adjust MAX_PAGES per site.
# ---------------------------------------------------------------------------
CARS_CO_ZA_SEARCHES = [
    # cars.co.za search with fuel-type refinements
    "https://www.cars.co.za/usedcars/?sort=sort_rank&price_type=listing_price&transmission=Automatic&fuel_type=h&P=1",
    "https://www.cars.co.za/usedcars/?sort=sort_rank&price_type=listing_price&transmission=Automatic&fuel_type=e&P=1",
    "https://www.cars.co.za/usedcars/?sort=sort_rank&price_type=listing_price&transmission=Automatic&fuel_type=x&P=1",
]

AUTOTRADER_SEARCHES = [
    # autotrader.co.za primary fuel type "Hybrid-Electric"
    "https://www.autotrader.co.za/cars-for-sale?fueltype=Electric&fueltype=Hybrid",
]

MAX_PAGES_CARS_CO_ZA = 1          # each query above is one page of results
MAX_PAGES_AUTOTRADER = 3          # paginate through hybrid-electric results
REQUEST_TIMEOUT_MS = 60_000
DELAY_BETWEEN_PAGES_S = 4         # be polite / reduce block risk
RETRIES = 2

VALID_FUEL = {"HEV", "PHEV", "REEV", "Electric"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def clean_number(val):
    """Extract digits from strings like 'R 389 900' or '35 000 km' -> int."""
    if val is None:
        return 0
    digits = re.sub(r"[^\d]", "", str(val))
    return int(digits) if digits else 0


def normalize_fuel(raw):
    """Map any fuel description to HEV/PHEV/REEV/Electric, or None if not e/hybrid."""
    f = str(raw or "").lower()
    if not f:
        return None
    if "plug" in f or "phev" in f:
        return "PHEV"
    if "range" in f or "reev" in f:
        return "REEV"
    if "electric" in f and "hybrid" not in f:
        return "Electric"
    if "ev" == f.strip():
        return "Electric"
    if "hybrid" in f or "hev" in f or "e-power" in f or "e-power" in f:
        return "HEV"
    # plain petrol/diesel/etc -> excluded by this aggregator
    return None


def normalize_trans(raw):
    t = str(raw or "").lower()
    if "manual" in t:
        return "Manual"
    return "Auto"


def looks_blocked(html):
    """Crude bot-wall detector."""
    h = html.lower()
    markers = ["cf-browser-verification", "just a moment", "attention required",
               "access denied", "captcha", "enable javascript and cookies",
               "ddos-guard", "px-captcha"]
    return any(m in h for m in markers)


def extract_json_ld(page):
    """Return all parsed JSON-LD objects found on the page."""
    items = []
    try:
        scripts = page.locator('script[type="application/ld+json"]').all_inner_texts()
    except Exception:
        return items
    for s in scripts:
        try:
            data = json.loads(s)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                if "@graph" in d and isinstance(d["@graph"], list):
                    stack.extend(d["@graph"])
                else:
                    items.append(d)
    return items


def car_from_ld_item(item, source_name, page_url):
    """Build a normalized car dict from a schema.org Vehicle/Product JSON-LD item, or None."""
    t = str(item.get("@type", ""))
    if not any(k in t for k in ("Vehicle", "Car", "Product")):
        return None

    fuel_raw = (item.get("fuelType") or item.get("powerType")
                or item.get("vehicleSpecialType") or "")
    # some sites put fuel info in additionalProperty name/value pairs
    if not fuel_raw:
        for prop in item.get("additionalProperty", []) or []:
            if isinstance(prop, dict) and "fuel" in str(prop.get("name", "")).lower():
                fuel_raw = prop.get("value", "")
                break
    fuel = normalize_fuel(fuel_raw)
    if fuel not in VALID_FUEL:
        return None

    offers = item.get("offers", {})
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = clean_number(offers.get("price"))
    if price == 0:
        return None

    mileage_obj = item.get("mileageFromOdometer", {})
    miles_km = clean_number(mileage_obj.get("value") if isinstance(mileage_obj, dict) else mileage_obj)

    engine_obj = item.get("vehicleEngine", {})
    engine_cc = kw = 0
    if isinstance(engine_obj, dict):
        disp = engine_obj.get("engineDisplacement", {})
        engine_cc = clean_number(disp.get("value") if isinstance(disp, dict) else disp)
        power = engine_obj.get("enginePower", {})
        kw = clean_number(power.get("value") if isinstance(power, dict) else power)

    img_val = item.get("image", "")
    if isinstance(img_val, list):
        img_val = img_val[0] if img_val else ""
    if isinstance(img_val, dict):
        img_val = img_val.get("url", "")
    img_val = urljoin(page_url, img_val) if img_val else ""

    brand = item.get("brand", "Unknown")
    make = brand.get("name", "Unknown") if isinstance(brand, dict) else str(brand)

    url = offers.get("url") or item.get("url") or page_url
    ref = str(item.get("sku") or item.get("productId") or url)

    return {
        "id": f"{source_name.split('.')[0].lower()}-{clean_number(ref) or abs(hash(url)) % 10**9}",
        "source": source_name,
        "url": url,
        "image": img_val or "https://via.placeholder.com/600x400?text=No+Image",
        "make": make,
        "model": str(item.get("model") or item.get("name") or "Unknown"),
        "type": str(item.get("bodyType") or "SUV"),
        "year": clean_number(item.get("vehicleModelDate") or item.get("productionDate")),
        "price": price,
        "miles": miles_km,
        "engine_cc": engine_cc,
        "kw": kw,
        "seats": clean_number(item.get("seatingCapacity")) or 5,
        "fuel": fuel,
        "transmission": normalize_trans(item.get("vehicleTransmission")),
    }


# ---------------------------------------------------------------------------
# Fallback DOM-based card parsing (when sites don't ship JSON-LD vehicles)
# ---------------------------------------------------------------------------
CARD_SELECTORS = {
    "Cars.co.za": [
        "[data-qa='searchResultCard']", ".srp-card", "article.listing",
        "[class*='ListingCard']", "[class*='search-result'] a[href*='/detail/']",
    ],
    "AutoTrader.co.za": [
        "[data-qa='card-list-item']", ".atc_SearchResultCard", "article[class*='SearchResult']",
        "[class*='listing-card']",
    ],
}

TEXT_HINTS = {
    "price": r"(R\s?[\d\s]{6,})",
    "mileage": r"([\d\s.,]+)\s?(km|KM)",
    "year": r"\b(19|20)\d{2}\b",
}


def scrape_cards_dom(page, source_name, page_url):
    """Best-effort extraction from listing-card DOM. Returns list of car dicts."""
    cars = []
    selectors = CARD_SELECTORS.get(source_name, [])
    cards = []
    for sel in selectors:
        try:
            found = page.locator(sel).all()
            if found:
                cards = found
                break
        except Exception:
            continue
    for card in cards[:60]:  # cap per page
        try:
            text = card.inner_text(timeout=5000)
            link_el = card.locator("a[href]").first
            href = link_el.get_attribute("href", timeout=5000) or ""
            url = urljoin(page_url, href) if href else page_url

            img_el = card.locator("img").first
            image = ""
            try:
                image = img_el.get_attribute("src", timeout=3000) or \
                        img_el.get_attribute("data-src", timeout=3000) or ""
            except Exception:
                pass

            price_m = re.search(TEXT_HINTS["price"], text)
            price = clean_number(price_m.group(1)) if price_m else 0
            year_m = re.search(r"\b((?:19|20)\d{2})\b", text)
            year = clean_number(year_m.group(1)) if year_m else 0
            km_m = re.search(r"([\d\s.,]+)\s?km", text, re.I)
            miles = clean_number(km_m.group(1)) if km_m else 0

            fuel = normalize_fuel(text)  # detects 'hybrid', 'electric', 'plug-in' words
            if price == 0 or fuel not in VALID_FUEL:
                continue

            lines = [l.strip() for l in text.splitlines() if l.strip()]
            header = lines[0] if lines else "Unknown Unknown"
            parts = header.replace("/", " ").split()
            make, model = (parts[0], " ".join(parts[1:]) or "Unknown") if parts else ("Unknown", "Unknown")

            cars.append({
                "id": f"{source_name.split('.')[0].lower()}-dom-{abs(hash(url)) % 10**9}",
                "source": source_name,
                "url": url,
                "image": image or "https://via.placeholder.com/600x400?text=No+Image",
                "make": make.capitalize(),
                "model": model,
                "type": "SUV",
                "year": year,
                "price": price,
                "miles": miles,
                "engine_cc": 0,
                "kw": 0,
                "seats": 5,
                "fuel": fuel,
                "transmission": normalize_trans(text),
            })
        except Exception:
            continue
    return cars


# ---------------------------------------------------------------------------
# Site scraping with retry / block handling
# ---------------------------------------------------------------------------
def goto_with_retry(page, url):
    for attempt in range(RETRIES + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=REQUEST_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except PWTimeout:
                pass  # ads/analytics may never go idle; fine
            html = page.content()
            if looks_blocked(html):
                print(f"  [block] Bot-protection page at {url} (attempt {attempt + 1})")
                time.sleep(5 * (attempt + 1))
                continue
            if len(html) < 5000:
                print(f"  [empty] Suspiciously small page ({len(html)} bytes), retrying")
                time.sleep(3)
                continue
            return True
        except PWTimeout:
            print(f"  [timeout] {url} (attempt {attempt + 1})")
            time.sleep(3)
        except Exception as e:
            print(f"  [error] {url}: {e}")
            time.sleep(3)
    return False


def scrape_site(context, search_urls, source_name, max_pages=1, page_param=True):
    results = []
    page = context.new_page()
    try:
        for template in search_urls:
            urls = ([template.format(page=p) for p in range(1, max_pages + 1)]
                    if page_param and "{page}" in template else [template])
            for url in urls:
                print(f"[{source_name}] {url}")
                if not goto_with_retry(page, url):
                    print(f"  giving up on {url}")
                    continue

                cars_here = []
                for item in extract_json_ld(page):
                    car = car_from_ld_item(item, source_name, url)
                    if car:
                        cars_here.append(car)

                if not cars_here:
                    print("  no JSON-LD vehicles found, trying DOM card parser...")
                    cars_here = scrape_cards_dom(page, source_name, url)

                print(f"  -> {len(cars_here)} hybrid/electric listings")
                results.extend(cars_here)
                time.sleep(DELAY_BETWEEN_PAGES_S)
    finally:
        page.close()
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="scrape but do not write cars.json")
    ap.add_argument("--out", default="cars.json")
    args = ap.parse_args()

    combined = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=USER_AGENT,
            locale="en-ZA",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        # stealth-ish tweaks
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        combined += scrape_site(context, CARS_CO_ZA_SEARCHES, "Cars.co.za",
                                max_pages=MAX_PAGES_CARS_CO_ZA, page_param=False)
        combined += scrape_site(context, AUTOTRADER_SEARCHES, "AutoTrader.co.za",
                                max_pages=MAX_PAGES_AUTOTRADER)
        browser.close()

    # de-dupe by URL
    unique = {c["url"]: c for c in combined if c.get("url") and c.get("price")}
    cars = sorted(unique.values(), key=lambda c: c["price"])
    print(f"\nTotal unique hybrid/electric listings scraped: {len(cars)}")

    if not cars:
        print("WARNING: scraped 0 cars - keeping existing cars.json untouched.")
        print("Sites likely served a bot-block page. Options: run from a residential IP,")
        print("lower frequency in the GitHub Action cron, or add official API feeds here.")
        sys.exit(1)

    if args.dry_run:
        print(json.dumps(cars[:3], indent=2))
        print("(dry run - cars.json NOT written)")
        return

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(cars, f, indent=2)
    print(f"Saved {len(cars)} cars to {args.out}")


if __name__ == "__main__":
    main()
