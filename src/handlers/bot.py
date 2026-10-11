"""Telegram webhook (Lambda Function URL).

Workers:  /start -> language -> city -> zone; then a keyboard for status and reports.
Officers: /officer <code> -> city -> role; then receive actions with a Done button.
Anyone:   /stop to unsubscribe. Officers: /ask <question> for the plan agent.
"""
from __future__ import annotations

import base64
import hmac
import json
import os
from datetime import datetime, timezone

from heatplan import engine, messages, store
from heatplan.plan import list_cities, load_city, zone_by_id
from heatplan.telegram import Telegram
from heatplan.wbgt import IST

REPORT_COOLDOWN_S = 30 * 60
WELCOME = "🔥 Taap — heat alerts / गर्मी चेतावनी\nभाषा चुनें / Choose language:"


def _keyboard(lang):
    t = messages.REPORT_TYPES
    return [[messages.STATUS_BUTTON[lang]], [t["water"][lang], t["shade"][lang]], [t["work"][lang], t["ill"][lang]]]


def _grid(buttons, per_row=2):
    return [buttons[i:i + per_row] for i in range(0, len(buttons), per_row)]


class Bot:
    def __init__(self, st=None, tg=None, agent=None):
        self.st = st or store.get_store()
        self.tg = tg or Telegram()
        self.agent = agent  # callable(question, city) -> str

    # ---- entry points -------------------------------------------------
    def handle(self, update: dict):
        if "callback_query" in update:
            return self.on_callback(update["callback_query"])
        msg = update.get("message") or {}
        if "text" in msg:
            return self.on_text(msg)

    def on_text(self, msg):
        chat = msg["chat"]["id"]
        text = msg["text"].strip()
        user = store.get_user(self.st, chat) or {}
        name = (msg.get("from") or {}).get("first_name", "")

        if text.startswith("/start"):
            self.tg.send(chat, WELCOME, buttons=[[{"text": "हिंदी", "callback_data": "l|hi"},
                                                   {"text": "English", "callback_data": "l|en"}]])
            return
        if text.startswith("/officer"):
            code = text.split(maxsplit=1)[1].strip() if " " in text else ""
            if not os.environ.get("OFFICER_CODE") or code != os.environ["OFFICER_CODE"]:
                self.tg.send(chat, "Officer code not recognised.")
                return
            store.save_user(self.st, chat, {"role": "officer_pending", "name": name, "lang": "en"})
            self.tg.send(chat, "Officer registration. Choose your city:", buttons=_grid(
                [{"text": load_city(c)["name"], "callback_data": f"oc|{c}"} for c in list_cities()]))
            return
        if text.startswith("/stop"):
            self.st.delete("USER", str(chat))
            self.tg.send(chat, "Unsubscribed. / सदस्यता बंद। /start to join again.")
            return
        if text.startswith("/ask"):
            q = text[4:].strip()
            if user.get("role") != "officer":
                self.tg.send(chat, "The plan assistant is for registered officers.")
            elif not q:
                self.tg.send(chat, "Usage: /ask Which zones still have open actions?")
            elif not self.agent:
                self.tg.send(chat, "Assistant is not configured.")
            else:
                self.tg.send(chat, "🤔 …")
                reply = self.agent(q, user["city"], chat)
                if reply:
                    self.tg.send(chat, reply)
            return

        if user.get("role") == "worker" and user.get("zone"):
            lang = user.get("lang", "hi")
            if text == messages.STATUS_BUTTON[lang]:
                return self.send_status(chat, user)
            for rtype, labels in messages.REPORT_TYPES.items():
                if text == labels[lang]:
                    return self.report(chat, user, rtype)
            self.tg.send(chat, "👇", keyboard=_keyboard(lang))
            return
        self.tg.send(chat, "Send /start to subscribe to heat alerts. / गर्मी चेतावनी के लिए /start भेजें।")

    def on_callback(self, cq):
        """Button presses. callback_data can be forged by a modified client, so every
        privileged action re-checks the sender's stored role and city."""
        msg = cq.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        mid = msg.get("message_id")
        who = (cq.get("from") or {}).get("first_name", "officer")
        parts = (cq.get("data") or "").split("|")
        kind = parts[0]
        user = store.get_user(self.st, chat) or {}
        self.tg.answer(cq["id"])
        cities = list_cities()

        if kind == "l" and len(parts) == 2 and parts[1] in ("hi", "en"):  # language -> city
            lang = parts[1]
            store.save_user(self.st, chat, {"role": "worker_pending", "lang": lang})
            prompt = "अपना शहर चुनें:" if lang == "hi" else "Choose your city:"
            self.tg.send(chat, prompt, buttons=_grid(
                [{"text": load_city(c)["name_hi" if lang == "hi" else "name"], "callback_data": f"c|{c}"}
                 for c in cities]))
        elif kind == "c" and len(parts) == 2 and parts[1] in cities:  # city -> zone
            if user.get("role") not in ("worker_pending", "worker"):
                return self._restart(chat)
            city = load_city(parts[1])
            lang = user.get("lang", "hi")
            user.update(city=city["id"], role="worker_pending")  # no alerts until a zone in this city is picked
            user.pop("zone", None)
            store.save_user(self.st, chat, user)
            prompt = "अपना इलाका चुनें:" if lang == "hi" else "Choose your area:"
            self.tg.send(chat, prompt, buttons=_grid(
                [{"text": z["name_hi" if lang == "hi" else "name"], "callback_data": f"z|{z['id']}"}
                 for z in city["zones"]]))
        elif kind == "z" and len(parts) == 2:
            if user.get("role") not in ("worker_pending", "worker") or user.get("city") not in cities:
                return self._restart(chat)
            city = load_city(user["city"])
            zone = zone_by_id(city, parts[1])
            if not zone:
                return self._restart(chat)
            lang = user.get("lang", "hi")
            user.update(role="worker", zone=zone["id"], joined=store.now_ts())
            store.save_user(self.st, chat, user)
            done = (f"✅ आप {zone['name_hi']} की गर्मी चेतावनी से जुड़ गए। खतरा होने पर हम आवाज़ और संदेश भेजेंगे।"
                    if lang == "hi" else f"✅ Subscribed to heat alerts for {zone['name']}. You'll get a voice note and text when it gets dangerous.")
            self.tg.send(chat, done, keyboard=_keyboard(lang))
            self.send_status(chat, user)
        elif kind == "oc" and len(parts) == 2 and parts[1] in cities:
            if user.get("role") not in ("officer_pending", "officer"):
                return  # only after /officer <code>
            city = load_city(parts[1])
            user.update(role="officer_pending", city=city["id"])
            store.save_user(self.st, chat, user)
            roles = [{"text": label, "callback_data": f"or|{rid}"} for rid, label in city["roles"].items()]
            roles.append({"text": "Nodal officer (all actions)", "callback_data": "or|all"})
            self.tg.send(chat, "Your department:", buttons=_grid(roles, 1))
        elif kind == "or" and len(parts) == 2:
            if user.get("role") != "officer_pending" or user.get("city") not in cities:
                return
            city = load_city(user["city"])
            if parts[1] not in city["roles"] and parts[1] != "all":
                return
            user.update(role="officer", officer_role=parts[1], name=who)
            store.save_user(self.st, chat, user)
            self.tg.send(chat, f"✅ Registered as {parts[1]} officer for {city['name']}. "
                               "Plan actions and worker reports for your department will arrive here. /ask <question> for the plan assistant.")
        elif kind == "d" and len(parts) == 6:  # d|city|mode|date|zone|action
            _, city_id, mode, day, zone, aid = parts
            item = self.st.get(store.act_pk(city_id, mode, day), f"{zone}#{aid}")
            if not item or not self._may_handle(user, city_id, item["role"]):
                return self.tg.send(chat, "Only the responsible officer can mark this action done.")
            item = engine.mark_done(self.st, city_id, mode, day, zone, aid, user.get("name") or who)
            t = datetime.fromtimestamp(item["done_at"], timezone.utc).astimezone(IST).strftime("%H:%M")
            self._mark_button(chat, mid, msg, cq.get("data"), f"✔️ {messages.action_label(aid)} — {item['done_by']} {t}")
        elif kind == "r" and len(parts) == 4:  # r|city|date|sk
            _, city_id, day, sk = parts
            pk = f"REP#{city_id}#{day}"
            rep = self.st.get(pk, sk)
            if not rep or not self._may_handle(user, city_id, messages.REPORT_TYPES[rep["type"]]["role"]):
                return self.tg.send(chat, "Only the responsible officer can resolve this report.")
            if rep["status"] != "resolved":
                rep.update(status="resolved", resolved_by=user.get("name") or who, resolved_at=store.now_ts())
                self.st.put(pk, sk, rep)
            self._mark_button(chat, mid, msg, cq.get("data"), f"✔️ Resolved — {rep['resolved_by']}")

    @staticmethod
    def _may_handle(user, city_id, role) -> bool:
        return (user.get("role") == "officer" and user.get("city") == city_id
                and user.get("officer_role") in (role, "all"))

    def _mark_button(self, chat, mid, msg, data, label):
        rows = (msg.get("reply_markup") or {}).get("inline_keyboard") or []
        rows = [[{"text": label, "callback_data": "x"} if b.get("callback_data") == data else b for b in row]
                for row in rows]
        self.tg.edit_buttons(chat, mid, rows)

    def _restart(self, chat):
        self.tg.send(chat, "Please start again with /start. / कृपया /start से दोबारा शुरू करें।")

    # ---- worker features ------------------------------------------------
    def send_status(self, chat, user):
        lang = user.get("lang", "hi")
        if user.get("city") not in list_cities():
            return self._restart(chat)
        snap = store.load_snapshot(self.st, user["city"], "live")
        city = load_city(user["city"])
        zone = zone_by_id(city, user.get("zone"))
        if not zone:
            return self._restart(chat)
        zr = next((z for z in snap["zones"] if z["zone"] == user["zone"]), None) if snap else None
        self.tg.send(chat, messages.status_text(zr, zone, snap["date"] if snap else engine.today_ist(), lang))

    def report(self, chat, user, rtype):
        lang = user.get("lang", "hi")
        if user.get("city") not in list_cities() or not zone_by_id(load_city(user["city"]), user.get("zone")):
            return self._restart(chat)
        last = self.st.get("RATE", f"{chat}#{rtype}")
        if last and store.now_ts() - last["at"] < REPORT_COOLDOWN_S:
            self.tg.send(chat, "✅ यह रिपोर्ट पहले ही भेजी जा चुकी है।" if lang == "hi" else "✅ Already reported recently.")
            return
        self.st.put("RATE", f"{chat}#{rtype}", {"at": store.now_ts()})
        city = load_city(user["city"])
        zone = zone_by_id(city, user["zone"])
        now = datetime.now(timezone.utc).astimezone(IST)
        day = now.date().isoformat()
        sk = f"{now.strftime('%H%M%S')}#{chat}"
        rep = {"type": rtype, "city": city["id"], "zone": zone["id"], "zone_name": zone["name"],
               "time": now.strftime("%H:%M"), "status": "open", "lang": lang}
        self.st.put(f"REP#{city['id']}#{day}", sk, rep)
        n = engine.Notifier(self.st, self.tg)
        for o in n.officers_for(city["id"], messages.REPORT_TYPES[rtype]["role"]):
            self.tg.send(o["sk"], messages.officer_report(rep, city, zone),
                         buttons=[[{"text": "✅ Resolved", "callback_data": f"r|{city['id']}|{day}|{sk}"}]])
        self.tg.send(chat, messages.ILL_REPLY[lang] if rtype == "ill" else messages.REPORT_THANKS[lang])


