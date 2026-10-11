"""Open-Meteo client (forecast + historical archive). Data: CC-BY 4.0, open-meteo.com."""
from __future__ import annotations

import base64
import json
import os
import zlib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# FORECAST_URL can point at a relay with the same API (see src/web/api/forecast.js) when the
# host's shared IP is over Open-Meteo's free daily limit.
FORECAST_URL = os.environ.get("FORECAST_URL") or "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
VARS = "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation,direct_radiation,surface_pressure"


class WeatherError(RuntimeError):
    pass


def _get(url: str, params: dict) -> list:
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "taap-heat-plan/1.0"})
    data = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            raise WeatherError(f"Open-Meteo HTTP {e.code}: {e.read()[:200]!r}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 1:
                raise WeatherError(f"Open-Meteo unreachable: {e}") from e
    if isinstance(data, dict) and data.get("error"):
        raise WeatherError(f"Open-Meteo: {data.get('reason')}")
    return data if isinstance(data, list) else [data]


def parse(payload: list, zones: list[dict]) -> dict:
    """Open-Meteo multi-location response -> zone_id -> list of hourly dicts (UTC)."""
    out = {}
    for zone, loc in zip(zones, payload):
        h = loc["hourly"]
        rows = []
        for i, t in enumerate(h["time"]):
            rows.append({
                "time": datetime.fromisoformat(t).replace(tzinfo=timezone.utc),
                "temp": h["temperature_2m"][i],
                "rh": h["relative_humidity_2m"][i],
                "wind": h["wind_speed_10m"][i],
                "sw": h["shortwave_radiation"][i],
                "direct": h["direct_radiation"][i],
                "p": h["surface_pressure"][i],
            })
        out[zone["id"]] = rows
    return out


def _coords(zones):
    return {
        "latitude": ",".join(str(z["lat"]) for z in zones),
        "longitude": ",".join(str(z["lon"]) for z in zones),
    }


def fetch_forecast_raw(zones: list[dict]) -> list:
    params = {**_coords(zones), "hourly": VARS, "timezone": "GMT", "past_days": 1, "forecast_days": 3}
    return _get(FORECAST_URL, params)


def fetch_archive_raw(zones: list[dict], start: str, end: str) -> list:
    params = {**_coords(zones), "hourly": VARS, "timezone": "GMT", "start_date": start, "end_date": end}
    return _get(ARCHIVE_URL, params)


def serialise(series: dict) -> dict:
    return {z: [{**r, "time": r["time"].isoformat()} for r in rows] for z, rows in series.items()}


def deserialise(data: dict) -> dict:
    return {z: [{**r, "time": datetime.fromisoformat(r["time"])} for r in rows] for z, rows in data.items()}


FIELDS = ("temp", "rh", "wind", "sw", "direct", "p")


def pack(series: dict) -> str:
    """Compact, compressed form for caching (a month of hourly data for 14 zones is ~150 KB)."""
    out = {}
    for z, rows in series.items():
        out[z] = {"t0": rows[0]["time"].isoformat() if rows else None,
                  **{f: [None if r[f] is None else round(r[f], 1) for r in rows] for f in FIELDS}}
    raw = json.dumps(out, separators=(",", ":")).encode()
    return base64.b64encode(zlib.compress(raw, 9)).decode()


def unpack(blob: str) -> dict:
    from datetime import timedelta
    data = json.loads(zlib.decompress(base64.b64decode(blob)))
    series = {}
    for z, cols in data.items():
        t0 = datetime.fromisoformat(cols["t0"]) if cols["t0"] else None
        n = len(cols["temp"])
        series[z] = [{"time": t0 + timedelta(hours=i), **{f: cols[f][i] for f in FIELDS}} for i in range(n)]
    return series
