"""City plans and per-zone heat risk.

Two scales, side by side:
  * the official plan tier (green/yellow/orange/red) from the day's max temperature,
    escalated one level after a hot night, exactly as the city's plan is written;
  * the worker band (0-4) from hourly WBGT, mapped to work-rest cycles.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from functools import lru_cache

from .wbgt import IST, wbgt

CITY_DIR = os.path.join(os.path.dirname(__file__), "cities")
TIERS = ["green", "yellow", "orange", "red"]

# Work-rest bands for heavy, acclimatised outdoor work, from the ACGIH TLV
# screening table (heavy work: 27.5 C at 50-75% work, 29.0 at 25-50%, 30.5 at 0-25%).
# The first step (25 C) is a precautionary action level. Guidance, not medical advice.
BANDS = [
    {"band": 0, "min": None, "work": 60, "rest": 0, "label": "Normal", "label_hi": "सामान्य"},
    {"band": 1, "min": 25.0, "work": 45, "rest": 15, "label": "Caution", "label_hi": "सावधान"},
    {"band": 2, "min": 27.5, "work": 30, "rest": 30, "label": "High", "label_hi": "ज़्यादा खतरा"},
    {"band": 3, "min": 29.0, "work": 15, "rest": 45, "label": "Very high", "label_hi": "बहुत ज़्यादा खतरा"},
    {"band": 4, "min": 30.5, "work": 0, "rest": 60, "label": "Extreme: stop heavy work", "label_hi": "अत्यधिक खतरा: भारी काम बंद"},
]


# Workers new to heavy outdoor work (or to the city) in the last ~2 weeks are not
# acclimatised. ACGIH's action limits for unacclimatised heavy work sit 2.5-3.5 C
# below the TLVs above (24.0 / 25.5 / 28.0), so their bands start earlier.
UNACCLIMATISED_MIN = [None, 22.0, 24.0, 25.5, 28.0]


def band_for(w: float, new_worker: bool = False) -> int:
    mins = UNACCLIMATISED_MIN if new_worker else [r["min"] for r in BANDS]
    b = 0
    for band in range(1, len(BANDS)):
        if w >= mins[band]:
            b = band
    return b


def _window(hourly: list[dict], worst: int):
    risky = [x["hour"] for x in hourly if x["band"] >= max(worst, 1) - 1 and x["band"] >= 1]
    return (_hour_label(min(risky)), _hour_label(max(risky) + 1)) if risky else (None, None)


def personalise(zr: dict, new_worker: bool) -> dict:
    """Zone risk re-banded for one worker (stricter limits for new, unacclimatised workers)."""
    if not zr or not zr.get("available") or not new_worker:
        return zr
    hourly = [{**h, "band": band_for(h["wbgt"], True)} for h in zr["hourly"]]
    worst = max(h["band"] for h in hourly)
    start, end = _window(hourly, worst)
    b = BANDS[worst]
    return {**zr, "hourly": hourly, "band": worst, "work": b["work"], "rest": b["rest"],
            "start": start, "end": end, "new_worker": True}


@lru_cache(maxsize=None)
def load_city(city_id: str) -> dict:
    with open(os.path.join(CITY_DIR, f"{city_id}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def list_cities() -> list[str]:
    return sorted(f[:-5] for f in os.listdir(CITY_DIR) if f.endswith(".json"))


def tier_for(tmax: float, tiers: dict) -> str:
    t = "green"
    for name in ("yellow", "orange", "red"):
        if tmax >= tiers[name]:
            t = name
    return t


def escalate(tier: str) -> str:
    return TIERS[min(TIERS.index(tier) + 1, len(TIERS) - 1)]


def tier_at_least(tier: str, minimum: str) -> bool:
    return TIERS.index(tier) >= TIERS.index(minimum)


def _hour_label(h: int) -> str:
    return f"{h:02d}:00"


def zone_risk(zone: dict, hours: list[dict], day: str, city: dict) -> dict:
    """Risk for one zone on one IST calendar day.

    `hours` are dicts with keys time (aware UTC datetime), temp, rh, wind, sw, direct, p.
    They should cover the previous evening as well, for the hot-night check.
    """
    day_d = datetime.strptime(day, "%Y-%m-%d").date()
    night_start = datetime.combine(day_d - timedelta(days=1), datetime.min.time(), IST) + timedelta(hours=18)
    night_end = datetime.combine(day_d, datetime.min.time(), IST) + timedelta(hours=8)

    hourly, night_temps = [], []
    for h in hours:
        local = h["time"].astimezone(IST)
        if night_start <= local < night_end and h.get("temp") is not None:
            night_temps.append(h["temp"])
        # A missing humidity or sunshine value would read as dry air / no sun and understate the risk,
        # so such hours are skipped rather than guessed. Missing wind is fine: calm air is the cautious case.
        if local.date() != day_d or h.get("temp") is None or h.get("rh") is None or h.get("sw") is None:
            continue
        w = wbgt(h["temp"], h["rh"], h.get("wind") or 0, h["sw"], h.get("direct") or 0,
                 zone["lat"], zone["lon"], h["time"], h.get("p") or 1000.0)
        hourly.append({"hour": local.hour, "temp": round(h["temp"], 1), "rh": round(h.get("rh") or 0),
                       "wbgt": w["wbgt"], "band": band_for(w["wbgt"])})

    if not hourly:
        return {"zone": zone["id"], "available": False}

    tmax = max(x["temp"] for x in hourly)
    peak = max(hourly, key=lambda x: x["wbgt"])
    worst = peak["band"]
    start, end = _window(hourly, worst)
    night_min = min(night_temps) if night_temps else None
    hot_night = night_min is not None and night_min >= city["hot_night_c"]
    base_tier = tier_for(tmax, city["tiers"])
    tier = escalate(base_tier) if hot_night and base_tier != "green" else base_tier
    b = BANDS[worst]
    return {
        "zone": zone["id"],
        "available": True,
        "tmax": round(tmax, 1),
        "night_min": round(night_min, 1) if night_min is not None else None,
        "hot_night": hot_night,
        "base_tier": base_tier,
        "tier": tier,
        "peak_wbgt": peak["wbgt"],
        "peak_hour": peak["hour"],
        "band": worst,
        "work": b["work"],
        "rest": b["rest"],
        "start": start,
        "end": end,
        "hourly": hourly,
    }


def city_risk(city: dict, series: dict, day: str) -> dict:
    """series: zone_id -> hours. Returns the snapshot for the whole city."""
    zones = [zone_risk(z, series.get(z["id"], []), day, city) for z in city["zones"]]
    live = [z for z in zones if z.get("available")]
    city_tier = "green"
    for z in live:
        if TIERS.index(z["tier"]) > TIERS.index(city_tier):
            city_tier = z["tier"]
    return {
        "city": city["id"],
        "date": day,
        "tier": city_tier,
        "max_band": max((z["band"] for z in live), default=0),
        "zones": zones,
    }


def triggered_actions(city: dict, zr: dict) -> list[dict]:
    """Plan actions whose trigger condition is met in this zone."""
    if not zr.get("available") or not tier_at_least(zr["tier"], city.get("actions_min_tier", "yellow")):
        return []  # the plan is not active: workers still get WBGT guidance, departments are not tasked
    out = []
    for a in city["actions"]:
        cond = a["when"]
        hit = False
        if "band" in cond and zr["band"] >= cond["band"]:
            hit = True
        if "tier" in cond and tier_at_least(zr["tier"], cond["tier"]):
            hit = True
        if hit:
            out.append(a)
    return out


def zone_by_id(city: dict, zone_id: str) -> dict | None:
    return next((z for z in city["zones"] if z["id"] == zone_id), None)


ACTION_FIELDS = {"id", "role", "when", "text", "ref"}


def validate_city(cfg: dict, require_zones: bool = True) -> list[str]:
    """Problems that would stop a city plan from running (empty list = valid)."""
    errs = []
    for key in ("id", "name", "plan_name", "tiers", "hot_night_c", "roles", "actions"):
        if key not in cfg:
            errs.append(f"missing '{key}'")
    tiers = cfg.get("tiers") or {}
    try:
        y, o, r = (float(tiers[k]) for k in ("yellow", "orange", "red"))
        if not 30 <= y < o < r <= 55:
            errs.append("tiers must satisfy 30 <= yellow < orange < red <= 55 (deg C)")
    except (KeyError, TypeError, ValueError):
        errs.append("tiers needs numeric yellow, orange and red")
    try:
        if not 20 <= float(cfg.get("hot_night_c")) <= 40:
            errs.append("hot_night_c should be between 20 and 40 deg C")
    except (TypeError, ValueError):
        errs.append("hot_night_c must be a number")
    roles = cfg.get("roles") or {}
    seen = set()
    for i, a in enumerate(cfg.get("actions") or []):
        where = f"action {i + 1} ({a.get('id', '?') if isinstance(a, dict) else '?'})"
        if not isinstance(a, dict) or not ACTION_FIELDS <= set(a):
            errs.append(f"{where}: needs {sorted(ACTION_FIELDS)}")
            continue
        if not (isinstance(a["id"], str) and a["id"].isalnum() and len(a["id"]) <= 12):
            errs.append(f"{where}: id must be short and alphanumeric")
        elif a["id"] in seen:
            errs.append(f"{where}: duplicate id")
        else:
            seen.add(a["id"])
        if a["role"] not in roles:
            errs.append(f"{where}: role '{a['role']}' is not in roles")
        when = a["when"] if isinstance(a["when"], dict) else {}
        if not when or set(when) - {"band", "tier"}:
            errs.append(f"{where}: 'when' needs 'band' (1-4) and/or 'tier' (yellow/orange/red)")
        if "band" in when and when["band"] not in (1, 2, 3, 4):
            errs.append(f"{where}: band must be 1-4")
        if "tier" in when and when["tier"] not in ("yellow", "orange", "red"):
            errs.append(f"{where}: tier must be yellow, orange or red")
        try:
            a["text"].format(zone="Z", start="S", end="E")
        except (KeyError, IndexError, ValueError, AttributeError):
            errs.append(f"{where}: text may only use {{zone}}, {{start}}, {{end}}")
    if not cfg.get("actions"):
        errs.append("no actions")
    zones = cfg.get("zones") or []
    if require_zones and not zones:
        errs.append("no zones")
    for z in zones if isinstance(zones, list) else []:
        if not isinstance(z, dict) or not {"id", "name", "name_hi", "lat", "lon"} <= set(z):
            errs.append(f"zone {z.get('id', '?') if isinstance(z, dict) else '?'}: needs id, name, name_hi, lat, lon")
        elif not all(type(z[k]) in (int, float) for k in ("lat", "lon")) or not (6 <= z["lat"] <= 37 and 68 <= z["lon"] <= 98):
            errs.append(f"zone {z['id']}: coordinates must be numbers inside India")
    return errs
