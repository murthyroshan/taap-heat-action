"""The pipeline: weather -> zone risk -> plan actions -> officers and workers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import messages, store, voice
from .plan import city_risk, load_city, triggered_actions, zone_by_id
from .telegram import Telegram
from .wbgt import IST


def today_ist() -> str:
    return datetime.now(timezone.utc).astimezone(IST).date().isoformat()


class Notifier:
    def __init__(self, st, tg: Telegram | None = None):
        self.st = st
        self.tg = tg or Telegram()

    def _users(self, city_id, role):
        return [u for u in store.list_users(self.st) if u.get("city") == city_id and u.get("role") == role]

    def officers_for(self, city_id, action_role):
        return [u for u in self._users(city_id, "officer") if u.get("officer_role") in (action_role, "all")]

    def send_action(self, city, action, zr, mode):
        zone = zone_by_id(city, action["zone"])
        text = messages.officer_action(action, city, zone, zr, replay=mode == "replay")
        cb = f"d|{city['id']}|{mode}|{action['date']}|{action['zone']}|{action['action_id']}"
        sent = 0
        for o in self.officers_for(city["id"], action["role"]):
            self.tg.send(o["sk"], text, buttons=[[{"text": "✅ Done", "callback_data": cb}]])
            sent += 1
        return sent

    def alert_workers(self, city, snap, mode):
        sent = 0
        alert_pk = f"ALERT#{mode}#{snap['date']}"
        for u in self._users(city["id"], "worker"):
            zr = next((z for z in snap["zones"] if z["zone"] == u.get("zone")), None)
            if not zr or not zr.get("available") or zr["band"] < 1:
                continue
            key = f"{u['sk']}#{zr['zone']}"
            prev = self.st.get(alert_pk, key) or {"band": 0}
            if zr["band"] <= prev["band"]:
                continue
            self.st.put(alert_pk, key, {"band": zr["band"], "at": store.now_ts()})
            zone = zone_by_id(city, zr["zone"])
            lang = u.get("lang", "hi")
            msg = messages.worker_alert(zr, zone, snap["date"], lang, replay=mode == "replay")
            self.tg.send(u["sk"], msg["text"])
            audio = voice.synthesize(msg["voice"], lang)
            if audio:
                self.tg.send_voice(u["sk"], audio, caption="🔊")
            sent += 1
        return sent


def reset_day(st, city_id, mode, day):
    """Clear actions and alert log for a day (used to re-run a replay for a demo)."""
    for a in store.list_actions(st, city_id, mode, day):
        st.delete(store.act_pk(city_id, mode, day), a["sk"])
    for a in st.query(f"ALERT#{mode}#{day}"):
        st.delete(f"ALERT#{mode}#{day}", a["sk"])


def run(city_id: str, mode: str, day: str, series: dict, st, notifier: Notifier | None = None,
        notify: bool = True) -> dict:
    city = load_city(city_id)
    snap = city_risk(city, series, day)
    snap["mode"] = mode
    snap["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.save_snapshot(st, snap, mode)

    created = []
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
                created.append(item)

    stats = {"actions_created": len(created), "officer_messages": 0, "worker_alerts": 0}
    if notify:
        notifier = notifier or Notifier(st)
        by_zone = {z["zone"]: z for z in snap["zones"]}
        for item in created:
            stats["officer_messages"] += notifier.send_action(city, item, by_zone[item["zone"]], mode)
        stats["worker_alerts"] = notifier.alert_workers(city, snap, mode)
    snap["stats"] = stats
    return snap


def mark_done(st, city_id, mode, day, zone, action_id, who: str) -> dict | None:
    pk, sk = store.act_pk(city_id, mode, day), f"{zone}#{action_id}"
    item = st.get(pk, sk)
    if not item:
        return None
    if item["status"] != "done":
        item.update(status="done", done_at=store.now_ts(), done_by=who)
        st.put(pk, sk, item)
    return item


def day_range(start: str, end: str):
    d = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    while d <= e:
        yield d.isoformat()
        d += timedelta(days=1)
