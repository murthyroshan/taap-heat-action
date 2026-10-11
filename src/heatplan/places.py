"""Where can a worker actually get water, shade or care - and is the walk itself safe?

Help points come from OpenStreetMap (bundled, ODbL) plus places officers confirmed
when marking a plan action done (a cooling centre or water point they opened).
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache

from . import store

PLACES_DIR = os.path.join(os.path.dirname(__file__), "places")
WALK_M_PER_MIN = 70  # a slow walk in heat
# Longest walk worth taking to reach help, by worker band (minutes in the sun).
MAX_WALK_MIN = {0: 30, 1: 20, 2: 12, 3: 8, 4: 5}
KINDS = ("water", "cool", "health")
LOCATED_ACTIONS = {"water": "water", "cool": "cool", "mobile": "health"}


@lru_cache(maxsize=None)
def bundled(city_id: str) -> tuple:
    path = os.path.join(PLACES_DIR, f"{city_id}.json")
    if not os.path.exists(path):
        return ()
    with open(path, encoding="utf-8") as fh:
        return tuple(json.load(fh)["places"])


def distance_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def official(st, city_id: str, mode: str, date: str) -> list[dict]:
    """Places officers confirmed today (plan actions marked done with a location)."""
    out = []
    for a in store.list_actions(st, city_id, mode, date):
        place = a.get("place")
        if a.get("status") == "done" and place and a.get("action_id") in LOCATED_ACTIONS:
            out.append({"k": LOCATED_ACTIONS[a["action_id"]], "n": place.get("label") or a["text_rendered"][:60],
                        "s": "official", "lat": place["lat"], "lon": place["lon"], "official": True,
                        "by": a.get("done_by"), "at": a.get("done_at")})
    return out


def nearby(st, city_id: str, lat: float, lon: float, band: int, mode: str = "live", date: str = "",
           per_kind: int = 3) -> dict:
    limit = MAX_WALK_MIN.get(band, 12)
    pool = official(st, city_id, mode, date) + list(bundled(city_id))
    result = {}
    for kind in KINDS:
        cands = []
        for p in pool:
            if p["k"] != kind:
                continue
            d = distance_m(lat, lon, p["lat"], p["lon"])
            cands.append({**p, "dist_m": round(d), "walk_min": max(1, round(d / WALK_M_PER_MIN)),
                          "directions": f"https://www.google.com/maps/dir/?api=1&destination={p['lat']},{p['lon']}&travelmode=walking"})
        # confirmed official places first when they are reasonably close
        cands.sort(key=lambda c: (not c.get("official") or c["walk_min"] > limit * 2, c["dist_m"]))
        top = cands[:per_kind]
        for c in top:
            c["too_far"] = c["walk_min"] > limit
        result[kind] = top
    return {"band": band, "max_walk_min": limit, "places": result,
            "source": "OpenStreetMap contributors (ODbL) + officer-confirmed places"}


def nearest_zone(city: dict, lat: float, lon: float) -> dict:
    return min(city["zones"], key=lambda z: distance_m(lat, lon, z["lat"], z["lon"]))
