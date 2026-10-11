"""Hourly EventBridge Scheduler target: run every city's plan on the live forecast."""
from __future__ import annotations

import json
import os

from heatplan import engine, store, weather
from heatplan.plan import list_cities, load_city


def run_city(city_id: str, st=None, notify: bool = True) -> dict:
    st = st or store.get_store()
    city = load_city(city_id)
    series = weather.parse(weather.fetch_forecast_raw(city["zones"]), city["zones"])
    snap = engine.run(city_id, "live", engine.today_ist(), series, st, notify=notify)
    return {"city": city_id, "tier": snap["tier"], "max_band": snap["max_band"], **snap["stats"]}


def handler(event, context):
    cities = [c for c in (os.environ.get("CITIES") or ",".join(list_cities())).split(",") if c]
    results = []
    for c in cities:
        try:
            results.append(run_city(c))
        except Exception as e:  # one city failing must not stop the others
            print(f"tick failed for {c}: {e}")
            results.append({"city": c, "error": str(e)})
    print(json.dumps(results))
    return results
