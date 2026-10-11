"""Minimal Telegram Bot API client (stdlib only)."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_TOKEN", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def call(self, method: str, payload: dict) -> dict:
        if not self.enabled:
            print(f"[telegram disabled] {method}: {json.dumps(payload, ensure_ascii=False)[:300]}")
            return {}
        req = urllib.request.Request(API.format(token=self.token, method=method),
                                     data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                body = e.read()[:300]
                if e.code == 429 and attempt == 0:  # rate limited: wait as told, once
                    try:
                        wait = json.loads(body).get("parameters", {}).get("retry_after", 1)
                    except ValueError:
                        wait = 1
                    time.sleep(min(float(wait), 5))
                    continue
                print(f"telegram {method} failed: {e.code} {body!r}")
                return {}
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"telegram {method} unreachable: {e}")
                return {}
        return {}

    def send(self, chat_id, text: str, buttons: list | None = None, keyboard: list | None = None) -> dict:
        payload = {"chat_id": chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        elif keyboard:
            payload["reply_markup"] = {"keyboard": [[{"text": t} for t in row] for row in keyboard],
                                       "resize_keyboard": True, "is_persistent": True}
        return self.call("sendMessage", payload)

    def edit(self, chat_id, message_id, text: str) -> dict:
        return self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text})

    def edit_buttons(self, chat_id, message_id, buttons: list) -> dict:
        return self.call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                                    "reply_markup": {"inline_keyboard": buttons}})

    def answer(self, callback_id: str, text: str = "") -> dict:
        return self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def send_voice(self, chat_id, audio: bytes, caption: str = "") -> dict:
        if not self.enabled:
            print(f"[telegram disabled] sendVoice {len(audio)} bytes")
            return {}
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (("chat_id", str(chat_id)), ("caption", caption[:1000])):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="voice"; filename="alert.mp3"\r\n'
                      'Content-Type: audio/mpeg\r\n\r\n').encode() + audio + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(API.format(token=self.token, method="sendVoice"), data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"telegram sendVoice failed: {e.code} {e.read()[:300]!r}")
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"telegram sendVoice unreachable: {e}")
        return {}
