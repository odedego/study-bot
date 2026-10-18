"""
משימות פתוחות: דברים שצריך לעשות ואינם שיעור או מטלה של קורס (להגיש טופס, להירשם למלגה, לשלם...).
כל משימה: שם, דדליין אופציונלי, הערכת זמן אופציונלית. הן מופיעות בהודעת הבוקר ובסיכום השבועי, עם כפתור סגירה.

  - דירוג אוטומטי לפי דדליין וגודל. רחוק מהדדליין המשימה מופיעה רק בימי ראשון ורביעי; 7 ימים לפני ומעלה כל בוקר,
    ויומיים לפני (או באיחור) היא מסומנת 🔴.
  - משימה עם הערכת זמן של שעה ומעלה ודדליין: הצעה לבלוק עבודה ביומן האישי, רק באישור (כמו כל שינוי ביומן האישי).
  - משימה שמגיעה ממייל או מתמונה נשלחת כהצעה עם כפתור "הוסף", ולא נרשמת לבד.
  - משימה שדדליין שלה נדחה פעמיים: שואלים אם היא עדיין רלוונטית.
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import date, datetime, timedelta

import planner as P
from bot import semester as SEM

TASK_ACTIONS = ("task", "task_done", "task_due", "task_time", "task_drop")
MAX_OPEN = 60
MORNING_LINES = 8
MORNING_BUTTONS = 6
FAR_DAYS = 7                 # מעבר לזה: רק בימים של FAR_WEEKDAYS
FAR_WEEKDAYS = (6, 2)        # ראשון, רביעי (weekday של פייתון)
RED_DAYS = 2                 # עד כמה ימים לפני הדדליין: 🔴
TP_TTL_H = 72                # הצעת משימה ממייל/תמונה שלא אושרה פגה אחרי 3 ימים
KEEP_DONE_DAYS = 14


def _tid(title: str, at: str) -> str:
    return "t" + hashlib.sha1(f"{title}|{at}".encode()).hexdigest()[:6]


def _norm(s: str) -> str:
    return re.sub(r"[^\w]", "", (s or "").lower())


def _dd(d: date) -> str:
    return f"{P.HEB_DAY[d.weekday()]} {d.day}.{d.month}"


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else f"{n} {many}"


class TasksMixin:
    # ------------------------------------------------------------ data
    def tasks(self) -> list[dict]:
        return self.st.setdefault("tasks", [])

    def open_tasks(self) -> list[dict]:
        return [t for t in self.tasks() if t.get("status") == "open"]

    def find_task(self, tid) -> dict | None:
        return next((t for t in self.tasks() if t.get("id") == tid), None)

    def _task_by_id(self, tid) -> dict:
        t = self.find_task(tid)
        if not t or t.get("status") != "open":
            raise ValueError("משימה לא ידועה (או שכבר נסגרה)")
        return t

    @staticmethod
    def _due_of(t: dict) -> date | None:
        try:
            return date.fromisoformat(t["due"][:10]) if t.get("due") else None
        except ValueError:
            return None

    def tasks_for_llm(self) -> list[dict]:
        from bot.core import clean_title
        return [{"id": t["id"], "title": clean_title(t.get("title")), "due": t.get("due"), "est_min": t.get("est_min")}
                for t in self.open_tasks()]

    # ------------------------------------------------------------ add / close / change
    def add_task(self, title, due=None, est_min=None, source: str = "") -> str:
        from bot.core import clean_title
        title = clean_title(title, 80)
        if not title:
            raise ValueError("חסר שם למשימה")
        d = None
        if due:
            d = date.fromisoformat(str(due)[:10])
            if d < self.now.date():
                raise ValueError("התאריך כבר עבר")
        est = int(est_min) if est_min not in (None, "", 0) else None
        for t in self.open_tasks():
            if _norm(t["title"]) == _norm(title):
                if d and not t.get("due"):
                    t["due"] = d.isoformat()
                    return f"📌 המשימה כבר רשומה. הוספתי לה דדליין: {_dd(d)}."
                return f"📌 המשימה כבר רשומה: {t['title']}"
        if len(self.open_tasks()) >= MAX_OPEN:
            raise ValueError("יש כבר המון משימות פתוחות. סגור כמה לפני שמוסיפים")
        t = {"id": _tid(title, P.iso(self.now)), "title": title, "due": d.isoformat() if d else None, "est_min": est,
             "status": "open", "added": P.iso(self.now), "postponed": 0}
        if source:
            t["source"] = source
        self.tasks().append(t)
        out = f"📌 נרשמה משימה: {title}" + (f" · עד {_dd(d)}" if d else " · בלי דדליין. תגיד לי עד מתי ואוסיף")
        note = self.maybe_task_time(t)
        return out + (f"\n{note}" if note else "")

    def close_task(self, tid: str) -> str:
        t = self._task_by_id(tid)
        t["status"], t["done_at"] = "done", P.iso(self.now)
        return f"✅ משימה הושלמה: {t['title']}"

    def reopen_task(self, tid: str) -> str:
        t = self.find_task(tid)
        if not t or t.get("status") != "done":
            return "אין מה לפתוח מחדש"
        t["status"] = "open"
        t.pop("done_at", None)
        return f"↩️ נפתחה מחדש: {t['title']}"

    def drop_task(self, tid: str) -> str:
        t = self._task_by_id(tid)
        t["status"], t["done_at"] = "dropped", P.iso(self.now)
        return f"🗑 המשימה בוטלה: {t['title']}"

    def set_task_due(self, tid: str, due) -> str:
        t = self._task_by_id(tid)
        d = date.fromisoformat(str(due)[:10])
        if d < self.now.date():
            raise ValueError("התאריך כבר עבר")
        old = self._due_of(t)
        t["due"] = d.isoformat()
        out = f"📌 {t['title']} · עד {_dd(d)}"
        if old and d > old:
            t["postponed"] = t.get("postponed", 0) + 1
            if t["postponed"] >= 2:
                out += (f"\n⏳ זו כבר הפעם ה-{t['postponed'] + 1} שהמשימה מתעכבת. עדיין רלוונטית? "
                        "אפשר גם לפרק אותה לצעד קטן, ואני אזכיר רק אותו.")
        if t.get("est_min"):
            t.pop("work_offered", None)
            note = self.maybe_task_time(t)
            if note:
                out += f"\n{note}"
        return out

    def set_task_time(self, tid: str, est_min) -> str:
        t = self._task_by_id(tid)
        t["est_min"] = int(est_min) if est_min not in (None, "", 0) else None
        out = f"📌 {t['title']}: כ-{t['est_min']} דק'" if t["est_min"] else f"📌 {t['title']}: בלי הערכת זמן"
        note = self.maybe_task_time(t)
        return out + (f"\n{note}" if note else "")

    def apply_task_action(self, a: dict) -> str:
        act = a.get("action")
        if act == "task":
            return self.add_task(a.get("title"), a.get("due"), a.get("est_min"))
        if act == "task_done":
            return self.close_task(a.get("id", ""))
        if act == "task_drop":
            return self.drop_task(a.get("id", ""))
        if act == "task_due":
            if not a.get("due"):
                raise ValueError("חסר תאריך")
            return self.set_task_due(a.get("id", ""), a["due"])
        if act == "task_time":
            return self.set_task_time(a.get("id", ""), a.get("est_min"))
        raise ValueError("פעולה לא מוכרת")

    # ------------------------------------------------------------ work block (personal calendar, only after approval)
    def maybe_task_time(self, t: dict) -> str:
        """משימה של שעה ומעלה עם דדליין: עד 2 בלוקי עבודה לפני הדדליין, כהצעה ליומן האישי."""
        from bot.extras import EXTRA_WEEKLY_MAX, PlanBlocked
        est, due = t.get("est_min"), self._due_of(t)
        if not est or not due or int(est) < 60 or t.get("work_offered") or not self.personal_cal:
            return ""
        today = self.now.date()
        last = due - timedelta(days=1)
        if last < today:
            return ""
        n = min(EXTRA_WEEKLY_MAX, math.ceil(int(est) / P.CONFIG["session_min"]))
        held = [(datetime.fromisoformat(p["new_start"]), datetime.fromisoformat(p["new_end"]))
                for p in self.st["bot"]["pp"].values()
                if p.get("state") == "pending" and p.get("op") == "create" and not p.get("all_day") and p.get("new_start")]
        try:
            slots = self.find_slots(n, today + timedelta(days=1) if last > today else today, last, None, budget=False, extra_taken=held)
        except PlanBlocked:
            return (f"📌 אציע זמן עבודה כשאצליח לקרוא את המערכת. כתוב לי שוב את ההערכה "
                    f"(\"{_short(t['title'], 30)} ייקח שעתיים\") עוד מעט.")
        t["work_offered"] = True
        if not slots:
            return f"📌 לא מצאתי זמן עבודה לפני {due.day}.{due.month}. המשימה תופיע בסיכומים."
        sent = 0
        for a, b in slots:
            try:
                self.propose_personal({"op": "create", "title": f"🛠 עבודה על: {t['title']}"[:100], "start": P.iso(a), "end": P.iso(b),
                                       "description": f"בלוק עבודה למשימה \"{t['title']}\" (דדליין {due.day}.{due.month})."})
                sent += 1
            except ValueError as e:
                self.log.append(f"task block: {str(e)[:80]}")
        return f"🛠 שלחתי {_plural(sent, 'הצעה', 'הצעות')} לבלוק עבודה ביומן, לאישור." if sent else ""

    # ------------------------------------------------------------ ranking and display
    def _rank(self, t: dict, today: date):
        d = self._due_of(t)
        if not d:
            return (1, 0, t.get("added", ""))
        slack = (d - today).days - min(int(t.get("est_min") or 0) // 120, 3)   # משימה גדולה קורית "קרובה" יותר
        return (0, slack, t.get("added", ""))

    def task_line(self, t: dict, today: date, absolute: bool = False) -> str:
        """absolute: תאריך במקום "בעוד N ימים" (בסיכום השבועי, שנקרא יום לפני שהשבוע מתחיל)."""
        d = self._due_of(t)
        tail = ""
        if not d:
            mark, tail = "⚪", "בלי דדליין"
        else:
            n = (d - today).days
            if n < 0:
                mark, tail = "🔴", "באיחור " + _plural(-n, "יום", "ימים")
            elif absolute:
                mark, tail = ("🔴" if n <= RED_DAYS else "🟠" if n <= FAR_DAYS else "⚪"), _dd(d)
            elif n == 0:
                mark, tail = "🔴", "היום!"
            elif n == 1:
                mark, tail = "🔴", "מחר"
            elif n <= RED_DAYS:
                mark, tail = "🔴", f"בעוד {n} ימים"
            elif n <= FAR_DAYS:
                mark, tail = "🟠", f"{_dd(d)} (בעוד {n} ימים)"
            else:
                mark, tail = "⚪", _dd(d)
        extra = f" · נדחתה {t['postponed']} פעמים" if t.get("postponed", 0) >= 2 else ""
        return f"{mark} {t['title']} · {tail}{extra}"

    def task_rows(self, ids: list[str]) -> list[list[tuple]]:
        rows = []
        for i in ids:
            t = self.find_task(i)
            if not t:
                continue
            name = _short(t["title"], 22)
            if t.get("status") == "done":
                rows.append([(f"✅ נסגר · {name}", "noop"), ("↩️ בטל", f"tk|undo|{i}")])
            elif t.get("status") == "open":
                rows.append([(f"✅ {name}", f"tk|done|{i}")])
        return rows

    def shown_today(self, today: date) -> list[dict]:
        """מה מופיע בבוקר: קרוב או באיחור כל יום, רחוק או בלי תאריך רק בימי ראשון ורביעי."""
        out = []
        for t in self.open_tasks():
            d = self._due_of(t)
            if (d and (d - today).days <= FAR_DAYS) or today.weekday() in FAR_WEEKDAYS:
                out.append(t)
        return sorted(out, key=lambda t: self._rank(t, today))

    def tasks_morning(self, today: date) -> tuple[list[str], list[str]]:
        """שורות לסעיף בהודעת הבוקר, ומזהי המשימות שיקבלו כפתור סגירה."""
        try:
            shown = self.shown_today(today)
        except Exception as e:  # noqa: BLE001
            self.log.append(f"tasks morning: {type(e).__name__}: {str(e)[:100]}")
            return [], []
        if not shown:
            return [], []
        lines = ["📌 משימות פתוחות:"] + [self.task_line(t, today) for t in shown[:MORNING_LINES]]
        rest = len(self.open_tasks()) - len(shown[:MORNING_LINES])
        if rest > 0:
            lines.append(f"(ועוד {rest}. כתוב \"משימות\" לרשימה המלאה)")
        stale = [t for t in shown if self._due_of(t) and (today - self._due_of(t)).days >= 7]
        if stale:
            lines.append("💬 יש משימות שמתעכבות מאוד. עדיין רלוונטיות? אפשר לכתוב \"תבטל את <שם המשימה>\".")
        return lines, [t["id"] for t in shown[:MORNING_BUTTONS]]

    def tasks_text(self, today: date) -> tuple[str, list[str]]:
        """הפקודה "משימות": כל הרשימה, בלי סינון לפי יום."""
        ts = sorted(self.open_tasks(), key=lambda t: self._rank(t, today))
        if not ts:
            return "📌 אין משימות פתוחות. כדי להוסיף: \"תוסיף משימה: להירשם למלגה, עד 5.11\".", []
        lines = ["📌 משימות פתוחות:"] + [self.task_line(t, today) for t in ts[:15]]
        if len(ts) > 15:
            lines.append(f"(ועוד {len(ts) - 15})")
        return "\n".join(lines), [t["id"] for t in ts[:10]]

    def tasks_weekly(self, today: date) -> list[str]:
        """בסיכום השבועי (מוצ״ש): מה נסגר, מה באיחור, מה מגיע בשבוע הבא, ואזהרת עומס."""
        nxt = today + timedelta(days=1)
        end = nxt + timedelta(days=6)
        ws = P.week_start(today)
        done = [t for t in self.tasks() if t.get("status") == "done" and (t.get("done_at") or "")[:10] >= ws.isoformat()]
        opn = sorted(self.open_tasks(), key=lambda t: self._rank(t, today))
        if not done and not opn:
            return []
        lines = ["📌 משימות:"]
        if done:
            lines.append(f"✅ נסגרו השבוע: {len(done)}" + (" (" + ", ".join(_short(t["title"], 24) for t in done[:3]) + ")" if len(done) <= 3 else ""))
        soon = [t for t in opn if self._due_of(t) and self._due_of(t) <= end]
        later = [t for t in opn if t not in soon]
        lines += [self.task_line(t, today, True) for t in soon[:8]]
        if len(soon) > 8:
            lines.append(f"(ועוד {len(soon) - 8} עם דדליין בשבוע הבא)")
        if later:
            nodate = sum(1 for t in later if not self._due_of(t))
            lines.append(f"ועוד {len(later)} פתוחות" + (f" ({nodate} בלי דדליין)" if nodate else "") + ". כתוב \"משימות\" לרשימה.")
        in_week = [t for t in soon if self._due_of(t) >= nxt]
        if len(in_week) >= 3:
            exams = [x for x in SEM.upcoming_exams(today) if x[2] <= 14]
            lines.append(f"⚠️ {len(in_week)} משימות עם דדליין בשבוע הבא" + (" ובדרך מבחן. כדאי לסיים חלק מהן מוקדם." if exams else ". כדאי להתחיל מוקדם."))
        lines.append("לסגירה: כתוב \"סיימתי עם <שם המשימה>\" או לחץ ✅ בהודעת הבוקר.")
        return lines

    def send_tasks(self, today: date):
        text, ids = self.tasks_text(today)
        mid = self.tg.send(text, self.task_rows(ids) or None)
        if ids:
            self.st["bot"]["msgs"][str(mid)] = {"type": "tasks", "base": [], "tasks": ids}

    def register_task_buttons(self, mid, base_rows: list, ids: list[str]):
        if ids:
            self.st["bot"]["msgs"][str(mid)] = {"type": "tasks", "base": [[list(b) for b in r] for r in base_rows], "tasks": ids}

    def refresh_task_buttons(self, mid):
        meta = self.st["bot"]["msgs"].get(str(mid), {})
        if meta.get("type") != "tasks":
            return
        rows = [[tuple(b) for b in r] for r in meta.get("base", [])] + self.task_rows(meta.get("tasks", []))
        self.tg.edit_buttons(mid, rows)

    # ------------------------------------------------------------ suggestions (mail / photo): only after a button press
    def suggest_task(self, a: dict, source: str = "") -> str:
        from bot.core import clean_title
        title = clean_title(a.get("title"), 80)
        if not title:
            raise ValueError("חסר שם למשימה")
        d = None
        if a.get("due"):
            d = date.fromisoformat(str(a["due"])[:10])
            if d < self.now.date():
                return ""
        if any(_norm(t["title"]) == _norm(title) for t in self.open_tasks()):
            return ""
        tp = self.st["bot"].setdefault("tp", {})
        if any(p.get("state") == "pending" and _norm(p["title"]) == _norm(title) for p in tp.values()):
            return ""
        est = int(a["est_min"]) if a.get("est_min") else None
        tid = _tid(title, P.iso(self.now))
        tp[tid] = {"title": title, "due": d.isoformat() if d else None, "est_min": est, "source": source,
                   "state": "pending", "at": P.iso(self.now)}
        self.deferred(lambda: self._send_tp(tid))
        return ""

    def _tp_text(self, p: dict) -> str:
        d = date.fromisoformat(p["due"]) if p.get("due") else None
        return "📌 להוסיף למשימות?\n" + p["title"] + (f" · עד {_dd(d)}" if d else "") + (f"\n(מ{p['source']})" if p.get("source") else "")

    def _send_tp(self, tid: str):
        p = self.st["bot"]["tp"][tid]
        p["send_try"] = p.get("send_try", 0) + 1
        p["mid"] = self.tg.send(self._tp_text(p), [[("➕ הוסף", f"tk|add|{tid}"), ("✖️ לא", f"tk|no|{tid}")]])

    def answer_task_proposal(self, act: str, tid: str) -> str:
        p = self.st["bot"].get("tp", {}).get(tid)
        if not p or p.get("state") != "pending":
            return "כבר טופל"
        if self.now - datetime.fromisoformat(p["at"]) > timedelta(hours=TP_TTL_H):
            p["state"] = "expired"
            if p.get("mid"):
                self.tg.edit(p["mid"], self._tp_text(p) + "\n\n⌛ פג תוקף. כתוב לי אם עדיין רלוונטי.")
            return "פג תוקף"
        if act == "no":
            p["state"] = "cancelled"
            if p.get("mid"):
                self.tg.edit(p["mid"], self._tp_text(p) + "\n\n✖️ לא נוסף.")
            return "בסדר"
        try:
            msg = self.add_task(p["title"], p.get("due"), p.get("est_min"), source=p.get("source", ""))
        except ValueError as e:
            p["state"] = "cancelled"
            if p.get("mid"):
                self.tg.edit(p["mid"], self._tp_text(p) + f"\n\n⚠️ לא נוסף: {str(e)[:80]}")
            return "לא נוסף"
        p["state"] = "done"
        if p.get("mid"):
            self.tg.edit(p["mid"], self._tp_text(p) + "\n\n✅ נוסף למשימות.")
        if "\n" in msg:
            self.tg.send(msg.split("\n", 1)[1])      # למשל: נשלחה הצעה לבלוק עבודה
        return "✅ נוסף"
