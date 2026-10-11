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
    assert {c["id"] for c in cities["cities"]} == {"delhi", "ahmedabad"}
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
