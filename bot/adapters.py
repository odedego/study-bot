"""
חיבורים לעולם החיצון. כל מחלקה כאן מוחלפת בגרסה מזויפת בבדיקות (tests/fakes.py),
כך שכל הלוגיקה של הבוט נבדקת בלי רשת.
"""
from __future__ import annotations

import json
import os
import time

import requests

TIMEOUT = 30


def _retry(fn, tries=3, wait=3):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(wait * (i + 1))
    raise last


# ---------------------------------------------------------------- Telegram
class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id) if chat_id else ""

    def _call(self, method: str, **params):
        def go():
            r = requests.post(f"{self.base}/{method}", json=params, timeout=TIMEOUT)
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError(f"telegram {method}: {data.get('description')}")
            return data["result"]
        return _retry(go)

    @staticmethod
    def _markup(buttons):
        if not buttons:
            return None
        return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}

    def get_updates(self, offset: int) -> list[dict]:
        return self._call("getUpdates", offset=offset, timeout=0, allowed_updates=["message", "callback_query"])

    def send(self, text: str, buttons=None, chat_id: str | None = None) -> int:
        p = {"chat_id": chat_id or self.chat_id, "text": text[:4000], "disable_web_page_preview": True}
        m = self._markup(buttons)
        if m:
            p["reply_markup"] = m
        return self._call("sendMessage", **p)["message_id"]

    def edit(self, message_id: int, text: str, buttons=None):
        p = {"chat_id": self.chat_id, "message_id": message_id, "text": text[:4000]}
        m = self._markup(buttons)
        if m:
            p["reply_markup"] = m
        try:
            self._call("editMessageText", **p)
        except RuntimeError as e:
            if "not modified" not in str(e):
                raise

    def edit_buttons(self, message_id: int, buttons):
        try:
            self._call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=message_id,
                       reply_markup=self._markup(buttons) or {"inline_keyboard": []})
        except RuntimeError as e:
            if "not modified" not in str(e):
                raise

    def answer(self, callback_id: str, text: str = ""):
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])
        except RuntimeError:
            pass  # קריאה ישנה מדי: לא חשוב


# ---------------------------------------------------------------- Google Calendar
class Calendar:
    """יומן Google דרך חשבון שירות. היומנים צריכים להיות משותפים עם כתובת חשבון השירות."""
    API = "https://www.googleapis.com/calendar/v3"

    def __init__(self, sa_json: str):
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/calendar"])
        self.s = AuthorizedSession(creds)
        self.email = info.get("client_email", "")

    def _req(self, method, url, **kw):
        def go():
            r = self.s.request(method, url, timeout=TIMEOUT, **kw)
            if r.status_code >= 400:
                raise RuntimeError(f"calendar {method} {r.status_code}: {r.text[:300]}")
            return r.json() if r.text else {}
        return _retry(go)

    def list(self, cal_id: str, start_iso: str, end_iso: str) -> list[dict]:
        out, token = [], None
        while True:
            params = {"timeMin": start_iso, "timeMax": end_iso, "singleEvents": "true",
                      "orderBy": "startTime", "maxResults": 250, "timeZone": "Asia/Jerusalem"}
            if token:
                params["pageToken"] = token
            data = self._req("GET", f"{self.API}/calendars/{requests.utils.quote(cal_id)}/events", params=params)
            out += data.get("items", [])
            token = data.get("nextPageToken")
            if not token:
                return out

    def create(self, cal_id: str, body: dict) -> dict:
        return self._req("POST", f"{self.API}/calendars/{requests.utils.quote(cal_id)}/events", json=body)

    def patch(self, cal_id: str, event_id: str, body: dict) -> dict:
        return self._req("PATCH", f"{self.API}/calendars/{requests.utils.quote(cal_id)}/events/{event_id}", json=body)

    def delete(self, cal_id: str, event_id: str):
        def go():
            r = self.s.request("DELETE", f"{self.API}/calendars/{requests.utils.quote(cal_id)}/events/{event_id}", timeout=TIMEOUT)
            if r.status_code not in (200, 204, 404, 410):
                raise RuntimeError(f"calendar delete {r.status_code}")
        _retry(go)


# ---------------------------------------------------------------- University feed
class Feed:
    def __init__(self, url: str):
        self.url = url

    def fetch(self) -> str:
        def go():
            r = requests.get(self.url, timeout=60, headers={"User-Agent": "Mozilla/5.0 study-bot"})
            r.raise_for_status()
            r.encoding = r.encoding or "utf-8"
            if "BEGIN:VCALENDAR" not in r.text:
                raise RuntimeError("הפיד לא החזיר יומן")
            return r.text
        return _retry(go)


# ---------------------------------------------------------------- State store
class Upstash:
    """מצב הבוט נשמר כמפתח אחד ב-Upstash Redis (REST)."""
    KEY = "study-bot:state"

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.h = {"Authorization": f"Bearer {token}"}

    def load(self) -> dict | None:
        def go():
            r = requests.post(self.url, headers=self.h, json=["GET", self.KEY], timeout=TIMEOUT)
            r.raise_for_status()
            return r.json().get("result")
        v = _retry(go)
        return json.loads(v) if v else None

    def save(self, state: dict):
        body = json.dumps(state, ensure_ascii=False)

        def go():
            r = requests.post(self.url, headers=self.h, json=["SET", self.KEY, body], timeout=TIMEOUT)
            r.raise_for_status()
            # גיבוי מתגלגל: עותק לכל יום, נשמר 30 יום
            day = state.get("updated", "")[:10]
            if day:
                requests.post(self.url, headers=self.h, json=["SET", f"{self.KEY}:{day}", body, "EX", 2592000], timeout=TIMEOUT)
        _retry(go)


class FileStore:
    def __init__(self, path: str):
        self.path = path

    def load(self):
        if not os.path.exists(self.path):
            return None
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def save(self, state):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)


# ---------------------------------------------------------------- LLM (optional)
class Claude:
    """פענוח הודעות חופשיות לפעולות. אופציונלי: בלי מפתח הבוט עובד עם כפתורים ופקודות."""

    def __init__(self, api_key: str, model: str):
        self.key, self.model = api_key, model

    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        def go():
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=60, headers={
                "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": self.model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json().get("content", []))
        return _retry(go, tries=2)
