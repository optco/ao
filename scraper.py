import json
import re
import time
from playwright.sync_api import sync_playwright

CARS_CO_ZA_URLS = [
    "https://cars.co.za",
    "https://cars.co.za"
]

AUTOTRADER_URLS = [
    "https://autotrader.co.za",
    "https://autotrader.co.za"
]

def clean_number(val):
    if not val:
        return 0
    digits = re.sub(r"[^\d]", "", str(val))
    return int(digits) if digits else 0

def normalize_fuel(raw_fuel):
    f = str(raw_fuel).lower()
    if "plug" in f or "phev" in f:
        return "PHEV"
    if "range" in f or "reev" in f:
        return "REEV"
    if "hybrid" in f or "hev" in f:
        return "HEV"
    if "electric" in f or f == "ev":
        return "Electric"
    return None

def normalize_trans(raw_trans):
    t = str(raw_trans).lower()
    return "Manual" if "manual" in t else "Auto"

def extract_json_ld(page):
    scripts = page.locator('script[type="application/ld+json"]').all_inner_texts()
    items = []
    for s in scripts:
        try:
            data = json.loads(s)
            if isinstance(data, list):
                items.extend(data)
            elif isinstance(data, dict):
                if "@graph" in data:
                    items.extend(data["@graph"])
                else:
                    items.append(data)
        except Exception:
            continue
    return items

def scrape_site(page, urls, source_name):
    results = []
    for url in urls:
        page.goto(url, wait_until="networkidle", timeout=60000)
        time.sleep(3)

        ld_items = extract_json_ld(page)
        for item in ld_items:
            item_type = str(item.get("@type", ""))
            if "Vehicle" in item_type or "Car" in item_type or "Product" in item_type:
                fuel_type = normalize_fuel(item.get("fuelType"))
                if not fuel_type:
                    continue

                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}

                price = clean_number(offers.get("price"))
                if price == 0:
                    continue

                mileage_obj = item.get("mileageFromOdometer", {})
                miles_km = clean_number(mileage_obj.get("value") if isinstance(mileage_obj, dict) else mileage_obj)

                engine_obj = item.get("vehicleEngine", {})
                engine_cc = clean_number(engine_obj.get("engineDisplacement", {}).get("value") if isinstance(engine_obj, dict) else 0)
                kw = clean_number(engine_obj.get("enginePower", {}).get("value") if isinstance(engine_obj, dict) else 0)

                img_val = item.get("image", "")
                if isinstance(img_val, list):
                    img_val = img_val[0] if img_val else ""

                results.append({
                    "id": f"{source_name.lower()}-{clean_number(item.get('sku') or offers.get('url'))}",
                    "source": source_name,
                    "url": offers.get("url") or item.get("url") or url,
                    "image": img_val or "https://placeholder.com",
                    "make": item.get("brand", {}).get("name", "Unknown") if isinstance(item.get("brand"), dict) else str(item.get("brand", "Unknown")),
                    "model": str(item.get("model", "Unknown")),
                    "type": str(item.get("bodyType", "SUV")),
                    "year": clean_number(item.get("vehicleModelDate") or item.get("productionDate")),
                    "price": price,
                    "miles": miles_km,
                    "engine_cc": engine_cc,
                    "kw": kw,
                    "seats": clean_number(item.get("seatingCapacity")) or 5,
                    "fuel": fuel_type,
                    "transmission": normalize_trans(item.get("vehicleTransmission"))
                })
    return results

def main():
    combined_cars = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        combined_cars.extend(scrape_site(page, CARS_CO_ZA_URLS, "Cars.co.za"))
        combined_cars.extend(scrape_site(page, AUTOTRADER_URLS, "AutoTrader.co.za"))

        browser.close()

    unique_cars = {car["url"]: car for car in combined_cars if car["url"]}.values()

    with open("cars.json", "w", encoding="utf-8") as f:
        json.dump(list(unique_cars), f, indent=2)

    print(f"Saved {len(unique_cars)} cars to cars.json")

if __name__ == "__main__":
    main()
