"""Saathi: a voice-first heat companion for outdoor workers (Hindi / English).

Three layers, in order:
  1. Emergency triage - fixed rules, never the model. Red-flag symptoms get first aid + 112.
  2. A Strands agent with tools that act: today's risk, nearest help with walking time,
     a safe shift plan, filing a report to the right officer, workers' rights.
  3. A rule-based fallback with the same tools, so it still answers when no model is available.
Every tool also returns a "card" the page renders (map links, schedule, emergency).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import messages, places, plan, shift, store
from .plan import BANDS, load_city, zone_by_id
from .wbgt import IST

RED_FLAGS = [
    "बेहोश", "बेहोशी", "होश नहीं", "गिर गया", "गिर गई", "दौरा", "उल्टी", "उलटी", "भ्रम", "बोल नहीं", "पसीना नहीं", "सांस", "साँस",
    "faint", "fainted", "unconscious", "collapse", "collapsed", "seizure", "fit ", "vomit", "confus", "not sweating",
    "can't breathe", "cannot breathe", "chest pain", "not responding", "passed out",
]
WARN_SIGNS = ["चक्कर", "सिरदर्द", "कमज़ोरी", "कमजोरी", "थकान", "ऐंठन", "dizzy", "headache", "weak", "cramp", "tired", "nausea", "जी मिचला"]

EMERGENCY = {
    "hi": {"title": "🚨 यह आपात स्थिति हो सकती है", "call": "अभी 112 पर कॉल करें",
           "steps": ["व्यक्ति को तुरंत छाँव या ठंडी जगह ले जाएँ", "कपड़े ढीले करें, शरीर पर ठंडा पानी डालें और हवा करें",
                     "होश में हो तो घूँट-घूँट पानी दें; बेहोश हो तो कुछ न पिलाएँ", "उनके साथ रहें जब तक मदद न आए"]},
    "en": {"title": "🚨 This may be an emergency", "call": "Call 112 now",
           "steps": ["Move the person to shade or a cool place now", "Loosen clothing, pour cool water on the body and fan them",
                     "If conscious, give sips of water; if not, give nothing by mouth", "Stay with them until help arrives"]},
}
WARN = {
    "hi": "ये गर्मी से थकावट के लक्षण हो सकते हैं। अभी काम रोकें, छाँव में बैठें, पानी पिएँ। 30 मिनट में ठीक न लगे या बेहोशी, उल्टी, भ्रम हो तो 112 पर कॉल करें।",
    "en": "These can be signs of heat exhaustion. Stop work now, sit in shade and drink water. If you don't feel better in 30 minutes, or there is fainting, vomiting or confusion, call 112.",
}


def triage(text: str):
    t = text.lower()
    if any(k in t for k in RED_FLAGS):
        return "emergency"
    if any(k in t for k in WARN_SIGNS):
        return "warning"
    return None


class Session:
    """Context for one turn: who is asking, where, and the cards tools produce."""

    def __init__(self, st, city: str, zone: str | None, lang: str, lat=None, lon=None, new_worker=False, chat_id="web"):
        self.st, self.city, self.lang, self.new_worker, self.chat_id = st, load_city(city), lang, new_worker, chat_id
        self.lat, self.lon = lat, lon
        z = zone_by_id(self.city, zone) if zone else None
        if not z and lat is not None:
            z = places.nearest_zone(self.city, lat, lon)
        self.zone = z or self.city["zones"][0]
        self.cards: list[dict] = []
        snap = store.load_snapshot(st, self.city["id"], "live")
        self.date = snap["date"] if snap else ""
        zr = next((x for x in snap["zones"] if x["zone"] == self.zone["id"]), None) if snap else None
        self.zr = plan.personalise(zr, new_worker) if zr and zr.get("available") else None

    # ---- tools (plain functions; wrapped for the agent below) ----
    def risk_now(self) -> dict:
        zr = self.zr
        if not zr:
            return {"available": False}
        hour = datetime.now(timezone.utc).astimezone(IST).hour
        now = next((h for h in zr["hourly"] if h["hour"] == hour), None)
        b = BANDS[zr["band"]]
        nb = BANDS[now["band"]] if now else b
        out = {"zone": self.zone["name"], "date": self.date, "today_band": zr["band"], "today_label": b["label"],
               "danger_window": [zr["start"], zr["end"]], "peak_wbgt": zr["peak_wbgt"], "tmax": zr["tmax"],
               "now_hour": hour, "now_band": now["band"] if now else None, "now_wbgt": now["wbgt"] if now else None,
               "now_rule": ("stop heavy work" if nb["band"] >= 4 else f"{nb['work']} min work / {nb['rest']} min rest per hour")
               if nb["band"] else "normal work", "new_worker_limits": self.new_worker}
        self.cards.append({"type": "risk", "zone": self.zone, "risk": {k: zr[k] for k in ("band", "start", "end", "peak_wbgt", "tmax", "hourly")},
                           "now": now})
        return out

    def nearest_help(self, kind: str = "any") -> dict:
        band = self.zr["band"] if self.zr else 0
        lat = self.lat if self.lat is not None else self.zone["lat"]
        lon = self.lon if self.lon is not None else self.zone["lon"]
        res = places.nearby(self.st, self.city["id"], lat, lon, band, "live", self.date)
        kinds = [kind] if kind in places.KINDS else list(places.KINDS)
        found = {k: res["places"].get(k, []) for k in kinds}
        self.cards.append({"type": "places", "places": found, "max_walk_min": res["max_walk_min"],
                           "from": "you" if self.lat is not None else self.zone["name"]})
        return {"max_safe_walk_min": res["max_walk_min"],
                "places": {k: [{"name": p["n"], "walk_min": p["walk_min"], "too_far": p["too_far"], "official": bool(p.get("official"))}
                               for p in v] for k, v in found.items()}}

    def shift_plan(self, hours: float = 8) -> dict:
        if not self.zr:
            return {"available": False}
        res = shift.plan_shift(self.zr, max(1.0, min(12.0, float(hours))), new_worker=False)  # zr already personalised
        self.cards.append({"type": "shift", "plan": res, "zone": self.zone})
        return {k: res[k] for k in ("feasible", "start", "finish", "safe_capacity_hours", "usual_hot_hours", "short_by_min")}

    def file_report(self, kind: str) -> dict:
        kind = {"no_water": "water", "no_shade": "shade", "work_not_stopped": "work", "someone_unwell": "ill"}.get(kind, kind)
        if kind not in messages.REPORT_TYPES:
            return {"filed": False, "error": "kind must be water, shade, work or ill"}
        now = datetime.now(timezone.utc).astimezone(IST)
        day = now.date().isoformat()
        rate_key = f"{self.chat_id}#{kind}"
        last = self.st.get("RATE", rate_key)
        if last and store.now_ts() - last["at"] < 30 * 60:
            self.cards.append({"type": "report", "kind": kind, "duplicate": True})
            return {"filed": False, "reason": "already reported in the last 30 minutes"}
        self.st.put("RATE", rate_key, {"at": store.now_ts()})
        rep = {"type": kind, "city": self.city["id"], "zone": self.zone["id"], "zone_name": self.zone["name"],
               "time": now.strftime("%H:%M"), "status": "open", "lang": self.lang, "via": "saathi"}
        self.st.put(f"REP#{self.city['id']}#{day}", f"{now.strftime('%H%M%S')}#{self.chat_id}", rep)
        role = messages.REPORT_TYPES[kind]["role"]
        self.cards.append({"type": "report", "kind": kind, "zone": self.zone["name"], "department": self.city["roles"].get(role, role)})
        return {"filed": True, "department": self.city["roles"].get(role, role), "zone": self.zone["name"]}

    def rights(self) -> dict:
        r = self.city.get("rights") or {}
        self.cards.append({"type": "rights", "text": r.get(self.lang) or r.get("en", "")})
        return {"rights": r.get("en", "")}


SYSTEM = """You are Saathi (साथी), a heat-safety companion for outdoor workers in Indian cities - construction
workers, delivery riders, street vendors. Many have little schooling and listen rather than read.
Rules:
- Reply in {lang_name} only, in 1-3 short spoken sentences. No markdown, no lists, no emojis. Use simple words.
- Always use the tools for facts (risk, places, schedules). Never invent temperatures, places or distances.
- If the person mentions a problem at their site (no water, no shade, work not stopped, someone unwell),
  call file_report so the right officer gets it, then tell them it was sent and to whom.
