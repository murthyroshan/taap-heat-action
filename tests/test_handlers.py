import json
import os

from helpers import FakeTelegram, synthetic_day

from handlers import api
from handlers.bot import Bot
from heatplan import engine, plan, store


def _msg(chat, text, name="Ravi"):
    return {"message": {"chat": {"id": chat}, "from": {"first_name": name}, "text": text}}


def _cb(chat, data, name="Ravi"):
    return {"callback_query": {"id": "cb1", "data": data, "from": {"first_name": name},
                               "message": {"chat": {"id": chat}, "message_id": 5}}}


def _live(st):
    city = plan.load_city("delhi")
    series = {z["id"]: synthetic_day(day=engine.today_ist(), tmax=46, tmin=31) for z in city["zones"]}
    return engine.run("delhi", "live", engine.today_ist(), series, st, notify=False, now=_noon_today())


def _noon_today():
    from datetime import datetime, timezone
    return datetime.fromisoformat(engine.today_ist() + "T07:00:00").replace(tzinfo=timezone.utc)


def _officer(st, chat, role="all", city="delhi", name="Vikram"):
    store.save_user(st, chat, {"role": "officer", "city": city, "officer_role": role, "name": name})


def test_worker_onboarding_status_and_report(monkeypatch):
    monkeypatch.setenv("OFFICER_CODE", "heat123")
    st, tg = store.MemoryStore(), FakeTelegram()
    bot = Bot(st, tg)
    _live(st)
    # officer joins
    bot.handle(_msg(900, "/officer heat123", "Asha"))
    bot.handle(_cb(900, "oc|delhi", "Asha"))
    bot.handle(_cb(900, "or|water", "Asha"))
    assert store.get_user(st, 900)["officer_role"] == "water"
    # worker joins in Hindi
    for upd in (_msg(1, "/start"), _cb(1, "l|hi"), _cb(1, "c|delhi"), _cb(1, "z|narela")):
        bot.handle(upd)
    u = store.get_user(st, 1)
    assert u == {**u, "role": "worker", "city": "delhi", "zone": "narela", "lang": "hi"}
    assert any("नरेला" in m[2] for m in tg.sent if m[0] == "send")
    # worker reports no water -> goes to the water officer with a Resolved button
    tg.sent.clear()
    bot.handle(_msg(1, "🚱 साइट पर पीने का पानी नहीं"))
    to_officer = [m for m in tg.sent if m[1] == "900"]
    assert to_officer and "No drinking water" in to_officer[0][2]
    cb = to_officer[0][3][0][0]["callback_data"]
    bot.handle(_cb(900, cb, "Asha"))
    rep = store.list_reports(st, "delhi", engine.today_ist())[0]
    assert rep["status"] == "resolved" and rep["resolved_by"] == "Asha"


def test_wrong_officer_code_rejected(monkeypatch):
    monkeypatch.setenv("OFFICER_CODE", "heat123")
    st, tg = store.MemoryStore(), FakeTelegram()
    Bot(st, tg).handle(_msg(5, "/officer nope"))
    assert store.get_user(st, 5) is None


def test_done_button_updates_public_log(monkeypatch):
    st, tg = store.MemoryStore(), FakeTelegram()
    _live(st)
    act = store.list_actions(st, "delhi", "live", engine.today_ist())[0]
    data = f"d|delhi|live|{engine.today_ist()}|{act['zone']}|{act['action_id']}"
    _officer(st, 77)
    Bot(st, tg).handle(_cb(77, data, "Vikram"))
    assert any(m[0] == "buttons" for m in tg.sent)
    res = api.route("GET", "/api/state", {"city": "delhi"}, {}, st)
    body = json.loads(res["body"])
    done = [a for a in body["actions"] if a["status"] == "done"]
    assert len(done) == 1 and done[0]["done_by"] == "Vikram"


def test_api_routes(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "t0k")
    st = store.MemoryStore()
    _live(st)
    assert api.route("GET", "/", {}, {}, st)["headers"]["Content-Type"].startswith("text/html")
    cities = json.loads(api.route("GET", "/api/cities", {}, {}, st)["body"])
    assert {"delhi", "ahmedabad", "mumbai", "hyderabad", "chennai", "kolkata", "jaipur"} <= {c["id"] for c in cities["cities"]}
    assert "<alert" in api.route("GET", "/cap.xml", {"city": "delhi"}, {}, st)["body"]
    assert "गर्मी सूचना" in api.route("GET", "/poster", {"city": "delhi", "zone": "okhla"}, {}, st)["body"]
    assert api.route("POST", "/api/replay", {}, {"city": "delhi"}, st)["statusCode"] == 403
    import pytest
    with pytest.raises(ValueError):
        api.route("GET", "/api/state", {"city": "nowhere"}, {}, st)
    assert api.route("GET", "/nope", {}, {}, st)["statusCode"] == 404