def _invoke_agent(question: str, city: str, chat_id=None) -> str | None:
    """With a chat id the agent runs asynchronously and replies on Telegram itself, so a slow
    model call can never time out the webhook (which would make Telegram redeliver the update)."""
    fn = os.environ.get("AGENT_FUNCTION")
    if not fn:
        return "Assistant is not configured."
    import boto3
    from botocore.config import Config
    client = boto3.client("lambda", config=Config(read_timeout=100, retries={"max_attempts": 1}))
    payload = json.dumps({"question": question, "city": city, "chat_id": chat_id})
    if chat_id is not None:
        client.invoke(FunctionName=fn, InvocationType="Event", Payload=payload)
        return None
    res = client.invoke(FunctionName=fn, Payload=payload)
    out = json.loads(res["Payload"].read() or b"{}")
    return out.get("answer") or out.get("error") or "No answer."


def handler(event, context):
    secret = os.environ.get("TELEGRAM_SECRET")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not secret or not hmac.compare_digest(headers.get("x-telegram-bot-api-secret-token", ""), secret):
        return {"statusCode": 403, "body": "forbidden"}  # only Telegram knows the secret
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    try:
        Bot(agent=_invoke_agent).handle(json.loads(body))
    except Exception as e:  # always 200 so Telegram doesn't retry forever
        print(f"bot error: {e!r}")
    return {"statusCode": 200, "body": "ok"}