- If someone feels unwell, tell them to stop, rest in shade and drink water; for fainting, vomiting or confusion: call 112.
- Rest time: tell them their right to rest (call workers_rights) when they worry about pay or the contractor.
- The cards with maps and schedules are shown next to your reply; don't read out long lists - mention the nearest one.
The worker is in {zone}, {city}.{new}"""


def _agent_reply(sess: Session, text: str) -> str:
    from strands import Agent, tool

    from handlers.agent import make_model

    @tool
    def todays_heat_risk() -> dict:
        """Heat risk for the worker's area today and right now: band, danger window, what to do this hour."""
        return sess.risk_now()

    @tool
    def nearest_help(kind: str = "any") -> dict:
        """Nearest drinking water, shade/cooling places and clinics with walking minutes.

        Args:
            kind: "water", "cool" (shade / cooling place), "health", or "any"
        """
        return sess.nearest_help(kind)

    @tool
    def safe_shift_plan(hours: float = 8) -> dict:
        """When to work today so a given number of heavy-work hours fits into the safe hours.

        Args:
            hours: hours of heavy work needed today
        """
        return sess.shift_plan(hours)

    @tool
    def file_report(kind: str) -> dict:
        """Send a problem at the worker's site to the responsible officer.

        Args:
            kind: "water" (no drinking water), "shade" (no shade), "work" (heavy work not stopped), "ill" (someone unwell)
        """
        return sess.file_report(kind)

    @tool
    def workers_rights() -> dict:
        """The worker's right to rest / paid breaks in heat in this city."""
        return sess.rights()

    lang_name = "Hindi (Devanagari script)" if sess.lang == "hi" else "simple English"
    system = SYSTEM.format(lang_name=lang_name, zone=sess.zone["name"], city=sess.city["name"],
                           new=" They are new to this work, so stricter limits apply." if sess.new_worker else "")
    agent = Agent(model=make_model(temperature=0.3), system_prompt=system, callback_handler=None,
                  tools=[todays_heat_risk, nearest_help, safe_shift_plan, file_report, workers_rights])
    return str(agent(text)).strip()


