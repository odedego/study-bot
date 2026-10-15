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
    def get_updates(self, offset):
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

    def create(self, cal, body):
        eid = f"ev{next(self._id)}"
        ev = dict(copy.deepcopy(body), id=eid)
        self.add(cal, ev)
        self.calls.append(("create", cal, body["summary"]))
        return ev

    def patch(self, cal, eid, body):
        self.cals[cal][eid].update(copy.deepcopy(body))
        self.calls.append(("patch", cal, eid))
        return self.cals[cal][eid]

    def delete(self, cal, eid):
        self.cals.get(cal, {}).pop(eid, None)


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


class FakeLLM:
    def __init__(self):
        self.calls = []
        self.script = []  # רשימת תשובות JSON לפי הסדר

    def complete(self, system, user, max_tokens=800):
        self.calls.append(user)
        return json.dumps(self.script.pop(0) if self.script else {"actions": [], "reply": "קיבלתי"}, ensure_ascii=False)
