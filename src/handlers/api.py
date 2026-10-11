"""Public API + dashboard (Lambda Function URL)."""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
from datetime import date

from heatplan import cap, engine, messages, poster, replay, store, weather
from heatplan.plan import BANDS, list_cities, load_city

ASK_PER_MINUTE = 10
MAX_PDF_B64 = 4 * 1024 * 1024 * 4 // 3 + 4  # 4 MB PDF; Lambda sync payloads cap at 6 MB


def _import_plan(pdf_b64: str) -> dict:
    import boto3
    from botocore.config import Config
    client = boto3.client("lambda", config=Config(read_timeout=100, retries={"max_attempts": 1}))
    res = client.invoke(FunctionName=os.environ["AGENT_FUNCTION"],
                        Payload=json.dumps({"task": "import_plan", "pdf_b64": pdf_b64}))
    return json.loads(res["Payload"].read() or b"{}")
WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
PAGES = {"/": "index", "/index.html": "index", "/dashboard": "dashboard", "/workers": "workers", "/officers": "officers",
         "/ledger": "ledger", "/replay": "replay", "/plans": "plans", "/method": "method"}
ASSET_TYPES = {".css": "text/css", ".js": "application/javascript", ".svg": "image/svg+xml", ".json": "application/json"}
ASSET_RE = re.compile(r"^/assets/([a-z0-9_-]+\.(?:css|js|svg|json))$")


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


