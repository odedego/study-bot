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


class Permanent(Exception):
    """שגיאה שאין טעם לנסות שוב (4xx)."""


def _retry(fn, tries=3, wait=3):
    """מנסה שוב רק על תקלות רשת, 5xx ו-429. לא ישן אחרי הניסיון האחרון."""
    for i in range(tries):
        try:
            return fn()
        except Permanent:
            raise
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(wait * (i + 1))


def _check(r, what):
    if r.status_code == 429:
        try:
            time.sleep(min(30, int(r.json().get("parameters", {}).get("retry_after", 5))))
        except Exception:  # noqa: BLE001
            time.sleep(5)
        raise RuntimeError(f"{what}: 429")
    if 400 <= r.status_code < 500:
        try:
            reason = r.json().get("description") or r.json().get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            reason = ""
        raise Permanent(f"{what}: {r.status_code} {str(reason)[:120]}")
    if r.status_code >= 500:
        raise RuntimeError(f"{what}: {r.status_code}")


# ---------------------------------------------------------------- Telegram
class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id) if chat_id else ""

    def _safe(self, e: Exception) -> RuntimeError:
        """שגיאות רשת של requests כוללות את ה-URL, ובו הטוקן. הלוגים של repo ציבורי גלויים, אז מוחקים אותו."""
        return RuntimeError(str(e).replace(self.token, "***") if self.token else str(e))

    def _call(self, method: str, **params):
        def go():
            try:
                r = requests.post(f"{self.base}/{method}", json=params, timeout=TIMEOUT)
            except requests.RequestException as e:
                raise self._safe(e) from None
            _check(r, f"telegram {method}")
            return r.json()["result"]
        return _retry(go, tries=1 if method == "answerCallbackQuery" else 3)

    def download(self, file_id: str) -> bytes:
        """קובץ ששלחו לבוט (תמונה / PDF). טלגרם מאפשר עד 20MB."""
        path = self._call("getFile", file_id=file_id)["file_path"]

        def go():
            try:
                r = requests.get(f"https://api.telegram.org/file/bot{self.token}/{path}", timeout=60)
            except requests.RequestException as e:
                raise self._safe(e) from None
            _check(r, "telegram file")
            return r.content
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
        except Permanent as e:
            if "not modified" not in str(e):
                raise

    def edit_buttons(self, message_id: int, buttons):
        try:
            self._call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=message_id,
                       reply_markup=self._markup(buttons) or {"inline_keyboard": []})
        except Permanent as e:
            if "not modified" not in str(e):
                raise

    def answer(self, callback_id: str, text: str = ""):
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text[:190])
        except Exception:  # noqa: BLE001
            pass  # "query is too old" כשהבוט רץ כל כמה דקות: לא חשוב


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
            _check(r, f"calendar {method}")
            return r.json() if r.text else {}
        return _retry(go)

    def get(self, cal_id: str, event_id: str) -> dict | None:
        """None אם האירוע נמחק באמת (404/410/cancelled)."""
        try:
            e = self._req("GET", f"{self.API}/calendars/{requests.utils.quote(cal_id)}/events/{event_id}")
        except Permanent as ex:
            if " 404" in str(ex) or " 410" in str(ex):
                return None
            raise
        return None if e.get("status") == "cancelled" else e

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
        """עם id קבוע מראש: יצירה כפולה מחזירה 409, ואז זה נחשב הצלחה (בלי כפילויות)."""
        try:
            return self._req("POST", f"{self.API}/calendars/{requests.utils.quote(cal_id)}/events", json=body)
        except Permanent as ex:
            if " 409" in str(ex) and body.get("id"):
                return {"id": body["id"], "duplicate": True}
            raise

    def access_role(self, cal_id: str) -> str:
        """ההרשאה של חשבון השירות ליומן (reader / writer / owner). מוסיף את היומן לרשימה של חשבון השירות
        עצמו אם צריך: זה לא משנה כלום אצל עודד."""
        url = f"{self.API}/users/me/calendarList/{requests.utils.quote(cal_id, safe='')}"
        try:
            return self._req("GET", url).get("accessRole", "")
        except Permanent as ex:
            if " 404" not in str(ex):
                raise
        return self._req("POST", f"{self.API}/users/me/calendarList", json={"id": cal_id}).get("accessRole", "")

    def create_calendar(self, summary: str) -> str:
        """יומן חדש בחשבון השירות (למשל "שיעורים"). מחזיר את ה-id שלו."""
        return self._req("POST", f"{self.API}/calendars", json={"summary": summary, "timeZone": "Asia/Jerusalem"})["id"]

    def share(self, cal_id: str, email: str, role: str = "reader"):
        """משתף יומן של חשבון השירות עם עודד. Google שולח לו מייל עם כפתור הוספה."""
        return self._req("POST", f"{self.API}/calendars/{requests.utils.quote(cal_id, safe='')}/acl",
                         json={"role": role, "scope": {"type": "user", "value": email}})

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
            _check(r, "feed")
            text = r.content.decode("utf-8", errors="replace")
            if "BEGIN:VCALENDAR" not in text:
                raise Permanent("הפיד לא החזיר יומן")
            return text
        return _retry(go)


