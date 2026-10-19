"""גרסאות מזויפות של טלגרם, יומן, פיד, אחסון ושעון, לבדיקות בלי רשת."""
from __future__ import annotations

import copy
import itertools
import json
from datetime import datetime

import planner as P

TZ = P.TZ


class Clock:
    def __init__(self, start: datetime):
        self.t = start

    def __call__(self):
        return self.t


def freeze_planner(clock: Clock):
    """planner משתמש ב-datetime.now בכמה מקומות. בבדיקה הזמן הוא של השעון המזויף."""
    class FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.t.astimezone(tz) if tz else clock.t
    P.datetime = FrozenDT


class FakeTelegram:
    def __init__(self, chat_id="42"):
        self.chat_id = chat_id
        self.sent: list[dict] = []
        self.inbox: list[dict] = []
        self.answers: list[str] = []
        self._uid = itertools.count(1)
        self._mid = itertools.count(100)

    # API
    def get_updates(self, offset, timeout=0):
        self.polls = getattr(self, "polls", 0) + 1
        return [u for u in self.inbox if u["update_id"] >= offset]

    def send(self, text, buttons=None, chat_id=None):
        mid = next(self._mid)
        self.sent.append({"id": mid, "text": text, "buttons": buttons, "chat": chat_id or self.chat_id})
        return mid

    def edit(self, mid, text, buttons=None):
        m = self.msg(mid)
        m["text"], m["buttons"] = text, buttons

    def edit_buttons(self, mid, buttons):
        self.msg(mid)["buttons"] = buttons

    def answer(self, cid, text=""):
        self.answers.append(text)

    # helpers for the test
    def msg(self, mid):
        return next(m for m in self.sent if m["id"] == mid)

    files: dict = {}

    def download(self, file_id):
        return self.files[file_id]

    def user_media(self, kind, file_id, data, caption="", chat=None, mime="image/jpeg"):
        """kind = photo / document. photo מגיע כמו בטלגרם: כמה גדלים, הגדול אחרון."""
        self.files = dict(self.files, **{file_id: data})
        msg = {"chat": {"id": int(chat or self.chat_id)}}
        if kind == "photo":
            msg["photo"] = [{"file_id": "thumb", "file_size": 900, "width": 90, "height": 90},
                            {"file_id": file_id, "file_size": len(data), "width": 1280, "height": 960}]
        else:
            msg["document"] = {"file_id": file_id, "file_size": len(data), "mime_type": mime, "file_name": "x"}
        if caption:
            msg["caption"] = caption
        self.inbox.append({"update_id": next(self._uid), "message": msg})

    def user_raw(self, msg, chat=None):
        self.inbox.append({"update_id": next(self._uid), "message": dict(msg, chat={"id": int(chat or self.chat_id)})})

    def user_text(self, text, chat=None):
        self.inbox.append({"update_id": next(self._uid), "message": {"chat": {"id": int(chat or self.chat_id)}, "text": text}})

    def press(self, mid, data):
        self.inbox.append({"update_id": next(self._uid), "callback_query": {
            "id": f"cb{len(self.inbox)}", "data": data,
            "message": {"message_id": mid, "chat": {"id": int(self.chat_id)}}}})

    def find_button(self, mid, pred):
        for row in self.msg(mid)["buttons"] or []:
            for t, d in row:
                if pred(t, d):
                    return d
        return None


