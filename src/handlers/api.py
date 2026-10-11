"""Public API + dashboard (Lambda Function URL)."""
from __future__ import annotations

import base64
import json
import os

from heatplan import cap, engine, poster, replay, store, weather
from heatplan.plan import BANDS, list_cities, load_city

WEB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "index.html")


def _resp(body, status=200, ctype="application/json"):
    if ctype == "application/json":
        body = json.dumps(body, ensure_ascii=False)
    return {"statusCode": status, "headers": {"Content-Type": f"{ctype}; charset=utf-8",
                                              "Cache-Control": "no-store"}, "body": body}


def _admin_ok(payload) -> bool:
    token = os.environ.get("ADMIN_TOKEN")
    return bool(token) and payload.get("token") == token


def _city(q):
    c = q.get("city", "delhi")
    if c not in list_cities():
        raise ValueError("unknown city")
    return c


def route(method: str, path: str, q: dict, payload: dict, st=None, agent=None):
    st = st or store.get_store()
    if method == "GET" and path in ("/", "/index.html"):
        with open(WEB, encoding="utf-8") as fh:
            return _resp(fh.read(), ctype="text/html")

    if method == "GET" and path == "/api/cities":
        out = []
        for c in list_cities():
            cfg = load_city(c)
            out.append({k: cfg[k] for k in ("id", "name", "plan_name", "tiers", "zones", "roles", "notes",
                                            "replay", "reference_event", "hot_night_c")})
        return _resp({"cities": out, "bands": BANDS})

    if method == "GET" and path == "/api/state":
        city, mode = _city(q), q.get("mode", "live")
        snap = store.load_snapshot(st, city, mode, q.get("date"))
        if not snap:
            return _resp({"snapshot": None, "actions": [], "reports": []})
        actions = store.list_actions(st, city, mode, snap["date"])
        reports = store.list_reports(st, city, snap["date"] if mode == "live" else engine.today_ist())
        return _resp({"snapshot": snap, "actions": actions, "reports": reports})

    if method == "GET" and path == "/api/backtest":
        try:
            return _resp(replay.backtest(st, _city(q)))
        except weather.WeatherError as e:
            print(f"backtest: {e}")
            return _resp({"error": "historical weather is not cached yet and Open-Meteo is rate-limiting; try again later"}, 503)

    if method == "GET" and path == "/cap.xml":
        city, mode = _city(q), q.get("mode", "live")
        snap = store.load_snapshot(st, city, mode, q.get("date"))
        if not snap:
            return _resp({"error": "no snapshot yet"}, 404)
        return _resp(cap.build(snap, mode), ctype="application/xml")

    if method == "GET" and path == "/poster":
        city, mode = _city(q), q.get("mode", "live")
        snap = store.load_snapshot(st, city, mode, q.get("date"))
        if not snap:
            return _resp("<p>No data yet.</p>", 404, "text/html")
        return _resp(poster.build(snap, q.get("zone", "")), ctype="text/html")

    if method == "POST" and path == "/api/replay":
        if not _admin_ok(payload):
            return _resp({"error": "admin token required"}, 403)
        city = _city(payload)
        day = payload.get("date") or load_city(city)["replay"]["default"]
        win = load_city(city)["replay"]
        if not win["start"] <= day <= win["end"]:
            return _resp({"error": f"date must be within {win['start']}..{win['end']}"}, 400)
        try:
            series = replay.archive_series(st, city)
        except weather.WeatherError as e:
            print(f"replay: {e}")
            return _resp({"error": "historical weather unavailable (bundle it with scripts/fetch_replay_data.py)"}, 503)
        engine.reset_day(st, city, "replay", day)
        snap = engine.run(city, "replay", day, series, st, notify=bool(payload.get("notify", True)))
        return _resp({"snapshot": snap})

    if method == "POST" and path == "/api/tick":
        if not _admin_ok(payload):
            return _resp({"error": "admin token required"}, 403)
        from handlers.tick import run_city
        return _resp(run_city(_city(payload), st, notify=bool(payload.get("notify", True))))

    if method == "POST" and path == "/api/ask":
        question = (payload.get("question") or "").strip()[:500]
        if not question:
            return _resp({"error": "question required"}, 400)
        if not agent:
            return _resp({"error": "assistant not configured"}, 503)
        return _resp({"answer": agent(question, _city(payload))})

    return _resp({"error": "not found"}, 404)


def handler(event, context):
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", "GET"), event.get("rawPath", "/")
    q = event.get("queryStringParameters") or {}
    payload = {}
    if event.get("body"):
        body = base64.b64decode(event["body"]).decode() if event.get("isBase64Encoded") else event["body"]
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return _resp({"error": "invalid json"}, 400)
    from handlers.bot import _invoke_agent
    try:
        return route(method, path, q, payload, agent=_invoke_agent if os.environ.get("AGENT_FUNCTION") else None)
    except ValueError as e:
        return _resp({"error": str(e)}, 400)
    except Exception as e:
        print(f"api error: {e!r}")
        return _resp({"error": "internal error"}, 500)
