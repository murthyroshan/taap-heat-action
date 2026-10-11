"""Run everything on your own machine - no AWS account needed ("Build It" mode).

    python scripts/run_local.py

Starts, in one process:
  * the dashboard + API on http://localhost:8000
  * the Telegram bot (long polling, so no public URL is needed)
  * the hourly plan run (forecast -> WBGT -> actions -> alerts), plus one run at start
  * the officer assistant: a Strands Agents agent on Groq (free API key) or a local Ollama model

Settings come from environment variables or a `.env` file next to this repo:
  TELEGRAM_TOKEN   bot token from @BotFather (optional; without it there is no bot)
  OFFICER_CODE     code officers send as "/officer <code>"
  ADMIN_TOKEN      needed for replay / plan import on the dashboard (default: "local")
  GROQ_API_KEY     free key from console.groq.com -> uses Groq (GROQ_MODEL, default openai/gpt-oss-120b)
  OLLAMA_MODEL     used when there is no GROQ_API_KEY; default llama3.2:3b (`ollama pull llama3.2:3b`)
  LOCAL_DB         SQLite file for state (default: taap.db)
  PORT             default 8000
"""
import base64
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))


def load_env(path=os.path.join(ROOT, ".env")):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
os.environ.setdefault("LOCAL_DB", os.path.join(ROOT, "taap.db"))
os.environ.setdefault("MODEL_PROVIDER", "groq" if os.environ.get("GROQ_API_KEY") else "ollama")
os.environ.setdefault("VOICE_ENABLED", "false")  # Amazon Polly needs an AWS account
os.environ.setdefault("ADMIN_TOKEN", "local")

from handlers import agent as agent_mod  # noqa: E402
from handlers import api  # noqa: E402
from handlers.bot import Bot  # noqa: E402
from handlers.tick import run_city  # noqa: E402
from heatplan import store  # noqa: E402
from heatplan.plan import list_cities  # noqa: E402
from heatplan.telegram import Telegram  # noqa: E402

ST = store.get_store()
TG = Telegram()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---- officer assistant -----------------------------------------------------
def ask_sync(question, city):
    try:
        return agent_mod.answer(question, city, ST)
    except Exception as e:
        log(f"assistant error: {e!r}")
        return "The assistant is unavailable right now (check GROQ_API_KEY, or that Ollama is running)."


def ask_for_bot(question, city, chat_id=None):
    if chat_id is None:
        return ask_sync(question, city)
    threading.Thread(target=lambda: TG.send(chat_id, ask_sync(question, city)), daemon=True).start()
    return None


def import_pdf(pdf_b64):
    try:
        return agent_mod.import_plan(base64.b64decode(pdf_b64))
    except Exception as e:
        log(f"import error: {e!r}")
        if "pdf" in type(e).__module__.lower():
            return {"error": "That file could not be read as a PDF."}
        return {"error": "Could not read the plan with the model (check GROQ_API_KEY, or that Ollama is running)."}


# ---- dashboard / API -------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _go(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        payload = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                payload = {}
        try:
            res = api.route(method, u.path, q, payload, ST, agent=ask_sync, importer=import_pdf)
        except ValueError as e:
            res = {"statusCode": 400, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"error": str(e)})}
        except Exception as e:
            log(f"api error: {e!r}")
            res = {"statusCode": 500, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"error": "internal error"})}
        body = res["body"].encode()
        self.send_response(res["statusCode"])
        for k, v in res["headers"].items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")


# ---- hourly plan run -------------------------------------------------------
def tick_all():
    for c in list_cities():
        try:
            log(f"plan run {c}: {run_city(c, ST)}")
        except Exception as e:
            log(f"plan run {c} failed: {e}")


def scheduler():
    tick_all()
    while True:
        now = datetime.now(timezone.utc)
        nxt = now.replace(minute=5, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(hours=1)
        time.sleep((nxt - now).total_seconds())  # every hour at hh:05
        tick_all()


# ---- Telegram long polling -------------------------------------------------
def poller():
    TG.call("deleteWebhook", {"drop_pending_updates": False})
    TG.call("setMyCommands", {"commands": [
        {"command": "start", "description": "Subscribe to heat alerts"},
        {"command": "new", "description": "New to this work? Stricter limits"},
        {"command": "stop", "description": "Unsubscribe"},
        {"command": "ask", "description": "Officers: ask the plan assistant"}]})
    bot = Bot(ST, TG, agent=ask_for_bot)
    offset = None
    log("Telegram bot is listening")
    while True:
        payload = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        res = TG.call("getUpdates", payload, timeout=35)
        if not res.get("ok"):
            time.sleep(3)
            continue
        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
            try:
                bot.handle(upd)
            except Exception:
                traceback.print_exc()


def main():
    port = int(os.environ.get("PORT", 8000))
    threading.Thread(target=scheduler, daemon=True).start()
    if TG.enabled:
        threading.Thread(target=poller, daemon=True).start()
    else:
        log("No TELEGRAM_TOKEN: running without the bot")
    if not os.environ.get("OFFICER_CODE"):
        log("No OFFICER_CODE set: officers cannot register in the bot")
    log(f"Dashboard: http://localhost:{port}   (admin token: {os.environ['ADMIN_TOKEN']})")
    model = (f"Groq ({os.environ.get('GROQ_MODEL', 'openai/gpt-oss-120b')})" if os.environ["MODEL_PROVIDER"] == "groq"
             else f"Ollama ({os.environ.get('OLLAMA_MODEL', 'llama3.2:3b')})")
    log(f"Assistant: Strands Agents + {model}; state in {os.environ['LOCAL_DB']}")
    try:
        ThreadingHTTPServer(("", port), Handler).serve_forever()
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