class FakeCalendar:
    def __init__(self):
        self.cals: dict[str, dict[str, dict]] = {}
        self._id = itertools.count(1)
        self.fail_next_list = False
        self.calls = []
        self.deleted = set()
        self.trash = {}

    def add(self, cal, ev):
        self.cals.setdefault(cal, {})[ev["id"]] = copy.deepcopy(ev)

    @staticmethod
    def _start(e):
        v = e["start"]
        return datetime.fromisoformat(v.get("dateTime") or v.get("date") + "T00:00:00+03:00")

    def list(self, cal, s, e):
        self.calls.append(("list", cal))
        if self.fail_next_list:
            self.fail_next_list = False
            return []
        a, b = datetime.fromisoformat(s), datetime.fromisoformat(e)
        evs = [copy.deepcopy(x) for x in self.cals.get(cal, {}).values() if a <= self._start(x) < b]
        return sorted(evs, key=self._start)

    def get(self, cal, eid):
        e = self.cals.get(cal, {}).get(eid)
        return copy.deepcopy(e) if e else None

    def create(self, cal, body):
        eid = body.get("id") or f"ev{next(self._id)}"
        if eid in self.cals.get(cal, {}) or eid in self.deleted:
            self.calls.append(("dup", cal, body["summary"]))
            return {"id": eid, "duplicate": True}
        ev = dict(copy.deepcopy(body), id=eid)
        self.add(cal, ev)
        self.calls.append(("create", cal, body["summary"]))
        return ev

    def patch(self, cal, eid, body):
        """כמו ב-Google: אירוע שנמחק נשאר כ"מבוטל", ו-status=confirmed מחזיר אותו."""
        if eid not in self.cals.get(cal, {}):
            gone = self.trash.get(cal, {}).get(eid)
            if gone is None or body.get("status") != "confirmed":
                raise RuntimeError(f"calendar PATCH: 404 {eid}")
            self.cals[cal][eid] = gone
            self.trash[cal].pop(eid)
            self.deleted.discard(eid)
            self.calls.append(("revive", cal, eid))
        body = {k: v for k, v in body.items() if k != "status"}
        self.cals[cal][eid].update(copy.deepcopy(body))
        self.cals[cal][eid]["etag"] = str(int(self.cals[cal][eid].get("etag", "0")) + 1)
        self.calls.append(("patch", cal, eid))
        return self.cals[cal][eid]

    roles: dict = {}
    shares: list = []

    def create_calendar(self, summary):
        cid = f"{summary}@sa"
        self.cals.setdefault(cid, {})
        self.calls.append(("create_calendar", summary))
        return cid

    def share(self, cal, email, role="reader"):
        self.shares = self.shares + [(cal, email, role)]

    def access_role(self, cal):
        return self.roles.get(cal, "writer")

    trash: dict = {}

    def delete(self, cal, eid):
        ev = self.cals.get(cal, {}).pop(eid, None)
        if ev is not None:
            self.trash.setdefault(cal, {})[eid] = ev
        self.deleted.add(eid)


class FakeFeed:
    """בונה ICS מאירועי יומן האוניברסיטה (JSON של Google), כמו שהפיד האמיתי נראה."""
    def __init__(self, google_events: list[dict]):
        self.events = copy.deepcopy(google_events)
        self.broken = False

    def fetch(self):
        if self.broken:
            raise RuntimeError("timeout")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0"]
        for e in self.events:
            if "dateTime" not in e["start"]:
                continue
            s = datetime.fromisoformat(e["start"]["dateTime"]).astimezone(TZ)
            en = datetime.fromisoformat(e["end"]["dateTime"]).astimezone(TZ)
            lines += ["BEGIN:VEVENT", f"UID:{e['id']}",
                      f"DTSTART;TZID=Asia/Jerusalem:{s:%Y%m%dT%H%M%S}", f"DTEND;TZID=Asia/Jerusalem:{en:%Y%m%dT%H%M%S}",
                      f"SUMMARY:{e.get('summary', '')}", f"DESCRIPTION:{(e.get('description') or '')}",
                      f"LOCATION:{e.get('location', '') or ''}", "END:VEVENT"]
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines)


class MemStore:
    """שומר כ-JSON כדי לוודא שהמצב עובר סריאליזציה מלאה בין הרצות."""
    def __init__(self):
        self.blob = None
        self.saves = 0

    def load(self):
        return json.loads(self.blob) if self.blob else None

    def save(self, st):
        self.blob = json.dumps(st, ensure_ascii=False)
        self.saves += 1

    mail: list = []

    def mail_read(self):
        return list(self.mail)

    def mail_trim(self, n):
        self.mail = self.mail[n:]


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.script = []  # רשימת תשובות JSON לפי הסדר

    def complete(self, system, user, max_tokens=800, media=None, tool=None):
        self.calls.append(user)
        self.media = getattr(self, "media", []) + [media]
        self.systems = getattr(self, "systems", []) + [system]
        return json.dumps(self.script.pop(0) if self.script else {"actions": [], "reply": "קיבלתי"}, ensure_ascii=False)
