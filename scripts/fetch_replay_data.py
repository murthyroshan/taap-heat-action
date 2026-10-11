"""Download the replay heatwave (ERA5 via Open-Meteo) and bundle it into the Lambda package.

Run once from your own machine before `sam build` (Open-Meteo rate-limits shared IPs):
    python scripts/fetch_replay_data.py            # all cities
    python scripts/fetch_replay_data.py delhi
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from heatplan import weather  # noqa: E402
from heatplan.plan import list_cities, load_city  # noqa: E402
from heatplan.replay import FIXTURE_DIR  # noqa: E402

for city_id in sys.argv[1:] or list_cities():
    city = load_city(city_id)
    win = city["replay"]
    print(f"{city_id}: {win['start']} .. {win['end']} for {len(city['zones'])} zones")
    series = weather.parse(weather.fetch_archive_raw(city["zones"], win["start"], win["end"]), city["zones"])
    out = os.path.join(FIXTURE_DIR, f"{city_id}.txt")
    with open(out, "w") as fh:
        fh.write(weather.pack(series))
    peak = max(r["temp"] for rows in series.values() for r in rows if r["temp"] is not None)
    print(f"  wrote {out} ({os.path.getsize(out) // 1024} KB); modelled peak temperature {peak} C")
