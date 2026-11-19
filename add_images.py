"""
Add free-licensed photos from Wikimedia Commons to cars.json.

For every make+model it searches Commons, keeps the first sensible exterior photo under a
free licence, and stores a direct link plus the credit text (most Commons licences require
attribution, which the page shows under the card).

    python add_images.py            # fills cars.json (resumable; results cached in images_cache.json)
    python add_images.py --retry    # retry models that previously found nothing

Search is automatic, so SOME PHOTOS WILL BE WRONG (a different generation, a similar model).
Spot-check, and fix any by editing images_cache.json (set "url", "page", "credit") then rerun.
"""
import argparse, json, os, re, time, urllib.parse, urllib.request

API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "AlternateOption/1.0 (https://alternateoption.com; car catalogue image lookup)"}
CACHE = "images_cache.json"
BAD = re.compile(r"interior|dashboard|engine|charging|charger|logo|badge|emblem|wheel|seat|trunk|boot|cockpit|"
                 r"screen|display|battery|chassis|cutaway|drawing|render|sketch|brochure|concept|crash|plug", re.I)
FREE = re.compile(r"^(cc[- ]by|cc0|public domain|pd|attribution)", re.I)


def call(params):
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))


def strip(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()


def find(make, model):
    words = [w for w in re.split(r"[\s\-]+", model.lower()) if len(w) > 1][:2]
    for query in (f"{make} {model}", f"{make} {model} car"):
        data = call({"action": "query", "generator": "search", "gsrsearch": f"{query} filetype:bitmap",
                     "gsrnamespace": 6, "gsrlimit": 15, "prop": "imageinfo",
                     "iiprop": "url|extmetadata|mime", "iiurlwidth": 800})
        for pg in sorted((data.get("query", {}).get("pages", {})).values(), key=lambda p: p.get("index", 99)):
            title = pg["title"]
            info = (pg.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            lic = strip(meta.get("LicenseShortName", {}).get("value"))
            low = title.lower()
            if info.get("mime") not in ("image/jpeg", "image/png") or BAD.search(title):
                continue
            if make.lower().split()[0] not in low or not all(w in low for w in words):
                continue
            if not FREE.match(lic):
                continue
            author = strip(meta.get("Artist", {}).get("value")) or "Wikimedia Commons"
            return {"url": info.get("thumburl") or info["url"], "page": info.get("descriptionurl", ""),
                    "credit": f"{author[:60]}, {lic}"}
        time.sleep(0.5)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--retry", action="store_true")
    a = ap.parse_args()
    cars = json.load(open("cars.json", encoding="utf-8"))
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    keys = sorted({(c["make"], c["model"]) for c in cars})
    for i, (make, model) in enumerate(keys, 1):
        k = f"{make}|{model}"
        if k in cache and (cache[k] or not a.retry):
            continue
        try:
            cache[k] = find(make, model)
        except Exception as e:
            print("  error", k, e); continue
        print(f"[{i}/{len(keys)}] {k}: {'ok' if cache[k] else 'no image'}")
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), indent=1)
        time.sleep(0.6)
    n = 0
    for c in cars:
        hit = cache.get(f'{c["make"]}|{c["model"]}')
        if hit:
            c["image"], c["image_page"], c["image_credit"] = hit["url"], hit["page"], hit["credit"]; n += 1
    json.dump(cars, open("cars.json", "w", encoding="utf-8"), separators=(",", ":"))
    print(f"Added images to {n} of {len(cars)} cars")


if __name__ == "__main__":
    main()
