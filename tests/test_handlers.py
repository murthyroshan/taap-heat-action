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
    return engine.run("delhi", "live", engine.today_ist(), series, st, notify=False)


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
    Bot(st, tg).handle(_cb(77, data, "Vikram"))
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
    st.put("CACHE", f"archive#delhi#{win['start']}#{win['end']}", weather.serialise(series))
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
