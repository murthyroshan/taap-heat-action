"""Officer plan assistant: a Strands agent on Amazon Bedrock with read-only tools.

The agent never decides alerts or actions (that is deterministic plan code).
It answers officers' questions from the live snapshot, the action log, worker
reports and the plan text, and must say when data is missing.
"""
from __future__ import annotations

import json
import os

from heatplan import engine, replay, store
from heatplan.plan import BANDS, load_city

SYSTEM = """You are the duty assistant for a city's Heat Action Plan control room in India.
Answer officers' questions using ONLY the tools. Be brief (under 120 words), concrete and operational:
name zones, times (IST), and which actions are open vs done. Quote the plan reference when relevant.
Temperatures are modelled forecast values, not station readings. Never invent data; if a tool
returns nothing, say so. Do not give medical diagnoses; for emergencies say: call 112."""


def _tools(st, mode_default="live"):
    from strands import tool

    @tool
    def zone_risk(city: str, mode: str = mode_default) -> str:
        """Today's heat risk per zone: plan tier, WBGT peak, worker band, risky window, hot-night flag.

        Args:
            city: city id, e.g. "delhi" or "ahmedabad"
            mode: "live" (today) or "replay" (latest replayed day)
        """
        snap = store.load_snapshot(st, city, mode)
        if not snap:
            return "No snapshot available."
        rows = [{k: z.get(k) for k in ("zone", "tier", "tmax", "peak_wbgt", "band", "start", "end", "hot_night")}
                for z in snap["zones"] if z.get("available")]
        return json.dumps({"date": snap["date"], "city_tier": snap["tier"], "zones": rows,
                           "bands": {b["band"]: b["label"] for b in BANDS}})

    @tool
    def plan_actions(city: str, mode: str = mode_default, status: str = "all") -> str:
        """Plan actions triggered for the latest day, with owner role, status and who completed them.

        Args:
            city: city id
            mode: "live" or "replay"
            status: "open", "done" or "all"
        """
        snap = store.load_snapshot(st, city, mode)
        if not snap:
            return "No actions: no snapshot."
        acts = store.list_actions(st, city, mode, snap["date"])
        if status != "all":
            acts = [a for a in acts if a["status"] == status]
        return json.dumps([{k: a.get(k) for k in ("zone_name", "action_id", "role", "status", "done_by",
                                                   "text_rendered", "ref")} for a in acts])

    @tool
    def worker_reports(city: str) -> str:
        """Reports sent by workers today (no water, no shade, work not stopped, someone unwell).

        Args:
            city: city id
        """
        reps = store.list_reports(st, city, engine.today_ist())
        return json.dumps([{k: r.get(k) for k in ("type", "zone_name", "time", "status")} for r in reps])

    @tool
    def plan_text(city: str) -> str:
        """The city's plan configuration: tier thresholds, hot-night rule, actions and references, caveats.

        Args:
            city: city id
        """
        c = load_city(city)
        return json.dumps({k: c[k] for k in ("plan_name", "tiers", "hot_night_c", "actions", "roles", "notes")})

    @tool
    def heatwave_backtest(city: str) -> str:
        """What the plan's rules would have triggered in the city's replayed past heatwave.

        Args:
            city: city id
        """
        bt = replay.backtest(st, city)
        return json.dumps({"summary": bt["summary"], "reference_event": bt["reference_event"], "caveat": bt["caveat"]})

    return [zone_risk, plan_actions, worker_reports, plan_text, heatwave_backtest]


def answer(question: str, city: str, st=None) -> str:
    from strands import Agent
    from strands.models import BedrockModel

    st = st or store.get_store()
    model = BedrockModel(model_id=os.environ.get("MODEL_ID", "apac.amazon.nova-lite-v1:0"),
                         region_name=os.environ.get("AWS_REGION", "ap-south-1"), temperature=0.2)
    agent = Agent(model=model, system_prompt=SYSTEM + f"\nThe officer's city is '{city}'.",
                  tools=_tools(st), callback_handler=None)
    return str(agent(question)).strip()


def handler(event, context):
    question = (event.get("question") or "").strip()[:500]
    city = event.get("city", "delhi")
    if not question:
        return {"error": "question required"}
    try:
        out = {"answer": answer(question, city)}
    except Exception as e:
        print(f"agent error: {e!r}")
        out = {"error": "The assistant is unavailable right now (check Bedrock model access)."}
    if event.get("chat_id") is not None:  # asynchronous call from the Telegram bot
        from heatplan.telegram import Telegram
        Telegram().send(event["chat_id"], out.get("answer") or out["error"])
    return out