INTENTS = [
    ("report_water", ["पानी नहीं", "no water", "water not", "पीने का पानी नहीं"]),
    ("report_shade", ["छाँव नहीं", "छांव नहीं", "no shade"]),
    ("report_work", ["काम नहीं रोका", "काम नहीं रुका", "not stopped", "forcing", "ज़बरदस्ती"]),
    ("water", ["पानी", "water", "drink", "प्यास"]),
    ("cool", ["छाँव", "छांव", "shade", "ठंडी", "cool", "rest place", "आराम की जगह"]),
    ("health", ["डॉक्टर", "अस्पताल", "clinic", "hospital", "doctor", "दवा"]),
    ("shift", ["कब काम", "शिफ्ट", "shift", "schedule", "when can i work", "कितने घंटे", "hours"]),
    ("rights", ["पैसे", "मज़दूरी", "मजदूरी", "pay", "wage", "salary", "ठेकेदार", "contractor", "अधिकार", "right"]),
]


def _fallback(sess: Session, text: str) -> str:
    t, hi = text.lower(), sess.lang == "hi"
    intent = next((name for name, keys in INTENTS if any(k in t for k in keys)), "risk")
    if intent.startswith("report_"):
        r = sess.file_report(intent.split("_")[1])
        if not r["filed"]:
            return "यह शिकायत अभी-अभी भेजी जा चुकी है।" if hi else "That was already reported in the last half hour."
        return (f"आपकी शिकायत {r['department']} को भेज दी गई है।" if hi else f"Sent to {r['department']} for {r['zone']}.")
    if intent in ("water", "cool", "health"):
        res = sess.nearest_help(intent)
        lst = res["places"].get(intent) or []
        if not lst:
            return "पास में कुछ नहीं मिला।" if hi else "Nothing found nearby."
        p = lst[0]
        return (f"सबसे पास: {p['name']}, लगभग {p['walk_min']} मिनट पैदल।" + (" यह दूर है, पहले पास की छाँव लें।" if p["too_far"] else "")
                if hi else f"Nearest: {p['name']}, about {p['walk_min']} minutes' walk." + (" That's far in this heat, find nearer shade first." if p["too_far"] else ""))
    if intent == "shift":
        r = sess.shift_plan(8)
        if r.get("available") is False:
            return "अभी जानकारी नहीं है।" if hi else "No forecast yet."
        return (f"आज भारी काम {r['start']} से {r['finish']} के बीच, हर घंटे के आराम के साथ करें।" if hi
                else f"Do today's heavy work between {r['start']} and {r['finish']}, with the rest shown each hour.")
    if intent == "rights":
        return sess.rights()["rights"] if not hi else (sess.city.get("rights") or {}).get("hi", "")
    r = sess.risk_now()
    if not r.get("available", True):
        return "अभी जानकारी नहीं है।" if hi else "No forecast yet."
    b = BANDS[r["today_band"]]
    if r["today_band"] == 0:
        return "आज गर्मी का खतरा कम है। पानी पीते रहें।" if hi else "Low heat risk today. Keep drinking water."
    win = f"{r['danger_window'][0]}–{r['danger_window'][1]}"
    if hi:
        return f"आज {b['label_hi']}। {win} के बीच " + ("भारी काम बंद रखें।" if r["today_band"] >= 4 else f"हर घंटे {b['work']} मिनट काम, {b['rest']} मिनट छाँव में आराम।")
    return f"Today: {b['label']}. Between {win}, " + ("stop heavy work." if r["today_band"] >= 4 else f"work {b['work']} minutes and rest {b['rest']} in shade each hour.")


