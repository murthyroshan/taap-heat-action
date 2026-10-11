"""Replay a past heatwave and backtest what the plan would have triggered.

Replay uses ERA5 reanalysis (what actually happened, ~25 km), so the backtest
measures what the plan's rules would have fired on real conditions. It does not
measure forecast skill, and reanalysis runs below station peaks.
"""
from __future__ import annotations

import os

from . import weather
from .engine import day_range
from .plan import TIERS, city_risk, load_city, triggered_actions

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "replay_data")


def archive_series(st, city_id: str) -> dict:
    """Hourly reanalysis for the city's replay window: bundled file -> cache -> Open-Meteo."""
    city = load_city(city_id)
    win = city["replay"]
    bundled = os.path.join(FIXTURE_DIR, f"{city_id}.txt")
    if os.path.exists(bundled):
        with open(bundled, encoding="utf-8") as fh:
            return weather.unpack(fh.read())
    key = f"archive#{city_id}#{win['start']}#{win['end']}"
    cached = st.get("CACHE", key)
    if cached:
        return weather.unpack(cached["blob"])
    raw = weather.fetch_archive_raw(city["zones"], win["start"], win["end"])
    series = weather.parse(raw, city["zones"])
    st.put("CACHE", key, {"blob": weather.pack(series)})
    return series


def backtest(st, city_id: str) -> dict:
    city = load_city(city_id)
    key = f"backtest#{city_id}"
    cached = st.get("CACHE", key)
    if cached:
        return cached
    series = archive_series(st, city_id)
    win = city["replay"]
    days = []
    for day in day_range(win["start"], win["end"]):
        snap = city_risk(city, series, day)
        live = [z for z in snap["zones"] if z.get("available")]
        if not live:
            continue
        days.append({
            "date": day,
            "tier": snap["tier"],
            "max_band": snap["max_band"],
            "tmax": max(z["tmax"] for z in live),
            "peak_wbgt": max(z["peak_wbgt"] for z in live),
            "zones_band3": sum(1 for z in live if z["band"] >= 3),
            "zones_orange": sum(1 for z in live if TIERS.index(z["tier"]) >= TIERS.index("orange")),
            "hot_night_zones": sum(1 for z in live if z["hot_night"]),
            "actions": sum(len(triggered_actions(city, z)) for z in live),
        })
    ref = city.get("reference_event", {})
    first_band3 = next((d["date"] for d in days if d["max_band"] >= 3), None)
    first_orange = next((d["date"] for d in days if TIERS.index(d["tier"]) >= TIERS.index("orange")), None)
    lead = None
    if first_band3 and ref.get("date"):
        from datetime import date
        lead = (date.fromisoformat(ref["date"]) - date.fromisoformat(first_band3)).days
    result = {
        "city": city_id,
        "window": win,
        "days": days,
        "summary": {
            "first_worker_warning": first_band3,
            "first_orange_or_red": first_orange,
            "lead_days_before_reference": lead,
            "warning_from_window_start": first_band3 == win["start"],
            "days_with_band3_plus": sum(1 for d in days if d["max_band"] >= 3),
            "zone_days_band3_plus": sum(d["zones_band3"] for d in days),
            "days_orange_or_red": sum(1 for d in days if TIERS.index(d["tier"]) >= TIERS.index("orange")),
            "hot_night_zone_days": sum(d["hot_night_zones"] for d in days),
            "actions_triggered": sum(d["actions"] for d in days),
            "model_peak_tmax": max((d["tmax"] for d in days), default=None),
        },
        "reference_event": ref,
        "caveat": ("Reanalysis (ERA5, ~25 km) of what happened, not a forecast. It runs below station peaks, "
                   "so treat temperatures as modelled values, not station readings."),
    }
    st.put("CACHE", key, result)
    return result

