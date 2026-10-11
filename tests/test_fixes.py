"""Regressions for the issues found in the pre-submission review."""
import base64
import json
import os
import re

from helpers import FakeTelegram, synthetic_day

from handlers import api
from handlers.bot import OFFICER_CODE_TRIES, Bot
from heatplan import engine, plan, saathi, store


def _msg(chat, text, name="Ravi"):
    return {"message": {"chat": {"id": chat}, "from": {"first_name": name}, "text": text}}


def _cb(chat, data, name="Ravi"):
    return {"callback_query": {"id": "cb1", "data": data, "from": {"first_name": name},
                               "message": {"chat": {"id": chat}, "message_id": 5}}}


def _live(st):
    from datetime import datetime, timezone
    city = plan.load_city("delhi")
    series = {z["id"]: synthetic_day(day=engine.today_ist(), tmax=46, tmin=31) for z in city["zones"]}
    noon = datetime.fromisoformat(engine.today_ist() + "T07:00:00").replace(tzinfo=timezone.utc)
    return engine.run("delhi", "live", engine.today_ist(), series, st, notify=False, now=noon)


def _officer(st, chat, role="all"):
    store.save_user(st, chat, {"role": "officer", "city": "delhi", "officer_role": role, "name": "Vikram"})


def _replay_cache(st, monkeypatch):
    from heatplan import replay, weather
    city = plan.load_city("delhi")
    win = city["replay"]
    series = {z["id"]: synthetic_day(day="2024-05-28", tmax=47.5, tmin=34) for z in city["zones"]}
    st.put("CACHE", f"archive#delhi#{win['start']}#{win['end']}", {"blob": weather.pack(series)})
    monkeypatch.setattr(replay, "FIXTURE_DIR", "/nonexistent")


# ---- Saathi reports ----------------------------------------------------------
def test_saathi_reports_reach_the_officer_and_can_be_resolved():
    st, tg = store.MemoryStore(), FakeTelegram()
    _live(st)
    _officer(st, 900, role="water")
    _officer(st, 901, role="health")
    r = saathi.reply(st, "no water at my site", "delhi", "narela", "en", use_model=False,
                     chat_id="x" * 40, notifier=engine.Notifier(st, tg))  # longest session id the API allows
    assert "Sent to" in r["reply"]
    sent = [s for s in tg.sent if s[0] == "send" and s[1] == "900"]
    assert len(sent) == 1 and "Narela" in sent[0][2] and not [s for s in tg.sent if s[1] == "901"]
    data = sent[0][3][0][0]["callback_data"]
    assert len(data.encode()) <= 64  # Telegram's limit for button data
    Bot(st, tg).handle(_cb(900, data))
    assert store.list_reports(st, "delhi", engine.today_ist())[0]["status"] == "resolved"
    # the emergency path (fixed rules, never the model) reaches the health officer too
    saathi.reply(st, "my friend fainted", "delhi", "narela", "en", chat_id="s2", notifier=engine.Notifier(st, tg))
    assert [s for s in tg.sent if s[0] == "send" and s[1] == "901"]


def test_saathi_web_reports_are_capped_city_wide():
    st = store.MemoryStore()
    _live(st)
    replies = [saathi.reply(st, "no shade here", "delhi", "narela", "en", use_model=False, chat_id=f"s{i}")["reply"]
               for i in range(saathi.WEB_REPORTS_PER_MINUTE + 1)]
    assert all("Sent to" in r for r in replies[:-1]) and "Too many" in replies[-1]
    assert len(store.list_reports(st, "delhi", engine.today_ist())) == saathi.WEB_REPORTS_PER_MINUTE