def test_replay_endpoint_uses_cached_archive(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "t0k")
    st = store.MemoryStore()
    city = plan.load_city("delhi")
    series = {z["id"]: synthetic_day(day="2024-05-28", tmax=47.5, tmin=34) for z in city["zones"]}
    from heatplan import replay, weather
    win = city["replay"]
    st.put("CACHE", f"archive#delhi#{win['start']}#{win['end']}", {"blob": weather.pack(series)})
    monkeypatch.setattr(replay, "FIXTURE_DIR", "/nonexistent")
    res = api.route("POST", "/api/replay", {}, {"city": "delhi", "date": "2024-05-28", "token": "t0k", "notify": False}, st)
    snap = json.loads(res["body"])["snapshot"]
    assert snap["mode"] == "replay" and snap["tier"] == "red"
    bad = api.route("POST", "/api/replay", {}, {"city": "delhi", "date": "2025-01-01", "token": "t0k"}, st)
    assert bad["statusCode"] == 400


def test_agent_tools_read_real_state():
    from handlers.agent import _tools
    st = store.MemoryStore()
    _live(st)
    tools = {t.tool_name: t for t in _tools(st)}
    risk = json.loads(tools["zone_risk"](city="delhi"))
    assert risk["city_tier"] in ("orange", "red") and len(risk["zones"]) == 14
    open_acts = json.loads(tools["plan_actions"](city="delhi", status="open"))
    assert open_acts and all(a["status"] == "open" for a in open_acts)
    assert "tiers" in json.loads(tools["plan_text"](city="ahmedabad"))


def test_forged_callbacks_are_rejected():
    st, tg = store.MemoryStore(), FakeTelegram()
    _live(st)
    bot = Bot(st, tg)
    # a stranger tries to become nodal officer without the code
    bot.handle(_cb(66, "oc|delhi"))
    bot.handle(_cb(66, "or|all"))
    assert store.get_user(st, 66) is None
    # a worker tries to mark an action done / resolve a report
    store.save_user(st, 67, {"role": "worker", "city": "delhi", "zone": "narela", "lang": "en"})
    act = store.list_actions(st, "delhi", "live", engine.today_ist())[0]
    bot.handle(_cb(67, f"d|delhi|live|{engine.today_ist()}|{act['zone']}|{act['action_id']}"))
    # an officer of another department / city cannot either
    _officer(st, 68, role="health")
    other = next(a for a in store.list_actions(st, "delhi", "live", engine.today_ist()) if a["role"] != "health")
    bot.handle(_cb(68, f"d|delhi|live|{engine.today_ist()}|{other['zone']}|{other['action_id']}"))
    _officer(st, 69, city="ahmedabad")
    bot.handle(_cb(69, f"d|delhi|live|{engine.today_ist()}|{act['zone']}|{act['action_id']}"))
    assert all(a["status"] == "open" for a in store.list_actions(st, "delhi", "live", engine.today_ist()))
    # malformed data never crashes
    for data in ("", "d|x", "z|nowhere", "c|atlantis", "l|fr", "r|delhi|2026-01-01|nope", "x"):
        bot.handle(_cb(70, data))


def test_reports_are_rate_limited_and_stale_buttons_restart():
    st, tg = store.MemoryStore(), FakeTelegram()
    bot = Bot(st, tg)
    store.save_user(st, 5, {"role": "worker", "city": "delhi", "zone": "narela", "lang": "en"})
    bot.handle(_msg(5, "🚱 No drinking water at site"))
    bot.handle(_msg(5, "🚱 No drinking water at site"))
    assert len(store.list_reports(st, "delhi", engine.today_ist())) == 1
    # a zone button pressed after /stop asks to restart instead of crashing
    st.delete("USER", "5")
    tg.sent.clear()
    bot.handle(_cb(5, "z|narela"))
    assert "/start" in tg.sent[-1][2]


def test_failed_history_fetch_backs_off(monkeypatch):
    from heatplan import replay, weather
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise weather.WeatherError("429")
    monkeypatch.setattr(replay, "FIXTURE_DIR", "/nonexistent")
    monkeypatch.setattr(weather, "fetch_archive_raw", boom)
    st = store.MemoryStore()
    for _ in range(3):
        res = api.route("GET", "/api/backtest", {"city": "delhi"}, {}, st)
        assert res["statusCode"] == 503
    assert len(calls) == 1


