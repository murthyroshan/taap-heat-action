from datetime import datetime, timedelta, timezone

NOON_IST = datetime(2026, 10, 11, 7, 0, tzinfo=timezone.utc)  # 12:30 IST


def synthetic_day(day="2024-05-28", tmax=46.0, tmin=33.0, rh_day=20, rh_night=40, sun=900):
    """Hourly UTC rows for the previous evening + the given IST day, shaped like a hot day."""
    rows = []
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc) - timedelta(hours=12)
    for i in range(48):
        t = start + timedelta(hours=i)
        ist_h = (t + timedelta(hours=5, minutes=30)).hour
        frac = max(0.0, 1 - abs(ist_h - 14.5) / 9)  # warmest ~14:30 IST
        temp = tmin + (tmax - tmin) * frac
        rh = rh_night - (rh_night - rh_day) * frac
        light = max(0.0, 1 - abs(ist_h - 12.5) / 6.5)
        rows.append({"time": t, "temp": temp, "rh": rh, "wind": 8, "sw": sun * light,
                     "direct": sun * light * 0.7, "p": 980})
    return rows


class FakeTelegram:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, chat_id, text, buttons=None, keyboard=None):
        self.sent.append(("send", str(chat_id), text, buttons))
        return {"ok": True, "result": {"message_id": len(self.sent)}}

    def send_voice(self, chat_id, audio, caption=""):
        self.sent.append(("voice", str(chat_id), caption, None))

    def edit(self, chat_id, message_id, text):
        self.sent.append(("edit", str(chat_id), text, None))

    def edit_buttons(self, chat_id, message_id, buttons):
        self.sent.append(("buttons", str(chat_id), "", buttons))

    def answer(self, cid, text=""):
        self.sent.append(("answer", cid, text, None))
