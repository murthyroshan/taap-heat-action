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


def provider() -> str:
    return os.environ.get("MODEL_PROVIDER", "bedrock").lower()


def make_model(temperature: float = 0.2):
    """Amazon Bedrock when deployed; a local Ollama model in the no-AWS-account ("Build It") mode."""
    if provider() == "ollama":
        from strands.models.ollama import OllamaModel
        return OllamaModel(os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
                           model_id=os.environ.get("OLLAMA_MODEL", "llama3.2:3b"), temperature=temperature)
    from strands.models import BedrockModel
    return BedrockModel(model_id=os.environ.get("MODEL_ID", "apac.amazon.nova-lite-v1:0"),
                        region_name=os.environ.get("AWS_REGION", "ap-south-1"), temperature=temperature)


def answer(question: str, city: str, st=None) -> str:
    from strands import Agent

    st = st or store.get_store()
    agent = Agent(model=make_model(), system_prompt=SYSTEM + f"\nThe officer's city is '{city}'.",
                  tools=_tools(st), callback_handler=None)
    return str(agent(question)).strip()


IMPORT_PROMPT = """You are converting an Indian city Heat Action Plan (attached PDF) into a machine-readable config.
Return ONLY one JSON object, no prose, no code fences, with exactly these keys:
{
  "id": short lowercase city id (letters only),
  "name": city name, "name_hi": city name in Hindi (Devanagari),
  "plan_name": the plan's title and issuing body,
  "tiers": {"yellow": number, "orange": number, "red": number}   (forecast max temperature thresholds in deg C that trigger each alert level, as written in the plan),
  "hot_night_c": number (night/minimum temperature the plan treats as dangerous; 30 if the plan gives none),
  "roles": {"<role_id>": "<department name>"}   (3-6 departments the plan assigns duties to; role ids lowercase letters),
  "actions": [ {"id": short lowercase letters, "role": one of the role ids,
                "when": {"tier": "yellow"|"orange"|"red"} or {"band": 1-4},
                "text": one concrete instruction for a zone; may use {zone}, {start}, {end} placeholders only,
                "ref": where in the plan this comes from (section or page) } ]   (4-8 of the plan's most important operational actions),
  "notes": [short strings: anything ambiguous, any threshold you inferred rather than read, and missing pieces]
}
Only use thresholds and duties that the plan actually states; if the plan does not state a threshold, put your best reading in "notes" and use IMD heatwave criteria (yellow 40, orange 45, red 47).
"""


def _read_pdf_text(pdf: bytes, max_chars: int = 60000) -> str:
    import io

    from pypdf import PdfReader
    pages = []
    for page in PdfReader(io.BytesIO(pdf)).pages:
        pages.append(f"[page {len(pages) + 1}]\n{page.extract_text() or ''}")
        if sum(len(p) for p in pages) > max_chars:
            break
    return "\n".join(pages)[:max_chars]


def _model_reply(pdf: bytes) -> str:
    if provider() == "ollama":  # local models cannot read PDFs: extract the text first
        from strands import Agent
        agent = Agent(model=make_model(temperature=0.1), callback_handler=None)
        return str(agent(IMPORT_PROMPT.replace("(attached PDF)", "(text below)")
                         + "\n\nPLAN TEXT:\n" + _read_pdf_text(pdf)))
    import boto3
    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
    res = client.converse(
        modelId=os.environ.get("MODEL_ID", "apac.amazon.nova-lite-v1:0"),
        messages=[{"role": "user", "content": [
            {"document": {"format": "pdf", "name": "heat action plan", "source": {"bytes": pdf}}},
            {"text": IMPORT_PROMPT}]}],
        inferenceConfig={"maxTokens": 3000, "temperature": 0.1},
    )
    return "".join(c.get("text", "") for c in res["output"]["message"]["content"])


def import_plan(pdf: bytes) -> dict:
    """Draft a city config from a Heat Action Plan PDF. A human must review it before it goes live."""
    from heatplan.plan import validate_city

    text = _model_reply(pdf)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"error": "The model did not return a config.", "raw": text[:2000]}
    try:
        draft = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        return {"error": f"The model returned invalid JSON ({e.msg}).", "raw": text[:2000]}
    if not isinstance(draft, dict):
        return {"error": "The model did not return a config object.", "raw": text[:2000]}
    draft.setdefault("timezone", "Asia/Kolkata")
    draft.setdefault("zones", [])
    draft.setdefault("alert_min_band", 2)
    draft.setdefault("actions_min_tier", "yellow")
    draft["notes"] = list(draft.get("notes") or []) + [
        "DRAFT extracted automatically from the plan PDF. Verify every threshold and duty against the document, "
        "then add zones (id, name, name_hi, lat, lon) before publishing."]
    return {"draft": draft, "problems": validate_city(draft, require_zones=False)}


def handler(event, context):
    if event.get("task") == "import_plan":
        import base64
        try:
            return import_plan(base64.b64decode(event["pdf_b64"]))
        except Exception as e:
            print(f"import error: {e!r}")
            return {"error": "Could not read the plan with the model (check Bedrock access and that the PDF is under 4.5 MB)."}
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