def test_webhook_requires_the_secret(monkeypatch):
    from handlers import bot
    called = []
    monkeypatch.setattr(bot.Bot, "handle", lambda self, u: called.append(u))
    monkeypatch.setattr(bot.store, "get_store", lambda: store.MemoryStore())
    ev = {"headers": {}, "body": json.dumps(_msg(1, "/stop"))}
    monkeypatch.delenv("TELEGRAM_SECRET", raising=False)
    assert bot.handler(ev, None)["statusCode"] == 403  # unset secret = closed, not open
    monkeypatch.setenv("TELEGRAM_SECRET", "s3cret-s3cret-s3cret")
    assert bot.handler(ev, None)["statusCode"] == 403
    ev["headers"] = {"X-Telegram-Bot-Api-Secret-Token": "s3cret-s3cret-s3cret"}
    assert bot.handler(ev, None)["statusCode"] == 200 and called


def test_old_city_button_resets_zone_and_reports_survive():
    st, tg = store.MemoryStore(), FakeTelegram()
    bot = Bot(st, tg)
    store.save_user(st, 8, {"role": "worker", "city": "delhi", "zone": "narela", "lang": "en"})
    bot.handle(_cb(8, "c|ahmedabad"))
    u = store.get_user(st, 8)
    assert u["role"] == "worker_pending" and "zone" not in u
    store.save_user(st, 9, {"role": "worker", "city": "ahmedabad", "zone": "narela", "lang": "en"})
    bot.handle(_msg(9, "🚱 No drinking water at site"))  # zone not in city: restart, no crash
    assert "/start" in tg.sent[-1][2]


def test_officer_ask_runs_async():
    st, tg = store.MemoryStore(), FakeTelegram()
    calls = []
    _officer(st, 10)
    Bot(st, tg, agent=lambda q, c, chat=None: calls.append((q, c, chat))).handle(_msg(10, "/ask open actions?"))
    assert calls == [("open actions?", "delhi", 10)]


def test_public_ask_is_rate_limited_and_replay_date_validated(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "t0k")
    st = store.MemoryStore()
    codes = [api.route("POST", "/api/ask", {}, {"question": "hi", "city": "delhi"}, st,
                       agent=lambda q, c: "ok")["statusCode"] for _ in range(api.ASK_PER_MINUTE + 2)]
    assert codes.count(200) == api.ASK_PER_MINUTE and codes[-1] == 429
    for bad in ("2024-05-2", "yesterday", 12345, "2024-13-01"):
        res = api.route("POST", "/api/replay", {}, {"city": "delhi", "date": bad, "token": "t0k"}, st)
        assert res["statusCode"] == 400


def test_cap_identifier_changes_every_run():
    from heatplan import cap
    st = store.MemoryStore()
    snap = _live(st)
    a = cap.build({**snap, "generated_at": "2026-10-11T07:05:00+00:00"}, "live")
    b = cap.build({**snap, "generated_at": "2026-10-11T08:05:00+00:00"}, "live")
    ident = lambda x: x.split("<identifier>")[1].split("<")[0]
    assert ident(a) != ident(b)


def _fake_bedrock(monkeypatch, text):
    import boto3

    class Fake:
        def converse(self, **kw):
            doc = kw["messages"][0]["content"][0]["document"]
            assert doc["format"] == "pdf" and doc["source"]["bytes"].startswith(b"%PDF")
            return {"output": {"message": {"content": [{"text": text}]}}}
    monkeypatch.setattr(boto3, "client", lambda *a, **k: Fake())


def test_plan_import_drafts_and_validates(monkeypatch):
    from handlers import agent
    good = {"id": "jaipur", "name": "Jaipur", "name_hi": "जयपुर", "plan_name": "Jaipur HAP",
            "tiers": {"yellow": 41, "orange": 43, "red": 45}, "hot_night_c": 31,
            "roles": {"health": "Health"},
            "actions": [{"id": "hosp", "role": "health", "when": {"tier": "orange"},
                         "text": "Alert hospitals in {zone}", "ref": "p.12"}]}
    _fake_bedrock(monkeypatch, "Here you go:\n```json\n" + json.dumps(good, ensure_ascii=False) + "\n```")
    out = agent.import_plan(b"%PDF-1.4 fake")
    assert out["problems"] == [] and out["draft"]["zones"] == []
    assert any("DRAFT" in n for n in out["draft"]["notes"])
    _fake_bedrock(monkeypatch, "Sorry, I cannot read this document.")
    assert "error" in agent.import_plan(b"%PDF-1.4 fake")
    _fake_bedrock(monkeypatch, '{"id": "x", "tiers": {"yellow": 50, "orange": 40, "red": 45}}')
    assert agent.import_plan(b"%PDF-1.4 fake")["problems"]