def test_saathi_model_runs_remotely_on_aws_with_fallback():
    st = store.MemoryStore()
    _live(st)
    calls = []
    remote = lambda **kw: calls.append(kw) or {"reply": "remote", "cards": [], "engine": "agent"}  # noqa: E731
    body = {"text": "where is water", "city": "delhi", "zone": "narela", "lang": "en"}
    res = api.route("POST", "/api/saathi", {}, body, st, agent=lambda q, c: "x", saathi_remote=remote)
    assert json.loads(res["body"])["reply"] == "remote" and calls[0]["zone"] == "narela"

    def broken(**kw):
        raise RuntimeError("down")
    res = api.route("POST", "/api/saathi", {}, body, st, agent=lambda q, c: "x", saathi_remote=broken)
    assert json.loads(res["body"])["engine"] == "fallback"
    res = api.route("POST", "/api/saathi", {}, {**body, "text": "he fainted"}, st, agent=lambda q, c: "x",
                    saathi_remote=remote)
    assert json.loads(res["body"])["engine"] == "triage" and len(calls) == 1  # emergencies stay on fixed rules


# ---- ledger --------------------------------------------------------------------
def test_browsing_replay_days_stays_out_of_the_ledger(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "t0k")
    monkeypatch.setenv("OFFICER_CODE", "heat2026")
    st = store.MemoryStore()
    _replay_cache(st, monkeypatch)

    def ledger_days(s):
        return [d["date"] for d in json.loads(api.route("GET", "/api/ledger", {"city": "delhi"}, {}, s)["body"])["days"]]

    state = api.route("GET", "/api/state", {"city": "delhi", "mode": "replay", "date": "2024-05-28"}, {}, st)
    assert json.loads(state["body"])["actions"]  # the replay day still shows its actions...
    assert ledger_days(st) == []  # ...but an anonymous visit does not put it on the public record
    bad = api.route("GET", "/api/state", {"city": "delhi", "mode": "replay", "date": "2024-05-2"}, {}, st)
    assert json.loads(bad["body"])["snapshot"] is None
    assert not st.get(store.snap_pk("delhi", "replay"), "2024-05-2")  # no second copy of a real day
    act = store.list_actions(st, "delhi", "replay", "2024-05-28")[0]
    api.route("POST", "/api/officer/done", {}, {"code": "heat2026", "city": "delhi", "mode": "replay",
                                                "date": "2024-05-28", "zone": act["zone"],
                                                "action_id": act["action_id"]}, st)
    assert ledger_days(st) == ["2024-05-28"]  # once an officer acts on the day, it counts
    st2 = store.MemoryStore()
    _replay_cache(st2, monkeypatch)
    api.route("POST", "/api/replay", {}, {"city": "delhi", "date": "2024-05-28", "token": "t0k", "notify": False}, st2)
    assert ledger_days(st2) == ["2024-05-28"]  # an explicit admin replay is always on the record


# ---- malformed input --------------------------------------------------------------
def test_malformed_requests_get_clean_errors(monkeypatch):
    from handlers import bot
    st = store.MemoryStore()
    assert api.route("POST", "/api/saathi", {}, ["list"], st)["statusCode"] == 400
    assert api.route("POST", "/api/ask", {}, "text", st, agent=lambda q, c: "x")["statusCode"] == 400
    ok = api.route("POST", "/api/ask", {}, {"question": 123, "city": "delhi"}, st, agent=lambda q, c: f"a:{q}")
    assert json.loads(ok["body"])["answer"] == "a:123"
    monkeypatch.setattr(api.store, "get_store", lambda: st)
    ev = {"requestContext": {"http": {"method": "POST"}}, "rawPath": "/api/saathi", "isBase64Encoded": True}
    for body in ("!!!not-base64", base64.b64encode(b"\xff\xfe").decode()):
        assert api.handler({**ev, "body": body}, None)["statusCode"] == 400
    monkeypatch.setenv("TELEGRAM_SECRET", "s3cret-s3cret-s3cret")
    ev = {"headers": {"x-telegram-bot-api-secret-token": "sécret"}, "body": "{}"}
    assert bot.handler(ev, None)["statusCode"] == 403


# ---- bot ------------------------------------------------------------------------
def test_officer_code_guessing_is_limited(monkeypatch):
    monkeypatch.setenv("OFFICER_CODE", "heat2026")
    st, tg = store.MemoryStore(), FakeTelegram()
    b = Bot(st, tg)
    for i in range(OFFICER_CODE_TRIES):
        b.handle(_msg(5, f"/officer guess{i}"))
    b.handle(_msg(5, "/officer heat2026"))
    assert "Too many" in tg.sent[-1][2] and not store.get_user(st, 5)


