"""Flag stale or incomplete hand-kept prices in manual_cars.json. Does not modify files."""
import json, sys
from datetime import date, datetime

MAX_AGE_DAYS = 30
cars = json.load(open("manual_cars.json", encoding="utf-8"))
problems = []
for c in cars:
    if not c.get("prices"):
        problems.append(f'{c["id"]}: no prices')
    for market, p in (c.get("prices") or {}).items():
        for k in ("price", "currency", "url", "checked"):
            if not p.get(k):
                problems.append(f'{c["id"]} [{market}]: missing {k}')
        try:
            age = (date.today() - datetime.strptime(p["checked"], "%Y-%m-%d").date()).days
            if age > MAX_AGE_DAYS:
                problems.append(f'{c["id"]} [{market}]: price last checked {age} days ago')
        except Exception:
            problems.append(f'{c["id"]} [{market}]: bad checked date')
print(f"{len(cars)} hand-priced cars checked")
if problems:
    print("\n".join(problems)); sys.exit(1)
print("All good")
