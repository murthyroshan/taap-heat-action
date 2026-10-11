"""Export a read-only snapshot of the whole site to docs/ for GitHub Pages.

Run it on the machine where Taap runs (it reads the same LOCAL_DB), after a live
plan run and a replay, so the snapshot has real data:

    python scripts/export_static.py            # uses taap.db
    python scripts/export_static.py --refresh  # also runs a live plan run first

Then commit docs/ and enable GitHub Pages: Settings -> Pages -> Branch: master, folder: /docs.
"""
import json
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("LOCAL_DB", os.path.join(ROOT, "taap.db"))
os.environ.pop("OFFICER_CODE", None)  # nothing writable in the snapshot

from handlers import api  # noqa: E402
from heatplan import store  # noqa: E402
from heatplan.plan import list_cities, load_city  # noqa: E402

OUT = os.environ.get("EXPORT_DIR", os.path.join(ROOT, "docs"))
WEB = os.path.join(ROOT, "src", "web")
ST = store.get_store()
KEYS = ("city", "mode", "date", "zone", "lang")


def static_name(path, q):
    """Must match staticPath() in src/web/assets/core.js."""
    parts = [path[len("/api/"):].replace("/", "-")] + [q[k] for k in KEYS if q.get(k)]
    return "_".join(parts) + ".json"


def save(rel, body):
    full = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as fh:
        fh.write(body)


def get(path, q=None, out=None):
    q = q or {}
    res = api.route("GET", path, q, {}, ST)
    if res["statusCode"] != 200:
        print(f"  skip {path} {q}: {res['body'][:120]}")
        return None
    save(out or os.path.join("data", static_name(path, q)), res["body"])
    return json.loads(res["body"]) if res["headers"]["Content-Type"].startswith("application/json") else res["body"]


def main():
    if "--refresh" in sys.argv:
        from handlers.tick import run_city
        for c in list_cities():
            print(run_city(c, ST, notify=False))
    # pages + assets
    os.makedirs(OUT, exist_ok=True)
    shutil.copytree(os.path.join(WEB, "assets"), os.path.join(OUT, "assets"), dirs_exist_ok=True)
    for name in set(api.PAGES.values()):
        with open(os.path.join(WEB, name + ".html"), encoding="utf-8") as fh:
            html = fh.read().replace("<head>", '<head>\n<meta name="taap-static" content="1">', 1)
        save(name + ".html", html)
    save(".nojekyll", "")
    # data
    get("/api/cities"); get("/api/meta")
    for c in list_cities():
        cfg = load_city(c)
        day = cfg["replay"]["default"]
        print(f"{c}:")
        live = get("/api/state", {"city": c, "mode": "live"})
        get("/api/state", {"city": c, "mode": "replay", "date": day})
        get("/api/backtest", {"city": c})
        get("/api/ledger", {"city": c})
        for mode, q in (("live", {}), ("replay", {"date": day})):
            get("/cap.xml", {"city": c, "mode": mode, **q}, out=f"cap-{c}-{mode}.xml")
            for z in cfg["zones"]:
                get("/poster", {"city": c, "mode": mode, "zone": z["id"], **q}, out=f"posters/{c}-{mode}-{z['id']}.html")
        for z in cfg["zones"]:
            for lang in ("hi", "en"):
                get("/api/worker", {"city": c, "zone": z["id"], "lang": lang})
        if not live or not live.get("snapshot"):
            print("  ! no live snapshot - run the server once, or use --refresh")
    print(f"\nWrote {OUT}. Commit it, then GitHub -> Settings -> Pages -> master /docs.")


if __name__ == "__main__":
    main()