def reply(st, text: str, city: str, zone: str | None = None, lang: str = "hi", lat=None, lon=None,
          new_worker: bool = False, chat_id: str = "web", use_model: bool = True) -> dict:
    lang = "en" if lang == "en" else "hi"
    text = re.sub(r"\s+", " ", (text or "")).strip()[:500]
    sess = Session(st, city, zone, lang, lat, lon, new_worker, chat_id)
    level = triage(text)
    if level == "emergency":  # fixed rules, never the model
        sess.file_report("ill")
        e = EMERGENCY[lang]
        return {"reply": f"{e['title']}. {e['call']}. " + " ".join(e["steps"]), "emergency": True,
                "cards": [{"type": "emergency", **e}] + sess.cards, "engine": "triage"}
    engine_used = "fallback"
    answer = ""
    if use_model:
        try:
            answer = _agent_reply(sess, text)
            engine_used = "agent"
        except Exception as e:  # no model / network: the rules still answer
            print(f"saathi agent unavailable: {e!r}")
            sess.cards = []
    if not answer:
        answer = _fallback(sess, text)
    if level == "warning":
        answer = WARN[lang] + " " + answer
        sess.cards.insert(0, {"type": "warning", "text": WARN[lang]})
    return {"reply": answer, "emergency": False, "cards": sess.cards, "engine": engine_used,
            "zone": sess.zone, "new_worker": new_worker}
