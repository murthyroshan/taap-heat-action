"""Fetch help points (water, health, shade/cooling) from OpenStreetMap and bundle them.

    python scripts/fetch_places.py            # all cities
Data (c) OpenStreetMap contributors, ODbL. Re-run to refresh.
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
from heatplan.plan import list_cities, load_city  # noqa: E402

MIRRORS = ["https://maps.mail.ru/osm/tools/overpass/api/interpreter",
           "https://overpass-api.de/api/interpreter",
           "https://overpass.kumi.systems/api/interpreter"]
QUERY = """[out:json][timeout:90];
(
  nwr["amenity"~"^(drinking_water|water_point|hospital|clinic|shelter)$"]({bbox});
  nwr["leisure"="park"]["name"]({bbox});
);
out center tags;"""


def kind(tags):
    a = tags.get("amenity")
    if a in ("drinking_water", "water_point"):
        return "water"
    if a in ("hospital", "clinic"):
        return "health"
    return "cool"  # named parks and shelters


def _query(bbox):
    body = urllib.parse.urlencode({"data": QUERY.replace("{bbox}", bbox)}).encode()
    for attempt in range(4):
        if attempt:
            time.sleep(4 * attempt)
        for url in (MIRRORS if attempt == 3 else MIRRORS[:1]):
            try:
                req = urllib.request.Request(url, data=body, headers={"User-Agent": "taap-heat-plan/1.0"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    return json.loads(r.read())["elements"]
            except Exception as e:
                print(f"  {url}: {e}")
    raise RuntimeError("all Overpass mirrors failed")


def fetch(city):
    """Query in 3x3 tiles so big cities don't time out."""
    lats = [z["lat"] for z in city["zones"]]
    lons = [z["lon"] for z in city["zones"]]
    s, w, n, e = min(lats) - .06, min(lons) - .06, max(lats) + .06, max(lons) + .06
    dl, dn = (n - s) / 3, (e - w) / 3
    seen, out = set(), []
    tiles = [(s + i * dl, w + j * dn, s + (i + 1) * dl, w + (j + 1) * dn) for i in range(3) for j in range(3)]
    failed = 0
    for a, b, c, d in tiles:
        try:
            elements = _query(f"{a:.4f},{b:.4f},{c:.4f},{d:.4f}")
        except RuntimeError:
            failed += 1
            continue
        for el in elements:
            key = (el["type"], el["id"])
            if key not in seen:
                seen.add(key)
                out.append(el)
    if failed == len(tiles):
        raise RuntimeError("every tile failed")
    if failed:
        print(f"  {failed}/{len(tiles)} tiles failed; saved what we got (re-run to complete)")
    return out


for cid in sys.argv[1:] or list_cities():
    city = load_city(cid)
    places = []
    try:
        elements = fetch(city)
    except RuntimeError as e:
        print(f"{cid}: {e}")
        continue
    for el in elements:
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None:
            continue
        t = el.get("tags", {})
        k = kind(t)
        name = t.get("name:en") or t.get("name") or {"water": "Drinking water", "health": "Clinic", "cool": "Shelter"}[k]
        sub = t.get("amenity") or t.get("leisure")
        places.append({"k": k, "n": name[:60], "s": sub, "lat": round(lat, 5), "lon": round(lon, 5)})
    out = os.path.join(ROOT, "src", "heatplan", "places", f"{cid}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"source": "OpenStreetMap contributors (ODbL)", "places": places}, fh, ensure_ascii=False, separators=(",", ":"))
    counts = {k: sum(1 for p in places if p["k"] == k) for k in ("water", "health", "cool")}
    print(f"{cid}: {counts} -> {os.path.getsize(out) // 1024} KB")
