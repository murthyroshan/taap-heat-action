import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("VOICE_ENABLED", "false")
os.environ.pop("TABLE_NAME", None)
os.environ.pop("TELEGRAM_TOKEN", None)
