"""Telegram webhook (Lambda Function URL).

Workers:  /start -> language -> city -> zone; then a keyboard for status and reports.
Officers: /officer <code> -> city -> role; then receive actions with a Done button.
Anyone:   /stop to unsubscribe. Officers: /ask <question> for the plan agent.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone

from heatplan import engine, messages, store
from heatplan.plan import list_cities, load_city, zone_by_id
from heatplan.telegram import Telegram
from heatplan.wbgt import IST

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
                self.tg.send(chat, self.agent(q, user["city"]))
            return

        if user.get("role") == "worker":
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
        chat = cq["message"]["chat"]["id"]
        mid = cq["message"]["message_id"]
        data = cq.get("data", "")
        who = (cq.get("from") or {}).get("first_name", "officer")
        parts = data.split("|")
        kind = parts[0]
        user = store.get_user(self.st, chat) or {}
        self.tg.answer(cq["id"])

        if kind == "l":  # language chosen -> pick city
            lang = parts[1]
            store.save_user(self.st, chat, {"role": "worker_pending", "lang": lang})
            prompt = "अपना शहर चुनें:" if lang == "hi" else "Choose your city:"
            self.tg.edit(chat, mid, prompt)
            self.tg.send(chat, prompt, buttons=_grid(
                [{"text": load_city(c)["name_hi" if lang == "hi" else "name"], "callback_data": f"c|{c}"}
                 for c in list_cities()]))
        elif kind == "c":  # city -> pick zone
            city = load_city(parts[1])
            lang = user.get("lang", "hi")
            user.update(city=city["id"])
            store.save_user(self.st, chat, user)
            prompt = "अपना इलाका चुनें:" if lang == "hi" else "Choose your area:"
            self.tg.send(chat, prompt, buttons=_grid(
                [{"text": z["name_hi" if lang == "hi" else "name"], "callback_data": f"z|{z['id']}"}
                 for z in city["zones"]]))
        elif kind == "z":
            lang = user.get("lang", "hi")
            user.update(role="worker", zone=parts[1], joined=store.now_ts())
            store.save_user(self.st, chat, user)
            city = load_city(user["city"])
            zone = zone_by_id(city, parts[1])
            done = (f"✅ आप {zone['name_hi']} की गर्मी चेतावनी से जुड़ गए। खतरा होने पर हम आवाज़ और संदेश भेजेंगे।"
                    if lang == "hi" else f"✅ Subscribed to heat alerts for {zone['name']}. You'll get a voice note and text when it gets dangerous.")
            self.tg.send(chat, done, keyboard=_keyboard(lang))
            self.send_status(chat, user)
        elif kind == "oc":
            city = load_city(parts[1])
            user.update(city=city["id"])
            store.save_user(self.st, chat, user)
            roles = [{"text": label, "callback_data": f"or|{rid}"} for rid, label in city["roles"].items()]
            roles.append({"text": "Nodal officer (all actions)", "callback_data": "or|all"})
            self.tg.send(chat, "Your department:", buttons=_grid(roles, 1))
        elif kind == "or":
            user.update(role="officer", officer_role=parts[1], name=who)
            store.save_user(self.st, chat, user)
            self.tg.send(chat, f"✅ Registered as {parts[1]} officer for {load_city(user['city'])['name']}. "
                               "Plan actions and worker reports for your department will arrive here. /ask <question> for the plan assistant.")
        elif kind == "d":  # d|city|mode|date|zone|action
            _, city_id, mode, day, zone, aid = parts
            item = engine.mark_done(self.st, city_id, mode, day, zone, aid, who)
            if item:
                t = datetime.fromtimestamp(item["done_at"], timezone.utc).astimezone(IST).strftime("%H:%M")
                self.tg.edit(chat, mid, f"✅ DONE by {item['done_by']} at {t} IST\n\n{item['text_rendered']}")
        elif kind == "r":  # r|city|date|sk
            _, city_id, day, sk = parts
            pk = f"REP#{city_id}#{day}"
            rep = self.st.get(pk, sk)
            if rep and rep["status"] != "resolved":
                rep.update(status="resolved", resolved_by=who, resolved_at=store.now_ts())
                self.st.put(pk, sk, rep)
                self.tg.edit(chat, mid, f"✅ RESOLVED by {who}\n{messages.REPORT_TYPES[rep['type']]['en']} · {rep['zone_name']}")

    # ---- worker features ------------------------------------------------
    def send_status(self, chat, user):
        lang = user.get("lang", "hi")
        snap = store.load_snapshot(self.st, user["city"], "live")
        city = load_city(user["city"])
        zone = zone_by_id(city, user["zone"])
        zr = next((z for z in snap["zones"] if z["zone"] == user["zone"]), None) if snap else None
        self.tg.send(chat, messages.status_text(zr, zone, snap["date"] if snap else engine.today_ist(), lang))

    def report(self, chat, user, rtype):
        lang = user.get("lang", "hi")
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


def _invoke_agent(question: str, city: str) -> str:
    fn = os.environ.get("AGENT_FUNCTION")
    if not fn:
        return "Assistant is not configured."
    import boto3
    res = boto3.client("lambda").invoke(FunctionName=fn, Payload=json.dumps({"question": question, "city": city}))
    out = json.loads(res["Payload"].read() or b"{}")
    return out.get("answer") or out.get("error") or "No answer."


def handler(event, context):
    secret = os.environ.get("TELEGRAM_SECRET")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if secret and headers.get("x-telegram-bot-api-secret-token") != secret:
        return {"statusCode": 403, "body": "forbidden"}
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    try:
        Bot(agent=_invoke_agent).handle(json.loads(body))
    except Exception as e:  # always 200 so Telegram doesn't retry forever
        print(f"bot error: {e!r}")
    return {"statusCode": 200, "body": "ok"}
