import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from helpers import FakeTelegram, synthetic_day

from heatplan import cap, engine, messages, plan, poster, store, weather
from heatplan.wbgt import stull_wet_bulb, wbgt

NOON_IST = datetime(2024, 5, 28, 7, 30, tzinfo=timezone.utc)
NIGHT_IST = datetime(2024, 5, 28, 21, 30, tzinfo=timezone.utc)


def test_wbgt_night_matches_wet_bulb_physics():
    # No sun: globe ~ air temperature and natural wet bulb ~ psychrometric wet bulb.
    r = wbgt(30, 50, 8, 0, 0, 28.6, 77.2, NIGHT_IST)
    assert abs(r["tnwb"] - stull_wet_bulb(30, 50)) < 1.0
    assert abs(r["tg"] - 30) < 1.5
    assert 23 < r["wbgt"] < 26


def test_wbgt_sun_and_humidity_raise_stress():
    dry_shade = wbgt(40, 20, 8, 0, 0, 28.6, 77.2, NIGHT_IST)["wbgt"]
    dry_sun = wbgt(40, 20, 8, 900, 650, 28.6, 77.2, NOON_IST)["wbgt"]
    humid_sun = wbgt(40, 50, 8, 900, 650, 28.6, 77.2, NOON_IST)["wbgt"]
    assert dry_sun > dry_shade + 3
    assert humid_sun > dry_sun + 3
    assert 28 < dry_sun < 36


def test_wind_cools():
    calm = wbgt(38, 40, 1, 800, 600, 28.6, 77.2, NOON_IST)["wbgt"]
    windy = wbgt(38, 40, 25, 800, 600, 28.6, 77.2, NOON_IST)["wbgt"]
    assert windy < calm


def test_bands_follow_acgih_heavy_work_steps():
    assert [plan.band_for(x) for x in (24.9, 25.0, 27.5, 29.0, 30.5, 33)] == [0, 1, 2, 3, 4, 4]


def test_tiers_and_hot_night_escalation():
    city = plan.load_city("ahmedabad")
    assert plan.tier_for(42.0, city["tiers"]) == "yellow"
    assert plan.tier_for(45.0, city["tiers"]) == "red"
    zone = city["zones"][0]
    cool_night = plan.zone_risk(zone, synthetic_day(tmax=42, tmin=26), "2024-05-28", city)
    hot_night = plan.zone_risk(zone, synthetic_day(tmax=42, tmin=32), "2024-05-28", city)
    assert cool_night["tier"] == "yellow" and not cool_night["hot_night"]
    assert hot_night["hot_night"] and hot_night["tier"] == "orange"


def test_live_forecast_fixture_parses():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "forecast_delhi_2zones.json")
    city = plan.load_city("delhi")
    zones = [plan.zone_by_id(city, "narela"), plan.zone_by_id(city, "newdelhi")]
    series = weather.parse(json.load(open(path)), zones)
    snap = plan.city_risk(city, series, "2026-10-11")
    narela = next(z for z in snap["zones"] if z["zone"] == "narela")
    assert narela["available"] and len(narela["hourly"]) == 24
    # Roundtrip used by the cache
    assert weather.deserialise(weather.serialise(series))["narela"][0]["time"] == series["narela"][0]["time"]


def _seed(st):
    store.save_user(st, 111, {"role": "worker", "city": "delhi", "zone": "narela", "lang": "hi"})
    store.save_user(st, 222, {"role": "worker", "city": "delhi", "zone": "okhla", "lang": "en"})
    store.save_user(st, 900, {"role": "officer", "city": "delhi", "officer_role": "disaster", "name": "DM North"})
    store.save_user(st, 901, {"role": "officer", "city": "delhi", "officer_role": "all", "name": "Nodal"})


def test_engine_creates_actions_alerts_once_and_tracks_done():
    st, tg = store.MemoryStore(), FakeTelegram()
    _seed(st)
    city = plan.load_city("delhi")
    series = {z["id"]: synthetic_day(tmax=47.5, tmin=34) for z in city["zones"]}
    snap = engine.run("delhi", "replay", "2024-05-28", series, st, engine.Notifier(st, tg))
    assert snap["tier"] == "red" and snap["max_band"] == 4
    acts = store.list_actions(st, "delhi", "replay", "2024-05-28")
    assert len(acts) == len(city["zones"]) * len(city["actions"])
    assert snap["stats"]["worker_alerts"] == 2
    # Nodal officer gets everything, disaster officer only disaster actions.
    to_nodal = [m for m in tg.sent if m[0] == "send" and m[1] == "901"]
    to_dm = [m for m in tg.sent if m[0] == "send" and m[1] == "900"]
    assert len(to_nodal) == len(acts)
    assert len(to_dm) == sum(1 for a in acts if a["role"] == "disaster")
    assert all(len(m[3][0][0]["callback_data"]) <= 64 for m in to_nodal)
    hindi = [m for m in tg.sent if m[1] == "111" and m[0] == "send"][0][2]
    assert "भारी काम बंद" in hindi and "112" in hindi and "REPLAY" in hindi

    # Re-running the same day neither duplicates actions nor re-alerts.
    tg.sent.clear()
    snap2 = engine.run("delhi", "replay", "2024-05-28", series, st, engine.Notifier(st, tg))
    assert snap2["stats"] == {"actions_created": 0, "officer_messages": 0, "worker_alerts": 0}

    done = engine.mark_done(st, "delhi", "replay", "2024-05-28", "narela", "cool", "DM North")
    assert done["status"] == "done" and done["done_by"] == "DM North"

    engine.reset_day(st, "delhi", "replay", "2024-05-28")
    assert store.list_actions(st, "delhi", "replay", "2024-05-28") == []


def test_cap_feed_is_valid_xml():
    st = store.MemoryStore()
    city = plan.load_city("delhi")
    series = {z["id"]: synthetic_day(tmax=46, tmin=31) for z in city["zones"]}
    snap = engine.run("delhi", "replay", "2024-05-28", series, st, notify=False)
    root = ET.fromstring(cap.build(snap, "replay"))
    ns = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}
    assert root.find("c:status", ns).text == "Exercise"
    assert len(root.findall("c:info", ns)) == len(city["zones"])


def test_poster_and_messages_render():
    st = store.MemoryStore()
    series = {z["id"]: synthetic_day() for z in plan.load_city("delhi")["zones"]}
    snap = engine.run("delhi", "live", "2024-05-28", series, st, notify=False)
    html = poster.build(snap, "narela")
    assert "गर्मी सूचना" in html and "112" in html
    assert messages.window_hi("12:00", "16:00") == "दोपहर 12 बजे से शाम 4 बजे तक"
    assert messages.window_en("09:00", "13:00") == "9 AM to 1 PM"