def test_plan_import_route_guards(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "t0k")
    st = store.MemoryStore()
    imp = lambda b64: {"draft": {"id": "x"}, "problems": []}
    assert api.route("POST", "/api/plan-import", {}, {"pdf_b64": "JVBE"}, st, importer=imp)["statusCode"] == 403
    huge = "A" * (api.MAX_PDF_B64 + 10)
    assert api.route("POST", "/api/plan-import", {}, {"token": "t0k", "pdf_b64": huge}, st, importer=imp)["statusCode"] == 400
    ok = api.route("POST", "/api/plan-import", {}, {"token": "t0k", "pdf_b64": "JVBE"}, st, importer=imp)
    assert ok["statusCode"] == 200 and json.loads(ok["body"])["draft"]["id"] == "x"


def test_model_provider_selection(monkeypatch):
    from handlers import agent
    monkeypatch.setenv("MODEL_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    m = agent.make_model()
    assert type(m).__name__ == "OpenAIModel" and m.config["model_id"] == "openai/gpt-oss-120b"
    monkeypatch.setenv("MODEL_PROVIDER", "ollama")
    assert type(agent.make_model()).__name__ == "OllamaModel"


def test_pages_and_assets_are_served():
    st = store.MemoryStore()
    for path in api.PAGES:
        if os.path.exists(os.path.join(api.WEB_DIR, api.PAGES[path] + ".html")):
            assert api.route("GET", path, {}, {}, st)["statusCode"] == 200, path
    css = api.route("GET", "/assets/app.css", {}, {}, st)
    assert css["statusCode"] == 200 and css["headers"]["Content-Type"].startswith("text/css")
    for bad in ("/assets/../handlers/api.py", "/assets/x.py", "/assets/missing.css"):
        assert api.route("GET", bad, {}, {}, st)["statusCode"] == 404


def test_officer_web_console_and_ledger(monkeypatch):
    monkeypatch.setenv("OFFICER_CODE", "heat2026")
    st = store.MemoryStore()
    _live(st)
    act = store.list_actions(st, "delhi", "live", engine.today_ist())[0]
    body = {"city": "delhi", "mode": "live", "date": engine.today_ist(), "zone": act["zone"], "action_id": act["action_id"], "name": "Asha"}
    assert api.route("POST", "/api/officer/done", {}, {**body, "code": "nope"}, st)["statusCode"] == 403
    ok = api.route("POST", "/api/officer/done", {}, {**body, "code": "heat2026"}, st)
    assert ok["statusCode"] == 200 and json.loads(ok["body"])["action"]["done_by"] == "Asha"
    led = json.loads(api.route("GET", "/api/ledger", {"city": "delhi"}, {}, st)["body"])
    role = next(r for r in led["roles"] if r["role"] == act["role"])
    assert role["done"] == 1 and role["total"] >= 1 and led["days"][0]["done"] == 1
    st.put(f"REP#delhi#{engine.today_ist()}", "120000#5", {"type": "water", "zone_name": "Narela", "status": "open"})
    res = api.route("POST", "/api/officer/resolve", {}, {"code": "heat2026", "city": "delhi", "date": engine.today_ist(), "sk": "120000#5"}, st)
    assert json.loads(res["body"])["report"]["status"] == "resolved"


def test_nearby_help_with_walk_times_and_official_places(monkeypatch):
    from heatplan import places
    monkeypatch.setenv("OFFICER_CODE", "heat2026")
    st = store.MemoryStore()
    _live(st)
    fake = ({"k": "water", "n": "Tap A", "s": "drinking_water", "lat": 28.8540, "lon": 77.0935},
            {"k": "water", "n": "Tap far", "s": "drinking_water", "lat": 28.90, "lon": 77.20},
            {"k": "health", "n": "Clinic", "s": "clinic", "lat": 28.86, "lon": 77.10})
    monkeypatch.setattr(places, "bundled", lambda c: fake)
    res = json.loads(api.route("GET", "/api/nearby", {"city": "delhi", "lat": "28.853", "lon": "77.093"}, {}, st)["body"])
    water = res["places"]["water"]
    assert res["zone"]["id"] == "narela" and res["from"] == "you"
    assert water[0]["n"] == "Tap A" and water[0]["walk_min"] <= 2 and not water[0]["too_far"]
    assert water[1]["too_far"] and "google.com/maps/dir" in water[1]["directions"]
    # an officer opens a cooling centre and drops its location -> it shows up for workers
    act = next(a for a in store.list_actions(st, "delhi", "live", engine.today_ist()) if a["action_id"] == "cool" and a["zone"] == "narela")
    api.route("POST", "/api/officer/done", {}, {"code": "heat2026", "city": "delhi", "mode": "live", "date": engine.today_ist(),
              "zone": "narela", "action_id": "cool", "name": "DM North", "lat": 28.8545, "lon": 77.094, "label": "Narela community hall"}, st)
    res = json.loads(api.route("GET", "/api/nearby", {"city": "delhi", "zone": "narela"}, {}, st)["body"])
    assert res["places"]["cool"][0]["official"] and res["places"]["cool"][0]["n"] == "Narela community hall"


def test_shift_planner_fits_work_into_safe_hours():
    st = store.MemoryStore()
    _live(st)
    res = json.loads(api.route("GET", "/api/shift", {"city": "delhi", "zone": "narela", "hours": "6"}, {}, st)["body"])
    assert res["start"] and res["rows"] and res["rights"]["hi"]
    assert sum(r["work"] for r in res["rows"]) == 360 or not res["feasible"]
    assert all(r["work"] <= r["cap"] for r in res["rows"])
    assert res["planned_hot_minutes"] <= res["usual_hot_hours"] * 60
    new = json.loads(api.route("GET", "/api/shift", {"city": "delhi", "zone": "narela", "hours": "6", "new": "1"}, {}, st)["body"])
    assert new["safe_capacity_hours"] <= res["safe_capacity_hours"]


def test_saathi_emergency_never_uses_the_model_and_files_a_report():
    from heatplan import saathi
    st = store.MemoryStore()
    _live(st)
    res = saathi.reply(st, "मेरा साथी बेहोश हो गया", "delhi", "narela", "hi", use_model=True)
    assert res["emergency"] and res["engine"] == "triage" and "112" in res["reply"]
    assert res["cards"][0]["type"] == "emergency"
    assert store.list_reports(st, "delhi", engine.today_ist())[0]["type"] == "ill"


def test_saathi_fallback_answers_and_acts():
    from heatplan import saathi
    st = store.MemoryStore()
    _live(st)
    r = saathi.reply(st, "आज कब काम करूँ?", "delhi", "narela", "hi", use_model=False)
    assert r["engine"] == "fallback" and r["cards"][0]["type"] == "shift" and "बजे" not in r["reply"][:0]
    r = saathi.reply(st, "site par paani nahi hai, no water here", "delhi", "narela", "en", use_model=False, chat_id="c1")
    assert r["cards"][0]["type"] == "report" and "Sent to" in r["reply"]
    again = saathi.reply(st, "no water here", "delhi", "narela", "en", use_model=False, chat_id="c1")
    assert "already" in again["reply"]
    warn = saathi.reply(st, "I feel dizzy", "delhi", "narela", "en", use_model=False)
    assert warn["cards"][0]["type"] == "warning" and "Stop work" in warn["reply"]
    risk = saathi.reply(st, "how hot is it today", "delhi", "narela", "en", use_model=False)
    assert risk["cards"][0]["type"] == "risk"


def test_saathi_endpoint_falls_back_when_agent_breaks(monkeypatch):
    from heatplan import saathi
    st = store.MemoryStore()
    _live(st)
    monkeypatch.setattr(saathi, "_agent_reply", lambda s, t: (_ for _ in ()).throw(RuntimeError("no model")))
    res = api.route("POST", "/api/saathi", {}, {"text": "where is water", "city": "delhi", "zone": "narela", "lang": "en"}, st, agent=lambda q, c: "x")
    body = json.loads(res["body"])
    assert res["statusCode"] == 200 and body["engine"] == "fallback" and body["cards"][0]["type"] == "places"


def test_shift_planner_avoids_hot_hours_when_cool_ones_suffice():
    from heatplan import shift
    hourly = [{"hour": h, "band": 3 if 12 <= h < 15 else 0, "wbgt": 29.5 if 12 <= h < 15 else 22} for h in range(24)]
    res = shift.plan_shift({"available": True, "hourly": hourly, "band": 3}, 8)
    assert res["feasible"] and res["planned_hot_minutes"] == 0 and res["usual_hot_hours"] == 3