def test_officer_start_warns_and_non_text_messages_get_a_reply():
    st, tg = store.MemoryStore(), FakeTelegram()
    _officer(st, 7)
    b = Bot(st, tg)
    b.handle(_msg(7, "/start"))
    assert "registered as an officer" in tg.sent[0][2]
    store.save_user(st, 8, {"role": "worker", "city": "delhi", "zone": "narela", "lang": "en"})
    b.handle({"message": {"chat": {"id": 8}, "voice": {"file_id": "v"}}})
    assert "buttons below" in tg.sent[-1][2]
    b.handle({"message": {"chat": {"id": 9}, "photo": [{}]}})
    assert "/start" in tg.sent[-1][2]


def test_assistant_text_is_cleaned_for_telegram_and_long_messages_fit():
    from handlers.agent import clean
    from heatplan.telegram import Telegram
    assert clean("**Civil Lines** – 34.4 °C【zone_risk】  \n## Open\nx") == "Civil Lines – 34.4 °C\nOpen\nx"
    tg = Telegram("t")
    seen = {}
    tg.call = lambda method, payload, timeout=15: seen.update(payload) or {}
    tg.send(1, "a" * 5000)
    assert len(seen["text"]) == 4096


# ---- plan, CAP, deployment ------------------------------------------------------------
def test_hours_missing_humidity_or_sun_are_skipped_not_read_as_dry():
    city = plan.load_city("delhi")
    zone = city["zones"][0]
    rows = synthetic_day(day="2024-05-28", tmax=40, tmin=30, rh_day=60, rh_night=80)
    full = plan.zone_risk(zone, rows, "2024-05-28", city)
    gappy = [dict(r, rh=None) if r["time"].hour in (6, 7, 8) else r for r in rows]  # 11:30-13:30 IST
    part = plan.zone_risk(zone, gappy, "2024-05-28", city)
    assert all(h["rh"] > 10 for h in part["hourly"]) and len(part["hourly"]) == len(full["hourly"]) - 3
    none = plan.zone_risk(zone, [dict(r, sw=None) for r in rows], "2024-05-28", city)
    assert none == {"zone": zone["id"], "available": False}


def test_validator_survives_malformed_drafts():
    base = json.loads(json.dumps(plan.load_city("delhi")))
    for bad in ({**base, "actions": ["water points"]},
                {**base, "actions": [{**base["actions"][0], "id": ["x"]}]},
                {**base, "zones": ["narela"]},
                {**base, "zones": [{**base["zones"][0], "lat": "28.8"}]}):
        assert plan.validate_city(bad)


def test_cap_sent_time_is_valid_cap():
    from heatplan import cap
    snap = {"city": "delhi", "date": "2024-05-28", "zones": [], "generated_at": "2026-10-11T14:06:27+00:00"}
    sent = re.search(r"<sent>(.*)</sent>", cap.build(snap, "live")).group(1)
    assert sent == "2026-10-11T19:36:27+05:30"  # CAP 1.2 forbids "+00:00"


def test_hourly_run_covers_every_city_when_cities_is_empty(monkeypatch):
    from handlers import tick
    ran = []
    monkeypatch.setattr(tick, "run_city", lambda c: ran.append(c) or {"city": c})
    monkeypatch.setenv("CITIES", "")
    tick.handler({}, None)
    assert ran == plan.list_cities()


def test_template_gives_the_api_the_officer_code_and_runs_every_city():
    with open(os.path.join(os.path.dirname(__file__), "..", "template.yaml"), encoding="utf-8") as fh:
        t = fh.read()
    api_block = t.split("  ApiFunction:")[1].split("  AgentFunction:")[0]
    assert "OFFICER_CODE: !Ref OfficerCode" in api_block
    default = t.split("  Cities:")[1].split("Default:")[1].splitlines()[0].strip()
    assert sorted(default.split(",")) == plan.list_cities()
