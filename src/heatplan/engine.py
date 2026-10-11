"""The pipeline: weather -> zone risk -> plan actions -> officers and workers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import messages, store, voice
from .plan import city_risk, load_city, personalise, triggered_actions, zone_by_id
from .telegram import Telegram
from .wbgt import IST

# Live mode only tasks officers and pushes alerts in waking hours (IST), so a
# new day's snapshot at 00:05 does not wake anyone; the 06:05 run sends them.
ACTIVE_HOURS = (6, 21)


def today_ist(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(IST).date().isoformat()


def is_active(mode: str, now: datetime | None = None) -> bool:
    if mode != "live":
        return True
    h = (now or datetime.now(timezone.utc)).astimezone(IST).hour
    return ACTIVE_HOURS[0] <= h < ACTIVE_HOURS[1]


def done_callback(item: dict) -> str:
    return f"d|{item['city']}|{item['mode']}|{item['date']}|{item['zone']}|{item['action_id']}"


class Notifier:
    def __init__(self, st, tg: Telegram | None = None):
        self.st = st
        self.tg = tg or Telegram()
        self._user_cache = None

    def _all_users(self):
        if self._user_cache is None:
            self._user_cache = store.list_users(self.st)
        return self._user_cache

    def _users(self, city_id, role):
        return [u for u in self._all_users() if u.get("city") == city_id and u.get("role") == role]

    def officers_for(self, city_id, action_role):
        return [u for u in self._users(city_id, "officer") if u.get("officer_role") in (action_role, "all")]

    def send_zone_actions(self, city, items, zr, mode) -> int:
        """One message per officer per zone, with a Done button for each of their actions."""
        zone = zone_by_id(city, zr["zone"])
        sent = 0
        for o in self._users(city["id"], "officer"):
            mine = [a for a in items if o.get("officer_role") in (a["role"], "all")]
            if not mine:
                continue
            text = messages.officer_actions(mine, city, zone, zr, replay=mode == "replay")
            buttons = [[{"text": f"✅ {messages.action_label(a['action_id'])}", "callback_data": done_callback(a)}]
                       for a in mine]
            self.tg.send(o["sk"], text, buttons=buttons)
            sent += 1
        return sent

    def alert_workers(self, city, snap, mode, now: datetime | None = None) -> int:
        sent = 0
        alert_pk = f"ALERT#{mode}#{snap['date']}"
        hour_now = (now or datetime.now(timezone.utc)).astimezone(IST).hour
        audio_cache: dict[str, bytes | None] = {}
        for u in self._users(city["id"], "worker"):
            zr = next((z for z in snap["zones"] if z["zone"] == u.get("zone")), None)
            zr = personalise(zr, bool(u.get("new_worker")))
            if not zr or not zr.get("available") or zr["band"] < city.get("alert_min_band", 1):
                continue
            if mode == "live" and zr["end"] and hour_now >= int(zr["end"][:2]):
                continue  # the dangerous window is already over today
            key = f"{u['sk']}#{zr['zone']}"
            prev = self.st.get(alert_pk, key) or {"band": 0}
            if zr["band"] <= prev["band"]:
                continue
            self.st.put(alert_pk, key, {"band": zr["band"], "at": store.now_ts()})
            zone = zone_by_id(city, zr["zone"])
            lang = u.get("lang", "hi")
            msg = messages.worker_alert(zr, zone, snap["date"], lang, replay=mode == "replay")
            self.tg.send(u["sk"], msg["text"])
            if msg["voice"] not in audio_cache:  # same zone + band + language -> same audio
                audio_cache[msg["voice"]] = voice.synthesize(msg["voice"], lang)
            if audio_cache[msg["voice"]]:
                self.tg.send_voice(u["sk"], audio_cache[msg["voice"]], caption="🔊")
            sent += 1
        return sent


def reset_day(st, city_id, mode, day):
    """Clear actions and alert log for a day (used to re-run a replay for a demo)."""
    for a in store.list_actions(st, city_id, mode, day):
        st.delete(store.act_pk(city_id, mode, day), a["sk"])
    for a in st.query(f"ALERT#{mode}#{day}"):
        st.delete(f"ALERT#{mode}#{day}", a["sk"])


def run(city_id: str, mode: str, day: str, series: dict, st, notifier: Notifier | None = None,
        notify: bool = True, now: datetime | None = None) -> dict:
    city = load_city(city_id)
    snap = city_risk(city, series, day)
    snap["mode"] = mode
    snap["generated_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    store.save_snapshot(st, snap, mode)

    stats = {"actions_created": 0, "officer_messages": 0, "worker_alerts": 0, "quiet_hours": False}
    if not is_active(mode, now):
        stats["quiet_hours"] = True
        snap["stats"] = stats
        return snap

    created: dict[str, list] = {}
    for zr in snap["zones"]:
        zone = zone_by_id(city, zr["zone"])
        for a in triggered_actions(city, zr):
            item = {
                "city": city_id, "mode": mode, "date": day, "zone": zr["zone"], "zone_name": zone["name"],
                "action_id": a["id"], "role": a["role"], "ref": a["ref"],
                "text_rendered": a["text"].format(zone=zone["name"], start=zr["start"] or "", end=zr["end"] or ""),
                "tier": zr["tier"], "band": zr["band"], "status": "open",
                "created_at": store.now_ts(), "done_at": None, "done_by": None,
            }
            if st.put(store.act_pk(city_id, mode, day), f"{zr['zone']}#{a['id']}", item, if_absent=True):
                created.setdefault(zr["zone"], []).append(item)
    stats["actions_created"] = sum(len(v) for v in created.values())

    if notify:
        notifier = notifier or Notifier(st)
        by_zone = {z["zone"]: z for z in snap["zones"]}
        for zone_id, items in created.items():
            stats["officer_messages"] += notifier.send_zone_actions(city, items, by_zone[zone_id], mode)
        stats["worker_alerts"] = notifier.alert_workers(city, snap, mode, now)
    snap["stats"] = stats
    return snap


def mark_done(st, city_id, mode, day, zone, action_id, who: str, place: dict | None = None) -> dict | None:
    """Mark a plan action done; `place` ({lat, lon, label}) puts e.g. a cooling centre on the workers' map."""
    pk, sk = store.act_pk(city_id, mode, day), f"{zone}#{action_id}"
    item = st.get(pk, sk)
    if not item:
        return None
    if item["status"] != "done":
        item.update(status="done", done_at=store.now_ts(), done_by=who)
    if place:
        item["place"] = place
    st.put(pk, sk, item)
    return item


def day_range(start: str, end: str):
    d = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)