def ledger(st, city: str) -> dict:
    """Plan vs reality across every recorded day: per-department completion and response time."""
    cfg = load_city(city)
    days, rows = [], []
    by_role = {r: {"role": r, "label": label, "total": 0, "done": 0, "minutes": []} for r, label in cfg["roles"].items()}
    for mode in ("live", "replay"):
        for snap in st.query(store.snap_pk(city, mode), newest_first=True, limit=60):
            acts = store.list_actions(st, city, mode, snap["sk"])
            if not acts:
                continue
            done = [a for a in acts if a["status"] == "done"]
            days.append({"date": snap["sk"], "mode": mode, "tier": snap.get("tier"), "max_band": snap.get("max_band"),
                         "total": len(acts), "done": len(done)})
            for a in acts:
                r = by_role.setdefault(a["role"], {"role": a["role"], "label": a["role"], "total": 0, "done": 0, "minutes": []})
                r["total"] += 1
                if a["status"] == "done":
                    r["done"] += 1
                    if a.get("done_at") and a.get("created_at"):
                        r["minutes"].append(max(0, (a["done_at"] - a["created_at"]) / 60))
                rows.append({k: a.get(k) for k in ("date", "mode", "zone_name", "action_id", "role", "status",
                                                    "done_by", "created_at", "done_at", "text_rendered", "ref")})
    roles = []
    for r in by_role.values():
        mins = sorted(r.pop("minutes"))
        r["median_minutes"] = round(mins[len(mins) // 2]) if mins else None
        r["rate"] = round(r["done"] / r["total"] * 100) if r["total"] else None
        roles.append(r)
    return {"city": city, "days": days, "roles": roles, "rows": rows[:1000]}


def route(method: str, path: str, q: dict, payload: dict, st=None, agent=None, importer=None):
    st = st or store.get_store()
    if method == "GET" and path in PAGES:
        with open(os.path.join(WEB_DIR, PAGES[path] + ".html"), encoding="utf-8") as fh:
            return _resp(fh.read(), ctype="text/html")

    m = ASSET_RE.match(path) if method == "GET" else None
    if m:
        full = os.path.join(WEB_DIR, "assets", m.group(1))
        if not os.path.exists(full):
            return _resp({"error": "not found"}, 404)
        with open(full, encoding="utf-8") as fh:
            return _resp(fh.read(), ctype=ASSET_TYPES[os.path.splitext(full)[1]])

    if method == "GET" and path == "/api/meta":
        return _resp({"bot_username": os.environ.get("BOT_USERNAME", ""),
                      "deployment": "aws" if os.environ.get("TABLE_NAME") else "local",
                      "assistant": bool(agent), "officer_console": bool(os.environ.get("OFFICER_CODE"))})

    if method == "GET" and path == "/api/worker":
        city, mode, lang = _city(q), q.get("mode", "live"), ("en" if q.get("lang") == "en" else "hi")
        cfg = load_city(city)
        zone = next((z for z in cfg["zones"] if z["id"] == q.get("zone")), cfg["zones"][0])
        snap = store.load_snapshot(st, city, mode, q.get("date"))
        zr = next((z for z in snap["zones"] if z["zone"] == zone["id"]), None) if snap else None
        if not zr or not zr.get("available"):
            return _resp({"zone": zone, "risk": None, "text": messages.status_text(None, zone, "", lang), "voice": ""})
        if zr["band"] >= 1:
            msg = messages.worker_alert(zr, zone, snap["date"], lang, replay=mode == "replay")
        else:
            t = messages.status_text(zr, zone, snap["date"], lang)
            msg = {"text": t, "voice": t.replace("🟢", "")}
        return _resp({"zone": zone, "date": snap["date"], "risk": zr, "bands": BANDS, **msg})

    if method == "GET" and path == "/api/ledger":
        return _resp(ledger(st, _city(q)))

    if method == "POST" and path in ("/api/officer/done", "/api/officer/resolve"):
        code = os.environ.get("OFFICER_CODE", "")
        if not code or not hmac.compare_digest(str(payload.get("code", "")), code):
            return _resp({"error": "officer code not recognised"}, 403)
        name = (str(payload.get("name") or "").strip() or "Officer (web)")[:40]
        city = _city(payload)
        if path.endswith("/done"):
            item = engine.mark_done(st, city, str(payload.get("mode", "live")), str(payload.get("date", "")),
                                    str(payload.get("zone", "")), str(payload.get("action_id", "")), name)
            return _resp({"action": item} if item else {"error": "no such action"}, 200 if item else 404)
        pk, sk = f"REP#{city}#{payload.get('date', '')}", str(payload.get("sk", ""))
        rep = st.get(pk, sk)
        if not rep:
            return _resp({"error": "no such report"}, 404)
        if rep.get("status") != "resolved":
            rep.update(status="resolved", resolved_by=name, resolved_at=store.now_ts())
            st.put(pk, sk, rep)
        return _resp({"report": rep})

    if method == "GET" and path == "/api/cities":
        out = []
        for c in list_cities():
            cfg = load_city(c)
            out.append({k: cfg.get(k) for k in ("id", "name", "name_hi", "plan_name", "tiers", "zones", "roles", "notes",
                                                "replay", "reference_event", "hot_night_c", "actions",
                                                "alert_min_band", "actions_min_tier")})
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
        win = load_city(city)["replay"]
        try:
            day = date.fromisoformat(str(payload.get("date") or win["default"])).isoformat()
        except ValueError:
            return _resp({"error": "date must be YYYY-MM-DD"}, 400)
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

    if method == "POST" and path == "/api/plan-import":
        if not _admin_ok(payload):
            return _resp({"error": "admin token required"}, 403)
        pdf_b64 = payload.get("pdf_b64") or ""
        if not pdf_b64 or len(pdf_b64) > MAX_PDF_B64:
            return _resp({"error": "send a PDF under 4 MB as base64 in pdf_b64"}, 400)
        if not importer:
            return _resp({"error": "plan import not configured"}, 503)
        return _resp(importer(pdf_b64))

    if method == "POST" and path == "/api/ask":
        question = (payload.get("question") or "").strip()[:500]
        if not question:
            return _resp({"error": "question required"}, 400)
        if not agent:
            return _resp({"error": "assistant not configured"}, 503)
        minute = store.now_ts() // 60
        used = (st.get("RATE", f"ask#{minute}") or {"n": 0})["n"]
        if used >= ASK_PER_MINUTE:  # public endpoint backed by paid model calls
            return _resp({"error": "the assistant is busy; try again in a minute"}, 429)
        st.put("RATE", f"ask#{minute}", {"n": used + 1})
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
        has_agent = bool(os.environ.get("AGENT_FUNCTION"))
        return route(method, path, q, payload, agent=_invoke_agent if has_agent else None,
                     importer=_import_plan if has_agent else None)
    except ValueError as e:
        return _resp({"error": str(e)}, 400)
    except Exception as e:
        print(f"api error: {e!r}")
        return _resp({"error": "internal error"}, 500)
