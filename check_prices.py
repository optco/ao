"""Validate cars.json and flag stale prices. Does not modify any file."""
import json, sys
from datetime import date, datetime

MAX_AGE_DAYS = 30
REQUIRED = ["id", "make", "model", "type", "fuel", "price", "url", "price_checked"]
FUELS = {"HEV", "PHEV", "REEV", "Electric"}

cars = json.load(open("cars.json", encoding="utf-8"))
problems, seen = [], set()
today = date.today()

for c in cars:
    cid = c.get("id", "?")
    for k in REQUIRED:
        if not c.get(k):
            problems.append(f"{cid}: missing {k}")
    if c.get("fuel") not in FUELS:
        problems.append(f"{cid}: fuel must be one of {sorted(FUELS)}")
    if cid in seen:
        problems.append(f"{cid}: duplicate id")
    seen.add(cid)
    try:
        age = (today - datetime.strptime(c["price_checked"], "%Y-%m-%d").date()).days
        if age > MAX_AGE_DAYS:
            problems.append(f"{cid}: price last checked {age} days ago")
    except Exception:
        problems.append(f"{cid}: bad price_checked date")

print(f"{len(cars)} cars checked")
if problems:
    print("\n".join(problems))
    sys.exit(1)
print("All good")
