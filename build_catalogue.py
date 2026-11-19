"""
Build cars.json (worldwide electrified-car catalogue) from the free Open EV Data dataset
(https://github.com/open-ev-data/open-ev-data-dataset, CDLA-Permissive-2.0 - credit it on the site),
then merge in manual_cars.json (hand-kept prices per market).

Run it yourself when you want fresh specs - no daily crawl, no scraping:
    python build_catalogue.py                    # downloads latest release
    python build_catalogue.py --input oed.json   # use a file you already have
"""
import argparse, json, re, urllib.request

REPO = "open-ev-data/open-ev-data-dataset"
BODY = {"crossover": "SUV", "suv": "SUV", "suv_coupe": "SUV", "sedan": "Sedan", "fastback": "Sedan",
        "hatchback": "Hatchback", "wagon": "Hatchback", "pickup": "Pickup", "mpv": "MPV",
        "minivan": "MPV", "van": "MPV", "coupe": "Coupe", "convertible": "Coupe"}
CYCLES = ["wltp", "epa", "nedc", "cltc", "jc08"]


def download():
    tag = urllib.request.urlopen(f"https://github.com/{REPO}/releases/latest").geturl().rsplit("/", 1)[-1]
    url = f"https://github.com/{REPO}/releases/download/{tag}/open-ev-data-{tag}.json"
    print("Downloading", url)
    return json.load(urllib.request.urlopen(url))


def get(d, *path):
    for k in path:
        d = d.get(k) if isinstance(d, dict) else None
    return d


def best_range(v):
    rated = get(v, "range", "rated") or []
    for cyc in CYCLES:
        for r in rated:
            if str(r.get("cycle", "")).lower() == cyc and r.get("range_km"):
                return round(r["range_km"]), cyc.upper()
    for r in rated:
        if r.get("range_km"):
            return round(r["range_km"]), str(r.get("cycle", "")).upper()
    return 0, ""


def is_phev(v):
    text = (v["model"]["name"] + " " + json.dumps(get(v, "range", "rated") or []) + " " +
            json.dumps(get(v, "charging", "ac") or {})).lower()
    return "phev" in text or "plug-in" in text or "electric-only" in text


def convert(v):
    rng, cyc = best_range(v)
    src = next((s.get("url") for s in (v.get("sources") or []) if s.get("type") == "oem" and s.get("url")),
               next((s.get("url") for s in (v.get("sources") or []) if s.get("url")), ""))
    return {
        "id": v.get("unique_code") or re.sub(r"\W+", "-", f'{v["make"]["slug"]}-{v["model"]["slug"]}-{v["trim"]["slug"]}-{v["year"]}').lower(),
        "make": v["make"]["name"], "model": v["model"]["name"],
        "trim": "" if v["trim"]["name"].lower() == "base" else v["trim"]["name"],
        "year": v["year"], "type": BODY.get(get(v, "body", "style"), "SUV"),
        "fuel": "PHEV" if is_phev(v) else "Electric",
        "kw": round(get(v, "powertrain", "system_power_kw") or 0),
        "seats": int(get(v, "body", "seats") or 0),
        "battery_kwh": round(get(v, "battery", "pack_capacity_kwh_net") or get(v, "battery", "pack_capacity_kwh_gross") or 0, 1),
        "range_km": rng, "range_cycle": cyc,
        "dc_kw": round(get(v, "charging", "dc", "max_power_kw") or 0),
        "markets": sorted(v.get("markets") or []), "prices": {},
        "url": src, "source": "open-ev-data",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input")
    ap.add_argument("--out", default="cars.json")
    a = ap.parse_args()
    data = json.load(open(a.input, encoding="utf-8")) if a.input else download()

    best = {}
    for v in data["vehicles"]:
        status = (v.get("availability") or {}).get("status")
        if status in ("discontinued", "announced"):
            continue
        c = convert(v)
        key = (c["make"], c["model"], c["trim"], c["kw"], c["battery_kwh"])
        if key not in best or c["year"] > best[key]["year"]:
            best[key] = c
    cars = list(best.values())

    seen = set()
    for c in cars:                       # make ids unique
        base, n = c["id"], 2
        while c["id"] in seen:
            c["id"], n = f"{base}-{n}", n + 1
        seen.add(c["id"])

    cars += json.load(open("manual_cars.json", encoding="utf-8"))
    cars.sort(key=lambda c: (c["make"], c["model"], c["trim"]))
    json.dump(cars, open(a.out, "w", encoding="utf-8"), separators=(",", ":"))
    print(f"Wrote {len(cars)} cars to {a.out}")


if __name__ == "__main__":
    main()
