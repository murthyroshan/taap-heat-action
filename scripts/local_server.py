"""Run the API + dashboard locally against an in-memory store (no AWS needed).

    python scripts/local_server.py            # http://localhost:8000
Seeds today's live snapshot for every city from Open-Meteo. Telegram/Polly are off
unless TELEGRAM_TOKEN / VOICE_ENABLED are set.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("VOICE_ENABLED", "false")
os.environ.setdefault("ADMIN_TOKEN", "local")

from handlers import api  # noqa: E402
from handlers.tick import run_city  # noqa: E402
from heatplan import store, weather  # noqa: E402
from heatplan.plan import list_cities  # noqa: E402

ST = store.MemoryStore()
CACHE_DIR = os.environ.get("LOCAL_CACHE_DIR", os.path.join(os.path.dirname(__file__), "..", ".cache"))
_fetch = weather.fetch_forecast_raw


def cached_forecast(zones):
    """Reuse today's forecast between restarts (Open-Meteo rate-limits shared IPs)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"forecast-{zones[0]['id']}-{len(zones)}.json")
    try:
        data = _fetch(zones)
        with open(path, "w") as fh:
            json.dump(data, fh)
        return data
    except weather.WeatherError:
        if os.path.exists(path):
            print(f"using cached forecast {path}")
            with open(path) as fh:
                return json.load(fh)
        raise


weather.fetch_forecast_raw = cached_forecast


class H(BaseHTTPRequestHandler):
    def _go(self, method):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        payload = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
        try:
            res = api.route(method, u.path, q, payload, ST)
        except ValueError as e:
            res = {"statusCode": 400, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"error": str(e)})}
        except Exception as e:
            res = {"statusCode": 500, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"error": repr(e)})}
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


if __name__ == "__main__":
    for c in list_cities():
        try:
            print(run_city(c, ST, notify=False))
        except Exception as e:
            print(f"could not seed {c}: {e}")
    port = int(os.environ.get("PORT", 8000))
    print(f"http://localhost:{port}  (admin token: {os.environ['ADMIN_TOKEN']})")
    ThreadingHTTPServer(("", port), H).serve_forever()