# ---------------------------------------------------------------- State store
class Upstash:
    """מצב הבוט נשמר כמפתח אחד ב-Upstash Redis (REST)."""
    KEY = "study-bot:state"

    def __init__(self, url: str, token: str):
        self.url = self.clean(url).rstrip("/")
        if self.url and not self.url.startswith("http"):
            self.url = "https://" + self.url
        self.h = {"Authorization": f"Bearer {self.clean(token)}"}

    @staticmethod
    def clean(v: str) -> str:
        """סלחני להדבקה: מקבל גם 'UPSTASH_REDIS_REST_URL="https://..."' או ערך עם מרכאות."""
        v = (v or "").strip()
        if v.startswith("UPSTASH_") and "=" in v:
            v = v.split("=", 1)[1]
        return v.strip().strip('"').strip("'").strip()

    def load(self) -> dict | None:
        def go():
            r = requests.post(self.url, headers=self.h, json=["GET", self.KEY], timeout=TIMEOUT)
            _check(r, "upstash")
            return r.json().get("result")
        v = _retry(go)
        return json.loads(v) if v else None

    def save(self, state: dict):
        body = json.dumps(state, ensure_ascii=False)

        def go():
            r = requests.post(self.url, headers=self.h, json=["SET", self.KEY, body], timeout=TIMEOUT)
            _check(r, "upstash")
            # גיבוי מתגלגל: עותק לכל יום, נשמר 30 יום
            day = state.get("updated", "")[:10]
            if day:
                requests.post(self.url, headers=self.h, json=["SET", f"{self.KEY}:{day}", body, "EX", 2592000], timeout=TIMEOUT)
        _retry(go)


    MAIL = "study-bot:mail"

    def _cmd(self, *cmd):
        def go():
            r = requests.post(self.url, headers=self.h, json=list(cmd), timeout=TIMEOUT)
            _check(r, "upstash")
            return r.json().get("result")
        return _retry(go)

    def mail_read(self) -> list[str]:
        """מיילים שסקריפט ה-Apps Script דחף (JSON לכל מייל), מהישן לחדש."""
        return self._cmd("LRANGE", self.MAIL, 0, -1) or []

    def mail_trim(self, n: int):
        """מוחק את n הראשונים (אלה שטופלו). מיילים שנדחפו בינתיים נשארים."""
        if n > 0:
            self._cmd("LTRIM", self.MAIL, n, -1)


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

    mail: list = []

    def mail_read(self):
        return list(self.mail)

    def mail_trim(self, n):
        self.mail = self.mail[n:]


# ---------------------------------------------------------------- LLM (optional)
class Claude:
    """פענוח הודעות חופשיות לפעולות. אופציונלי: בלי מפתח הבוט עובד עם כפתורים ופקודות."""

    def __init__(self, api_key: str, model: str):
        self.key, self.model = api_key, model

    def complete(self, system: str, user: str, max_tokens: int = 800, media: dict | None = None) -> str:
        """media = {"mime": "image/jpeg" | ... | "application/pdf", "data": base64}: תמונה או PDF לפני הטקסט."""
        content: list | str = user
        if media:
            kind = "document" if media["mime"] == "application/pdf" else "image"
            content = [{"type": kind, "source": {"type": "base64", "media_type": media["mime"], "data": media["data"]}},
                       {"type": "text", "text": user}]

        def go():
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=90 if media else 60, headers={
                "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": self.model, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": content}]})
            _check(r, "claude")
            return "".join(b.get("text", "") for b in r.json().get("content", []))
        return _retry(go, tries=2)
