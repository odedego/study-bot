#!/usr/bin/env python3
"""
מנוע השיבוץ של בוט הלימודים (סמסטר א׳, חשבונאות, רייכמן).

דטרמיניסטי: אותו קלט נותן אותו פלט. כל הכללים כאן, לא בשיקול דעת של המודל.
המודל בהרצה המתוזמנת רק אוסף קלט (אירועים מהיומנים, מצב), מריץ את הקובץ הזה,
ומבצע את הפעולות שהוא מחזיר (יצירה/עדכון/מחיקה ביומן "חזרות לימודים").

שימוש (כל פקודה מקבלת --events uni.json --personal p.json --state state.json לפי הצורך):
  plan        --week 2026-10-18 [--human]           תוכנית שבוע
  sync        --week ... --existing reviews.json [--write]   תוכנית + מה ליצור/לעדכן/למחוק ביומן החזרות
  review-data --date 2026-10-19                     נתונים לסקירה היומית
  exceptions  --start 2026-10-15 --days 7 [--human] חריגים קדימה
  day         --date 2026-10-19                     פרופיל יום, שיעורים, סשנים, נקודות מגע
  reschedule  --today 2026-10-20 [--write]          שיבוץ מחדש של מה שפוספס
  mark        --key K --status done|missed|dropped|planned
  recap       --date D --course math --rating 1-5 --topics ... --unclear ...
  assignment  --course C --title T --due D --est 90   |  --id ID --status done
  pin         --key K --start 2026-10-22T09:00
  question    --text ...  |  --id q1 --clear
  log-run     --run morning --ok yes|no --text ...
  week-report --week 2026-10-18                     סיכום שבוע לסקירה השבועית
  set-event   --key K --id EVENT_ID                 קישור סשן לאירוע ביומן
  move        --key K --start 2026-10-22T10:00      הזזת סשן (אחרי שהוזז ביומן)
  adopt       --existing rev.json --start 2026-10-14T00:00 --days 15   קליטת שינויים שעשית ביומן
  exc-notify  --today 2026-10-15 [--write]          חריגים חדשים + תזכורות של יומיים לפני
  exc-ack     --id abc123                           קיבלת החלטה על חריג
  course-kind --course econ --text refresh|cumulative
  init-state  --out state.json
  feed-check  --today 2026-10-15 [--write]          בדיקת תקינות הפיד ושינויים מאז ההרצה הקודמת
  version

קלט אירועים: פלט list_events של Google Calendar (JSON), רשימת אירועים, או קובץ ICS.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Jerusalem")
VERSION = "2026-10-15.5"

# ---------------------------------------------------------------- config
# python weekday(): Mon=0 Tue=1 Wed=2 Thu=3 Fri=4 Sat=5 Sun=6
SUN, MON, TUE, WED, THU, FRI, SAT = 6, 0, 1, 2, 3, 4, 5
HEB_DAY = {SUN: "ראשון", MON: "שני", TUE: "שלישי", WED: "רביעי", THU: "חמישי", FRI: "שישי", SAT: "שבת"}

CONFIG = {
    "session_min": 50,
    "break_min": 15,
    "morning_start": "09:00",
    "evening_start": "20:00",
    "evening_end_limit": "21:30",
    "hard_end": "23:00",                # כלל ברזל: אין לימוד אחרי 23:00 בלי בקשה מפורשת
    "feed_stale_hours": 60,             # סנכרון אחרון ישן מזה = התראה (אין הרצת בוקר בשבת)
    "feed_drop_ratio": 0.6,             # פחות מ-60% מהשיעורים של הפעם הקודמת = חשוד, לא משבצים
    "vanish_confirm_runs": 2,           # שיעור שנעלם בלי סימון ביטול: מאשרים רק אחרי 2 הרצות
    "free_day_end_limit": "19:00",
    "friday_end_limit": "16:00",
    "rest_after_class_min": 60,        # אין שיבוץ צמוד לסוף שיעורים
    "buffer_before_class_min": 60,     # סשן בוקר מסתיים שעה לפני שיעור
    "personal_margin_min": 15,
    "long_day_hours": 4.5,             # יום ארוך: סשן אחד בערב
    "free_day_cap": 4,
    "free_day_with_class_cap": 2,
    "evening_class_day_cap": 2,
    "evening_class_threshold": "17:00",  # שיעור שמסתיים מ-17:00 ואילך = יום לימודי ערב
    "evening_class_weekdays": [THU],
    "free_weekdays": [SUN, TUE, FRI],
    "closed_weekdays": [SAT],
    "saturday_backup_start": "12:00",
    "max_same_course_per_day": 2,
    "day_total_max": 7,                # תקרה כוללת ליום (חזרות + תוספות + מרתון)
    "weekly_review_weekday": SUN,
    "weekly_review_cap": 4,
    "recap_after_min": 20,
    "recap_reminder_after_min": 15,
    "daily_review_time": "21:30",
    "weekly_review_motzash_time": "21:00",
    # שיבוץ מחדש אוטומטי
    "reschedule_max_per_day": 3,
    "reschedule_no_extra_weekdays": [MON, WED],
    "reschedule_total_cap_free_day": 5,
    "courses": [
        # key, טקסט לזיהוי בכותרת, שם תצוגה, דרגת עדיפות, יעד סשנים בשבוע, סוג
        {"key": "math", "match": "מתמטיקה", "name": "מתמטיקה", "tier": 1, "weekly": 3, "kind": "cumulative"},
        {"key": "intro", "match": "מבוא לחשבונאות", "name": "מבוא לחשבונאות", "tier": 1, "weekly": 2, "kind": "cumulative"},
        {"key": "econ", "match": "כלכלה", "name": "כלכלה", "tier": 2, "weekly": 2, "kind": "cumulative"},
        {"key": "stats", "match": "סטטיסטיקה", "name": "סטטיסטיקה", "tier": 2, "weekly": 1, "kind": "cumulative"},
        {"key": "excel", "match": "אקסל", "name": "אקסל", "tier": 2, "weekly": 1, "kind": "cumulative"},
        {"key": "law", "match": "משפט", "name": "משפט", "tier": 3, "weekly": 1, "kind": "cumulative"},
        {"key": "mgmt", "match": "ניהול בארגונים", "name": "ניהול בארגונים", "tier": 3, "weekly": 1, "kind": "cumulative"},
    ],
    "weekly_groups": [["math", "stats"], ["intro", "excel"], ["econ"], ["law", "mgmt"]],
}

COURSES = {c["key"]: c for c in CONFIG["courses"]}
COURSE_ORDER = [c["key"] for c in CONFIG["courses"]]


def hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=TZ)


def fmt_d(d: date) -> str:
    return f"{d.day}.{d.month}"


def fmt_t(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%H:%M")


def iso(dt: datetime) -> str:
    return dt.astimezone(TZ).isoformat(timespec="minutes")


def week_start(d: date) -> date:
    """יום ראשון של השבוע (שבוע ישראלי ראשון–שבת)."""
    return d - timedelta(days=(d.weekday() + 1) % 7)


# ---------------------------------------------------------------- events
@dataclass
class Ev:
    uid: str
    summary: str
    description: str
    location: str
    start: datetime
    end: datetime
    transparent: bool = False
    all_day: bool = False

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def _parse_dt(obj) -> tuple[datetime, bool]:
    if "dateTime" in obj:
        return datetime.fromisoformat(obj["dateTime"]).astimezone(TZ), False
    d = date.fromisoformat(obj["date"])
    return at(d, time(0, 0)), True


def _parse_ics(text: str) -> list[Ev]:
    lines: list[str] = []
    for raw in text.splitlines():
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    out, cur = [], None
    for ln in lines:
        if ln == "BEGIN:VEVENT":
            cur = {}
        elif ln == "END:VEVENT" and cur is not None:
            def dtv(key):
                v = cur.get(key)
                if v is None:
                    return None
                params, val = v
                if "VALUE=DATE" in params and len(val) == 8:
                    return at(datetime.strptime(val, "%Y%m%d").date(), time(0, 0))
                if val.endswith("Z"):
                    return datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
                return datetime.strptime(val, "%Y%m%dT%H%M%S").replace(tzinfo=TZ)
            s, e = dtv("DTSTART"), dtv("DTEND")
            if s and e:
                out.append(Ev(
                    uid=cur.get("UID", ("", ""))[1],
                    summary=cur.get("SUMMARY", ("", ""))[1].replace("\\,", ","),
                    description=cur.get("DESCRIPTION", ("", ""))[1].replace("\\,", ","),
                    location=cur.get("LOCATION", ("", ""))[1].replace("\\,", ","),
                    start=s, end=e,
                    transparent=cur.get("TRANSP", ("", ""))[1] == "TRANSPARENT",
                ))
            cur = None
        elif cur is not None and ":" in ln:
            head, val = ln.split(":", 1)
            name, *params = head.split(";")
            cur[name] = (";".join(params), val)
    return out


def load_events(path: str | None) -> list[Ev]:
    if not path:
        return []
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if text.lstrip().startswith("BEGIN:VCALENDAR"):
        return _parse_ics(text)
    data = json.loads(text)
    items = data.get("events", data.get("items", [])) if isinstance(data, dict) else data
    out = []
    for e in items:
        if e.get("status") == "cancelled" or "start" not in e:
            continue
        s, all_day = _parse_dt(e["start"])
        en, _ = _parse_dt(e["end"])
        out.append(Ev(
            uid=e.get("id", e.get("iCalUID", "")),
            summary=e.get("summary", ""),
            description=e.get("description", "") or "",
            location=e.get("location", "") or "",
            start=s, end=en,
            transparent=e.get("transparency") == "transparent" or e.get("availability") == "AVAILABILITY_FREE",
            all_day=all_day,
        ))
    return out


# ---------------------------------------------------------------- classify
@dataclass
class Lesson:
    course: str
    start: datetime
    end: datetime
    kind: str            # שיעור / תרגול
    location: str
    uids: list[str] = field(default_factory=list)

    @property
    def day(self) -> date:
        return self.start.date()


@dataclass
class Uni:
    lessons: list[Lesson]
    cancelled: list[tuple[str, Ev]]
    no_study_days: dict[date, str]
    exams: list[tuple[str, Ev]]
    unknown: list[Ev]


def course_of(summary: str) -> str | None:
    for c in CONFIG["courses"]:
        if c["match"] in summary:
            return c["key"]
    return None


def classify(events: list[Ev]) -> Uni:
    raw_lessons: list[tuple[str, Ev]] = []
    cancelled, exams, unknown = [], [], []
    no_study: dict[date, str] = {}
    for e in events:
        s = e.summary
        if "אין לימודים" in s:
            no_study[e.start.date()] = s
            continue
        if s.startswith("מבחן"):
            c = course_of(s)
            exams.append((c, e))
            continue
        if "סוג מפגש" not in e.description:
            continue  # שבוע פתיחה, ימי הולדת, טקסים וכו׳
        c = course_of(s)
        if c is None:
            unknown.append(e)
            continue
        if "מבוטל" in s or "בוטל" in e.description:
            cancelled.append((c, e))
            continue
        raw_lessons.append((c, e))
    # איחוד אירועים חופפים של אותו קורס באותו יום (למשל 3.1: שני אירועי ניהול חופפים)
    raw_lessons.sort(key=lambda x: (x[1].start, x[0]))
    lessons: list[Lesson] = []
    for c, e in raw_lessons:
        kind = "תרגול" if "תרגול" in e.description else "שיעור"
        prev = next((l for l in reversed(lessons) if l.course == c and l.day == e.start.date()), None)
        if prev and e.start < prev.end and kind == prev.kind:
            prev.end = max(prev.end, e.end)
            prev.uids.append(e.uid)
            continue
        lessons.append(Lesson(c, e.start, e.end, kind, e.location, [e.uid]))
    return Uni(lessons, cancelled, no_study, exams, unknown)


def regular_pattern(lessons: list[Lesson]) -> dict[str, set[tuple[int, str]]]:
    """לכל קורס: (יום בשבוע, שעת התחלה) שחוזרים לפחות 3 פעמים בסמסטר."""
    cnt: dict[str, Counter] = defaultdict(Counter)
    for l in lessons:
        cnt[l.course][(l.start.weekday(), fmt_t(l.start))] += 1
    return {c: {k for k, n in ctr.items() if n >= 3} for c, ctr in cnt.items()}


# ---------------------------------------------------------------- slots
@dataclass
class Session:
    key: str
    kind: str                 # review / weekly / rescheduled
    course: str | None        # None לחזרה שבועית
    courses: list[str]
    start: datetime
    end: datetime
    lesson_day: date | None = None
    note: str = ""
    label: str = ""          # כותרת לסשנים שאינם חזרה על שיעור (חיזוק, מטלה, מרתון)

    def title(self) -> str:
        if self.kind in LABEL_KINDS:
            return self.label or f"📚 {COURSES[self.course]['name']}"
        if self.kind == "weekly":
            names = " + ".join(COURSES[c]["name"] for c in self.courses)
            return f"🗓️ חזרה שבועית · {names}"
        tag = "השלמה" if (self.kind != "review" or self.note.startswith("השלמה")) else "חזרה"
        return f"📚 {COURSES[self.course]['name']} · {tag} על {fmt_d(self.lesson_day)}"

    def to_json(self) -> dict:
        return {
            "key": self.key, "kind": self.kind, "course": self.course, "courses": self.courses,
            "start": iso(self.start), "end": iso(self.end),
            "lesson_day": self.lesson_day.isoformat() if self.lesson_day else None,
            "title": self.title(),
            "description": self.description(),
            "note": self.note,
            "label": self.label,
        }

    def description(self) -> str:
        lines = []
        if self.kind in LABEL_KINDS:
            pass
        elif self.kind == "weekly":
            lines.append("חזרה מקיפה על החומר של השבוע שעבר, ומטלות פתוחות.")
        else:
            lines.append("כ-30 דקות חזרה על השיעור, והשאר למטלות ותרגול של הקורס.")
        if self.note:
            lines.append(self.note)
        lines.append(f"מזהה: {self.key}")
        return "\n".join(lines)


# סשנים שהבוט מוסיף מעבר לחזרות הרגילות: לא נספרים בתקרה השבועית ולא משובצים מחדש אוטומטית
LABEL_KINDS = ("extra", "assign", "marathon")


def busy_blocks(day: date, uni: Uni, personal: list[Ev]) -> list[tuple[datetime, datetime, str]]:
    m = timedelta(minutes=CONFIG["personal_margin_min"])
    out = [(l.start, l.end, "lesson") for l in uni.lessons if l.day == day]
    for e in personal:
        if e.transparent or e.all_day:
            continue
        if e.start.date() <= day <= e.end.date():
            out.append((e.start - m, e.end + m, "personal"))
    return out


def day_profile(day: date, uni: Uni) -> dict:
    """סוג היום וחלון הזמן לסשנים, לפי הכללים במסמך האפיון."""
    wd = day.weekday()
    ls = sorted((l for l in uni.lessons if l.day == day), key=lambda l: l.start)
    class_hours = sum(l.end.timestamp() - l.start.timestamp() for l in ls) / 3600
    rest = timedelta(minutes=CONFIG["rest_after_class_min"])
    buf = timedelta(minutes=CONFIG["buffer_before_class_min"])
    eve_end = at(day, hm(CONFIG["evening_end_limit"]))

    if wd in CONFIG["closed_weekdays"]:
        return {"type": "closed", "start": None, "end": None, "cap": 0, "class_hours": class_hours}

    if not ls:
        end = at(day, hm(CONFIG["friday_end_limit"] if wd == FRI else CONFIG["free_day_end_limit"]))
        return {"type": "free", "start": at(day, hm(CONFIG["morning_start"])), "end": end,
                "cap": CONFIG["free_day_cap"], "class_hours": 0.0}

    first, last = ls[0].start, max(l.end for l in ls)
    evening_class = last.time() >= hm(CONFIG["evening_class_threshold"]) or wd in CONFIG["evening_class_weekdays"]
    if evening_class:
        return {"type": "evening_class", "start": at(day, hm(CONFIG["morning_start"])), "end": first - buf,
                "cap": CONFIG["evening_class_day_cap"], "class_hours": class_hours}

    if wd in CONFIG["free_weekdays"]:
        # יום חופשי שנכנס אליו שיעור (למשל שלישי 20.10): אחרי השיעור, עם מנוחה
        end = at(day, hm(CONFIG["friday_end_limit"] if wd == FRI else CONFIG["free_day_end_limit"]))
        start = max(last + rest, at(day, hm(CONFIG["morning_start"])))
        start = _round_up(start)
        cap = 1 if class_hours >= CONFIG["long_day_hours"] else CONFIG["free_day_with_class_cap"]
        return {"type": "free_with_class", "start": start, "end": end, "cap": cap, "class_hours": class_hours}

    # יום לימודי בוקר: ערב ב-20:00, סשן אחד
    start = max(at(day, hm(CONFIG["evening_start"])), last + rest)
    return {"type": "class_day", "start": start, "end": eve_end, "cap": 1, "class_hours": class_hours}


def _round_up(dt: datetime, step: int = 15) -> datetime:
    extra = (-dt.minute) % step
    return (dt + timedelta(minutes=extra)).replace(second=0, microsecond=0)


def free_slots(day: date, prof: dict, busy, taken: list[Session], cap: int | None = None) -> list[tuple[datetime, datetime]]:
    """סלוטים פנויים בחלון היום, בקפיצות של 50+15 דק׳, בלי חפיפה לאירועים ולסשנים קיימים."""
    if not prof["start"] or not prof["end"]:
        return []
    cap = prof["cap"] if cap is None else cap
    hard = at(day, hm(CONFIG["hard_end"]))
    if prof["end"] > hard and not prof.get("allow_late"):
        prof = dict(prof, end=hard)
    dur = timedelta(minutes=CONFIG["session_min"])
    step = timedelta(minutes=CONFIG["session_min"] + CONFIG["break_min"])
    brk = timedelta(minutes=CONFIG["break_min"])
    blocks = [(b[0], b[1]) for b in busy] + [(s.start - brk, s.end + brk) for s in taken if s.start.date() == day]
    out, t = [], prof["start"]
    while t + dur <= prof["end"] and len(out) < cap:
        clash = next((b for b in blocks if t < b[1] and b[0] < t + dur), None)
        if clash:
            t = _round_up(max(t + timedelta(minutes=5), clash[1]), 5)
            continue
        out.append((t, t + dur))
        t += step
    return out


# ---------------------------------------------------------------- planning
@dataclass
class Demand:
    course: str
    lesson: Lesson
    unit: int

    @property
    def key(self) -> str:
        return f"review:{self.course}:{self.lesson.day.isoformat()}:{self.lesson.kind}:{self.unit}"

    def sort_key(self):
        return (COURSES[self.course]["tier"], self.lesson.end, COURSE_ORDER.index(self.course), self.unit)


def day_room(st: dict | None, day: date, on_day: int) -> int:
    """כמה עוד סשנים אפשר להוסיף ביום: לפי מה שעודד אמר על היום הזה (st["capacity"]) ותקרה כוללת של 7."""
    lim = CONFIG["day_total_max"]
    cap = (st or {}).get("capacity", {}).get(day.isoformat())
    if cap is not None:
        lim = min(lim, int(cap))
    return max(0, lim - on_day)


def marathon_on(st: dict | None, d: date) -> bool:
    """במרתון אין חזרה שבועית ביום ראשון: סשני המרתון מחליפים אותה."""
    m = (st or {}).get("marathon") or {}
    return bool(m.get("start")) and m.get("status") in ("scheduled", "active") and m["start"] <= d.isoformat() <= m.get("end", "9999")


def target_of(c: str, st: dict | None) -> int:
    """יעד סשנים שבועי לקורס. אחרי הכיול אפשר לשנות לכל קורס (st["weekly_targets"])."""
    v = (st or {}).get("weekly_targets", {}).get(c)
    return COURSES[c]["weekly"] if v is None else int(v)


def demands_for_week(ws: date, uni: Uni, st: dict | None = None) -> list[Demand]:
    we = ws + timedelta(days=7)
    week_lessons = [l for l in uni.lessons if ws <= l.day < we]
    by_course: dict[str, list[Lesson]] = defaultdict(list)
    for l in week_lessons:
        by_course[l.course].append(l)
    out = []
    for c, ls in by_course.items():
        ls.sort(key=lambda l: l.start)
        target, n = target_of(c, st), len(ls)
        if target <= 0:
            continue
        units = [1] * n if n >= target else [target // n + (1 if i < target % n else 0) for i in range(n)]
        for l, u in zip(ls, units):
            for i in range(u):
                out.append(Demand(c, l, i + 1))
    return out


def _session_from_state(s: dict) -> Session:
    return Session(s["key"], s.get("kind", "review"), s.get("course"), s.get("courses") or [s.get("course")],
                   datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]),
                   date.fromisoformat(s["lesson_day"]) if s.get("lesson_day") else None, note=s.get("note", ""),
                   label=s.get("label", ""))


def base_key(key: str) -> str:
    k = key[3:] if key.startswith("re:") else key
    return re.sub(r"@\d{4}-\d{2}-\d{2}$", "", k)


def plan_week(ws: date, uni: Uni, personal: list[Ev], st: dict | None = None) -> dict:
    """
    עיקרון היציבות: מה שכבר נרשם במצב (ונכתב ליומן) שומר על המקום שלו.
    המנוע משבץ רק דרישות חדשות (שיעור שנוסף), ומסמן "יתומים": סשנים רשומים
    שהשיעור שלהם כבר לא קיים (בוטל/הוזז) — הצעה למחיקה, באישור.

    st["pins"]: החלטות שלך שגוברות על השיבוץ האוטומטי, למשל
      {"key": "review:econ:2026-10-20:שיעור:1", "start": "2026-10-22T09:00"}
    """
    st = st or new_state()
    we = ws + timedelta(days=7)
    sessions: list[Session] = []
    notes: list[str] = []
    claimed = {base_key(s["key"]) for s in st.get("sessions", [])} | set(st.get("archived_keys", []))
    skipped = {k: v["personal"] for k, v in lesson_overrides(uni, personal)["skipped"].items()}

    # 0) סשנים רשומים בשבוע הזה תופסים את המקום שלהם
    for s in st.get("sessions", []):
        if s.get("status") in ("planned", "done", "missed") and ws <= datetime.fromisoformat(s["start"]).date() < we:
            sessions.append(_session_from_state(s))

    # 1) חזרה שבועית ביום ראשון על השבוע שעבר
    prev_ws = ws - timedelta(days=7)
    prev_courses = {l.course for l in uni.lessons if prev_ws <= l.day < ws}
    sun = ws
    has_weekly = any(s.kind == "weekly" for s in sessions) or any(
        s.get("kind") == "weekly" and s["key"].startswith(f"weekly:{sun.isoformat()}") for s in st.get("sessions", []))
    if prev_courses and not has_weekly and not marathon_on(st, sun):
        prof = day_profile(sun, uni)
        if prof["type"] in ("free", "evening_class", "free_with_class"):
            groups = [g for g in CONFIG["weekly_groups"] if any(c in prev_courses for c in g)]
            groups = [[c for c in g if c in prev_courses] for g in groups][: CONFIG["weekly_review_cap"]]
            room = day_room(st, sun, sum(1 for s in sessions if s.start.date() == sun))
            slots = free_slots(sun, prof, busy_blocks(sun, uni, personal), sessions, cap=min(len(groups), room))
            for i, (g, sl) in enumerate(zip(groups, slots)):
                sessions.append(Session(f"weekly:{sun.isoformat()}:{i + 1}", "weekly", None, g, sl[0], sl[1]))
            if len(slots) < len(groups):
                notes.append(f"החזרה השבועית ב-{fmt_d(sun)} קיבלה {len(slots)} מתוך {len(groups)} סשנים (אין מקום).")
        else:
            notes.append(f"אין מקום לחזרה שבועית ב-{fmt_d(sun)}: צריך להחליט איפה.")

    # 2) דרישות חדשות בלבד
    demands = demands_for_week(ws, uni, st)
    demand_keys = {d.key for d in demands}
    pending = [d for d in demands if d.key not in claimed]
    dur = timedelta(minutes=CONFIG["session_min"])
    for p in st.get("pins", []):
        d = next((x for x in pending if x.key == p["key"]), None)
        if not d:
            continue
        s0 = datetime.fromisoformat(p["start"])
        s0 = s0.replace(tzinfo=TZ) if s0.tzinfo is None else s0
        pending.remove(d)
        sessions.append(Session(d.key, "review", d.course, [d.course], s0, s0 + dur, d.lesson.day, note="מועד שקבעת ידנית."))
    for i in range(7):
        day = ws + timedelta(days=i)
        if day == sun and prev_courses:
            continue  # ראשון שמור לחזרה השבועית
        prof = day_profile(day, uni)
        already = sum(1 for s in sessions if s.start.date() == day and s.kind in ("review", "rescheduled"))
        room = day_room(st, day, sum(1 for s in sessions if s.start.date() == day))
        slots = free_slots(day, prof, busy_blocks(day, uni, personal), sessions, cap=max(0, min(prof["cap"] - already, room)))
        per_course = Counter(s.course for s in sessions if s.start.date() == day and s.kind in ("review", "rescheduled"))
        for s0, e0 in slots:
            ready = [d for d in pending if d.lesson.end <= s0 and per_course[d.course] < CONFIG["max_same_course_per_day"]]
            if not ready:
                continue
            d = min(ready, key=Demand.sort_key)
            pending.remove(d)
            per_course[d.course] += 1
            sk = skipped.get(lesson_key(d.lesson))
            note = f"השלמה: השיעור חפף ל\"{sk}\" ביומן האישי." if sk else ""
            sessions.append(Session(d.key, "review", d.course, [d.course], s0, e0, d.lesson.day, note=note))

    unplaced = [{"key": d.key, "course": d.course, "lesson_day": d.lesson.day.isoformat(),
                 "title": f"{COURSES[d.course]['name']} ({fmt_d(d.lesson.day)})"} for d in pending]
    if unplaced:
        notes.append("לא נכנסו לשבוע ויעברו לחזרה השבועית: " + ", ".join(u["title"] for u in unplaced))

    # יתומים: סשן רשום ועתידי שהשיעור שלו כבר לא בתוכנית האוניברסיטה
    now = datetime.now(TZ)
    orphans = [{"key": s.key, "title": s.title(), "start": iso(s.start)} for s in sessions
               if s.kind in ("review", "rescheduled") and s.start > now
               and ws <= (s.lesson_day or s.start.date()) < we and base_key(s.key) not in demand_keys
               and ":".join(base_key(s.key).split(":")[1:4]) not in vanishing_keys(st)]
    if orphans:
        notes.append("סשנים שהשיעור שלהם בוטל או הוזז (להציע מחיקה): " + ", ".join(o["title"] for o in orphans))

    sessions.sort(key=lambda s: s.start)
    return {
        "week_start": ws.isoformat(),
        "sessions": [s.to_json() for s in sessions],
        "unplaced": unplaced,
        "orphans": orphans,
        "targets": weekly_targets(ws, uni, sessions, st),
        "touchpoints": touchpoints(ws, uni),
        "notes": notes,
    }


def weekly_targets(ws: date, uni: Uni, sessions: list[Session], st: dict | None = None) -> dict:
    planned = Counter(s.course for s in sessions if s.kind in ("review", "rescheduled"))
    out = {}
    for c in COURSE_ORDER:
        has = any(ws <= l.day < ws + timedelta(days=7) and l.course == c for l in uni.lessons)
        out[c] = {"name": COURSES[c]["name"], "target": target_of(c, st) if has else 0, "planned": planned[c]}
    return out


def touchpoints(ws: date, uni: Uni) -> list[dict]:
    """ריקאפ אחרי השיעור האחרון, תזכורת, סקירה יומית ושבועית."""
    out = []
    for i in range(7):
        day = ws + timedelta(days=i)
        ls = [l for l in uni.lessons if l.day == day]
        if ls:
            last = max(l.end for l in ls)
            r = last + timedelta(minutes=CONFIG["recap_after_min"])
            out.append({"kind": "recap", "at": iso(r), "courses": sorted({l.course for l in ls}, key=COURSE_ORDER.index)})
            out.append({"kind": "recap_reminder", "at": iso(r + timedelta(minutes=CONFIG["recap_reminder_after_min"]))})
        wd = day.weekday()
        if wd == SAT:
            out.append({"kind": "weekly_review", "at": iso(at(day, hm(CONFIG["weekly_review_motzash_time"])))})
        elif wd != FRI:
            out.append({"kind": "daily_review", "at": iso(at(day, hm(CONFIG["daily_review_time"])))})
    out.sort(key=lambda x: x["at"])
    return out


# ---------------------------------------------------------------- sync engine
def lesson_key(l: Lesson) -> str:
    """מזהה יציב לשיעור: קורס + תאריך + סוג. שעה ומיקום הם תכונות, לא חלק מהזהות."""
    return f"{l.course}:{l.day.isoformat()}:{l.kind}"


def lesson_overrides(uni: Uni, personal: list[Ev]) -> dict:
    """
    אירוע אישי שחופף לשיעור:
      - אם הכותרת שלו היא שם של אותו קורס: עותק ידני של השיעור (כפילות), מתעלמים ומדווחים.
      - אחרת: האירוע האישי גובר. השיעור נחשב "פוספס" והבוט משבץ השלמה.
    """
    skipped, dups = {}, {}
    for l in uni.lessons:
        for e in personal:
            if e.transparent or e.all_day or not (e.start < l.end and l.start < e.end):
                continue
            k = lesson_key(l)
            if course_of(e.summary) == l.course:
                dups[k] = {"date": l.day.isoformat(), "name": COURSES[l.course]["name"], "personal_id": e.uid}
            else:
                skipped[k] = {"date": l.day.isoformat(), "start": fmt_t(l.start), "name": COURSES[l.course]["name"],
                              "personal": e.summary}
    for k in dups:
        skipped.pop(k, None)
    return {"skipped": skipped, "duplicates": dups}


def feed_snapshot(uni: Uni, since: date) -> dict:
    out = {}
    for l in uni.lessons:
        if l.day >= since:
            out[lesson_key(l)] = {"start": fmt_t(l.start), "end": fmt_t(l.end), "loc": l.location, "st": "on"}
    for c, e in uni.cancelled:
        if c and e.start.date() >= since:
            kind = "תרגול" if "תרגול" in e.description else "שיעור"
            k = f"{c}:{e.start.date().isoformat()}:{kind}"
            out.setdefault(k, {"start": fmt_t(e.start), "end": fmt_t(e.end), "loc": e.location, "st": "cancelled"})
    return out


def _lk_label(k: str, v: dict) -> str:
    c, d, kind = k.split(":")
    dd = date.fromisoformat(d)
    return f"{COURSES[c]['name']} ({kind}) {HEB_DAY[dd.weekday()]} {fmt_d(dd)} {v['start']}"


def feed_check(uni: Uni, raw_count: int | None, st: dict, today: date, now: datetime, write: bool) -> dict:
    """
    מנוע הסנכרון. רץ בתחילת כל הרצה שמשבצת, לפני sync/reschedule.
      ok=false (block=true): הפיד ריק או חשוד. לא משבצים כלום, רק מתריעים. כשל אינו "אין שינוי".
      stale: הסנכרון התקין האחרון ישן מ-36 שעות.
      שינויים מול ההרצה הקודמת: נוסף, בוטל, שעה/מיקום השתנו, הועבר ליום אחר, ונעלם.
      שיעור שנעלם בלי סימון ביטול מסומן "בבדיקה", ומאושר כנעלם רק אחרי 2 הרצות רצופות.
    """
    feed = st.setdefault("feed", {})
    prev = feed.get("lessons", {})
    last_ok = feed.get("last_ok")
    res = {"ok": True, "block": False, "reasons": [], "stale": False, "first_run": not prev,
           "added": [], "cancelled": [], "changed": [], "moved": [], "vanishing": [], "vanished": [],
           "duplicates": [], "skipped": []}
    if last_ok and now - datetime.fromisoformat(last_ok) > timedelta(hours=CONFIG["feed_stale_hours"]):
        res["stale"] = True
        res["reasons"].append(f"הסנכרון התקין האחרון היה ב-{last_ok[:16].replace('T', ' ')}")
    n_on = sum(1 for l in uni.lessons if l.day >= today)
    prev_on = sum(1 for k, v in prev.items() if v["st"] == "on" and k.split(":")[1] >= today.isoformat())
    if raw_count == 0 or (raw_count is None and not uni.lessons):
        res.update(ok=False, block=True)
        res["reasons"].append("פיד האוניברסיטה חזר ריק")
    elif prev_on >= 10 and n_on < prev_on * CONFIG["feed_drop_ratio"]:
        res.update(ok=False, block=True)
        res["reasons"].append(f"בפיד יש עכשיו {n_on} שיעורים קדימה, לעומת {prev_on} בהרצה הקודמת")
    if res["block"]:
        return res

    cur = feed_snapshot(uni, today)
    vanish = feed.get("vanishing", {})
    new_vanish = {}
    removed, added = [], []
    for k, v in cur.items():
        p = prev.get(k)
        if p is None:
            if prev:
                (added if v["st"] == "on" else res["cancelled"]).append((k, v))
        elif v["st"] == "cancelled" and p["st"] == "on":
            res["cancelled"].append((k, v))
        elif v["st"] == "on" and (p["start"], p["end"], p["loc"]) != (v["start"], v["end"], v["loc"]):
            what = "שעה" if (p["start"], p["end"]) != (v["start"], v["end"]) else "מיקום"
            res["changed"].append({"key": k, "text": f"{_lk_label(k, v)}: {what} השתנה ({p['start']} עד {p['end']} {p['loc']} → {v['start']} עד {v['end']} {v['loc']})"})
    for k, p in prev.items():
        if k.split(":")[1] < today.isoformat() or k in cur or p["st"] != "on":
            continue
        removed.append((k, p))
    # הועבר ליום אחר: אותו קורס וסוג, נעלם מיום אחד והופיע ביום אחר בטווח של 14 יום
    for rk, rv in list(removed):
        rc, rd, rkind = rk.split(":")
        match = next(((ak, av) for ak, av in added if ak.split(":")[0] == rc and ak.split(":")[2] == rkind
                      and abs((date.fromisoformat(ak.split(":")[1]) - date.fromisoformat(rd)).days) <= 14), None)
        if match:
            added.remove(match)
            removed.remove((rk, rv))
            res["moved"].append({"from": rk, "to": match[0], "text": f"{_lk_label(rk, rv)} הועבר ל{HEB_DAY[date.fromisoformat(match[0].split(':')[1]).weekday()]} {fmt_d(date.fromisoformat(match[0].split(':')[1]))} {match[1]['start']}"})
    for rk, rv in removed:
        n = vanish.get(rk, 0) + 1
        if n >= CONFIG["vanish_confirm_runs"]:
            res["vanished"].append({"key": rk, "text": f"{_lk_label(rk, rv)} נעלם מהפיד (בלי הודעת ביטול)"})
        else:
            new_vanish[rk] = n
            res["vanishing"].append({"key": rk, "text": f"{_lk_label(rk, rv)} לא מופיע בפיד. בודק שוב בהרצה הבאה לפני שמשנים משהו"})
    res["added"] = [{"key": k, "text": f"נוסף: {_lk_label(k, v)}, {v['loc']}"} for k, v in added]
    res["cancelled"] = [{"key": k, "text": f"בוטל: {_lk_label(k, v)}"} for k, v in res["cancelled"]]
    if write:
        snap = dict(cur)
        for rk in new_vanish:  # עדיין "בבדיקה": נשאר בתמונה כדי שההרצה הבאה תשווה אליו
            snap[rk] = prev[rk]
        feed.update(lessons=snap, vanishing=new_vanish, last_ok=iso(now), count=raw_count if raw_count is not None else len(uni.lessons))
    return res


def vanishing_keys(st: dict) -> set[str]:
    return set(st.get("feed", {}).get("vanishing", {}).keys())


# ---------------------------------------------------------------- exceptions
def exceptions(start: date, days: int, uni: Uni, personal: list[Ev], plan_sessions: list[dict] | None = None) -> list[dict]:
    end = start + timedelta(days=days)
    pattern = regular_pattern(uni.lessons)
    out = []
    for c, e in uni.cancelled:
        if start <= e.start.date() < end:
            out.append({"date": e.start.date().isoformat(), "type": "cancelled",
                        "text": f"{COURSES[c]['name']} ב-{HEB_DAY[e.start.weekday()]} {fmt_d(e.start.date())} {fmt_t(e.start)} בוטל"})
    for l in uni.lessons:
        if not (start <= l.day < end):
            continue
        slot = (l.start.weekday(), fmt_t(l.start))
        if slot not in pattern.get(l.course, set()):
            out.append({"date": l.day.isoformat(), "type": "moved_or_extra",
                        "text": f"{COURSES[l.course]['name']} ({l.kind}) במועד לא רגיל: {HEB_DAY[l.start.weekday()]} {fmt_d(l.day)} {fmt_t(l.start)} עד {fmt_t(l.end)}, {l.location}"})
        if l.start.weekday() in CONFIG["free_weekdays"]:
            out.append({"date": l.day.isoformat(), "type": "class_on_free_day",
                        "text": f"שיעור ביום {HEB_DAY[l.start.weekday()]} {fmt_d(l.day)} (יום שבדרך כלל חופשי): {COURSES[l.course]['name']}"})
    for d, s in uni.no_study_days.items():
        if start <= d < end:
            out.append({"date": d.isoformat(), "type": "no_study_day", "text": f"{HEB_DAY[d.weekday()]} {fmt_d(d)}: {s}"})
    # אירוע אישי על שיעור: האישי גובר, והבוט משבץ השלמה
    for k, v in lesson_overrides(uni, personal)["skipped"].items():
        if start <= date.fromisoformat(v["date"]) < end:
            out.append({"date": v["date"], "type": "lesson_skipped",
                        "text": f"{v['name']} ב-{fmt_d(date.fromisoformat(v['date']))} {v['start']} חופף ל\"{v['personal']}\": האירוע האישי גובר, שובצה השלמה"})
    # חפיפה בין שיעורים של קורסים שונים
    for i, a in enumerate(uni.lessons):
        for b in uni.lessons[i + 1:]:
            if a.day == b.day and a.course != b.course and a.start < b.end and b.start < a.end and start <= a.day < end:
                out.append({"date": a.day.isoformat(), "type": "overlap",
                            "text": f"חפיפה ב-{fmt_d(a.day)}: {COURSES[a.course]['name']} ו{COURSES[b.course]['name']}"})
    # התנגשות בין סשן מתוכנן לאירוע אישי
    for s in plan_sessions or []:
        ss, se = datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"])
        if not (start <= ss.date() < end):
            continue
        for e in personal:
            if not e.transparent and not e.all_day and ss < e.end and e.start < se:
                out.append({"date": ss.date().isoformat(), "type": "personal_conflict",
                            "text": f"{s['title']} ({fmt_t(ss)}) מתנגש ב\"{e.summary}\""})
    out.sort(key=lambda x: (x["date"], x["type"]))
    # בלי כפילויות
    seen, uniq = set(), []
    for x in out:
        if x["text"] not in seen:
            seen.add(x["text"])
            uniq.append(x)
    return uniq


# ---------------------------------------------------------------- reschedule
def reschedule(today: date, uni: Uni, personal: list[Ev], state: dict, now: datetime | None = None) -> dict:
    """
    משבץ מחדש סשנים שפוספסו:
      - status=missed (דיווחת שלא עשית), או
      - status=planned שהסתיימו לפני תחילת היום ולא קיבלו אישור (שתיקה אינה ביצוע).
    חזרות שבועיות (בלי קורס) לא משובצות מחדש.
    """
    now = now or datetime.now(TZ)
    cutoff = at(today, time(0, 0))
    missed = [s for s in state.get("sessions", []) if s.get("course") and s.get("kind", "review") in ("review", "rescheduled") and (
        s.get("status") == "missed" or (s.get("status") == "planned" and datetime.fromisoformat(s["end"]) < cutoff))]
    missed_keys = {m["key"] for m in missed}
    # מה כבר תפוס: התוכנית של השבוע הזה והבא + השלמות קיימות
    existing: list[Session] = []
    for ws in sorted({week_start(today), week_start(today + timedelta(days=7))}):
        p = plan_week(ws, uni, personal, state)
        for s in p["sessions"]:
            if s["key"] in missed_keys:
                continue
            existing.append(Session(s["key"], s["kind"], s["course"], s["courses"], datetime.fromisoformat(s["start"]),
                                    datetime.fromisoformat(s["end"]),
                                    date.fromisoformat(s["lesson_day"]) if s.get("lesson_day") else None))
    missed.sort(key=lambda s: (COURSES[s["course"]]["tier"], s["start"]))
    moved, left = [], []
    added_per_day = Counter()
    for m in missed:
        placed = False
        for i in range(0, 7):
            day = today + timedelta(days=i)
            wd = day.weekday()
            if m.get("lesson_day") and day < date.fromisoformat(m["lesson_day"]):
                continue      # לא חוזרים על שיעור לפני שהתקיים
            if wd in CONFIG["reschedule_no_extra_weekdays"] or added_per_day[day] >= CONFIG["reschedule_max_per_day"]:
                continue
            same = sum(1 for s in existing if s.start.date() == day and s.course == m["course"])
            if same >= CONFIG["max_same_course_per_day"]:
                continue
            if day_room(state, day, sum(1 for s in existing if s.start.date() == day)) <= 0:
                continue
            if wd == SAT:
                # גיבוי בשבת רק לחומר של חמישי
                if date.fromisoformat(m["lesson_day"]).weekday() != THU:
                    continue
                prof = {"start": at(day, hm(CONFIG["saturday_backup_start"])), "end": at(day, hm("15:00")), "cap": 1}
            else:
                prof = day_profile(day, uni)
                if prof["type"] == "class_day":
                    continue
                on_day = sum(1 for s in existing if s.start.date() == day)
                total_cap = CONFIG["reschedule_total_cap_free_day"] if prof["type"] == "free" else prof["cap"] + 1
                if on_day >= total_cap:
                    continue
                prof = dict(prof, cap=prof["cap"] + CONFIG["reschedule_max_per_day"])
            if day == today:
                prof = dict(prof, start=max(prof["start"], _round_up(now + timedelta(minutes=30))))
            slots = free_slots(day, prof, busy_blocks(day, uni, personal), existing)
            if not slots:
                continue
            st, en = slots[0]
            base = base_key(m["key"])
            why = "דיווחת שלא בוצע" if m.get("status") == "missed" else "לא אושר כבוצע"
            s = Session(f"re:{base}@{day.isoformat()}", "rescheduled", m["course"], [m["course"]], st, en,
                        date.fromisoformat(m["lesson_day"]),
                        note=f"שובץ מחדש: הסשן ב-{fmt_d(datetime.fromisoformat(m['start']).date())} {why}. אם כבר עשית, סמן ואמחק.")
            existing.append(s)
            added_per_day[day] += 1
            moved.append({"from_key": m["key"], "from_title": _session_from_state(m).title(), "from_eventId": m.get("eventId"), **s.to_json()})
            placed = True
            break
        if not placed:
            left.append({"key": m["key"], "title": _session_from_state(m).title()})
    return {"today": today.isoformat(), "rescheduled": moved, "not_placed": left}


def apply_reschedule(st: dict, r: dict):
    idx = state_sessions_index(st)
    for x in r["rescheduled"]:
        if x["from_key"] in idx:
            idx[x["from_key"]]["status"] = "rescheduled"
            idx[x["from_key"]]["moved_to"] = x["key"]
        st["sessions"].append({k: x[k] for k in ("key", "kind", "course", "courses", "start", "end", "lesson_day", "title", "note")}
                              | {"status": "planned"})
    for x in r["not_placed"]:
        if x["key"] in idx:
            idx[x["key"]]["status"] = "dropped"
            idx[x["key"]]["note"] = "לא נמצא מקום תוך שבוע; עובר לחזרה השבועית."


# ---------------------------------------------------------------- diff
DONE_MARK = "✅ "


def _ev_start(e: dict) -> datetime:
    v = e["start"]
    if isinstance(v, dict):
        v = v.get("dateTime") or v.get("date")
    return datetime.fromisoformat(v).astimezone(TZ)


def diff(plan: dict, existing_events: list[dict], now: datetime | None = None) -> dict:
    """
    משווה תוכנית לאירועים ביומן 'חזרות לימודים' לפי 'מזהה:' בתיאור.
    נוגע רק בעתיד. אירוע שסומן ✅ לא משתנה.
    """
    now = now or datetime.now(TZ)
    by_key = {}
    for e in existing_events:
        m = re.search(r"מזהה: (\S+)", e.get("description", "") or "")
        if m:
            by_key[m.group(1)] = e
    create, update, keep = [], [], []
    plan_keys = set()
    for s in plan["sessions"]:
        plan_keys.add(s["key"])
        future = datetime.fromisoformat(s["start"]) > now
        e = by_key.get(s["key"])
        if not e:
            if future:
                create.append(s)
            continue
        if (e.get("summary") or "").startswith(DONE_MARK):
            keep.append(s["key"])
            continue
        es = _ev_start(e)
        if (iso(es) != s["start"] or e.get("summary") != s["title"]) and future and es > now:
            update.append({"eventId": e["id"], "old_start": iso(es), **s})
        else:
            keep.append(s["key"])
    remove = [{"eventId": e["id"], "key": k, "title": e.get("summary"), "start": iso(_ev_start(e))}
              for k, e in by_key.items()
              if k not in plan_keys and not (e.get("summary") or "").startswith(DONE_MARK)
              and _ev_start(e) > now]
    return {"create": create, "update": update, "delete": remove, "unchanged": keep}


# ---------------------------------------------------------------- state helpers
def new_state() -> dict:
    return {"version": 1, "updated": None, "pins": [], "sessions": [], "recaps": [], "assignments": [], "tasks": [],
            "course_kind": {c: COURSES[c]["kind"] for c in COURSE_ORDER}, "pending_questions": [], "runs": [],
            "notified": {}, "acked": [], "archive": {}, "archived_keys": []}


_MD_ESC = re.compile(r"\\([\\\[\]_*#`>~|{}()+\-.!<])")


def parse_state_text(text: str) -> dict:
    """
    מקבל את המצב בכל צורה שהוא מגיע מהכלים:
      - JSON רגיל
      - פלט read_file_content כפי שהוא ({"fileContent": "...", ...})
      - טקסט עם בריחות Markdown של Google Docs (לוכסן הפוך לפני סוגריים, קו תחתון, כוכבית)
    """
    text = text.strip().lstrip("\ufeff")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = json.loads(_MD_ESC.sub(r"\1", text))
    if isinstance(obj, dict) and "fileContent" in obj and "sessions" not in obj:
        return parse_state_text(obj["fileContent"])
    return obj


def state_checksum(st: dict) -> str:
    import hashlib
    body = {k: v for k, v in st.items() if k != "checksum"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:12]


def load_state(path: str | None) -> dict:
    st = new_state()
    if path:
        with open(path, encoding="utf-8") as f:
            loaded = parse_state_text(f.read())
        cs = loaded.get("checksum")
        if cs and cs != state_checksum(loaded):
            sys.exit("STATE_CHECKSUM_MISMATCH: המצב שהועתק לא זהה למקור. להשתמש בעותק הקודם ולדווח.")
        st.update(loaded)
    return st


def dump_state(st: dict) -> str:
    """JSON עם רשומה אחת בשורה: קטן וקל להעתקה מדויקת."""
    lines = ["{"]
    keys = list(st.keys())
    for i, k in enumerate(keys):
        v = st[k]
        comma = "," if i < len(keys) - 1 else ""
        if isinstance(v, list) and v:
            lines.append(f'"{k}":[')
            for j, item in enumerate(v):
                lines.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + ("," if j < len(v) - 1 else ""))
            lines.append("]" + comma)
        else:
            lines.append(f'"{k}":' + json.dumps(v, ensure_ascii=False, separators=(",", ":")) + comma)
    lines.append("}")
    return "\n".join(lines) + "\n"


COMPACT_AFTER_DAYS = 14
SESSION_FIELDS = ("key", "status", "kind", "course", "courses", "start", "end", "lesson_day", "eventId", "note", "moved_to", "label")


def compact_state(st: dict, now: datetime):
    """שומר את המצב קטן: סשנים ישנים מ-14 יום הופכים לסיכום שבועי לפי קורס."""
    cutoff = now - timedelta(days=COMPACT_AFTER_DAYS)
    keep, arch = [], st.setdefault("archive", {})
    for s in st.get("sessions", []):
        if datetime.fromisoformat(s["end"]) < cutoff:
            wk = week_start(datetime.fromisoformat(s["start"]).date()).isoformat()
            c = s.get("course") or "weekly"
            row = arch.setdefault(wk, {}).setdefault(c, {"planned": 0, "done": 0, "missed": 0})
            row["planned"] += 1 if s.get("kind") != "rescheduled" else 0
            if s.get("status") == "done":
                row["done"] += 1
            elif s.get("status") in ("missed", "dropped"):
                row["missed"] += 1
            if s.get("kind") == "marathon" and s.get("course"):
                # התקדמות המרתון נשמרת גם אחרי שהסשן עצמו נמחק מהמצב
                m = st.setdefault("marathon", {})
                if s.get("status") == "done":
                    m.setdefault("archived_done", {})[s["course"]] = m.get("archived_done", {}).get(s["course"], 0) + 1
                if s.get("status") != "dropped":
                    m.setdefault("archived_total", {})[s["course"]] = m.get("archived_total", {}).get(s["course"], 0) + 1
            st.setdefault("archived_keys", []).append(base_key(s["key"]))
        else:
            keep.append({k: s[k] for k in SESSION_FIELDS if s.get(k) not in (None, "")})
    st["sessions"] = keep
    st["archived_keys"] = sorted(set(st.get("archived_keys", [])))
    st["recaps"] = [r for r in st.get("recaps", []) if r.get("date", "") >= (now.date() - timedelta(days=45)).isoformat()]
    st["assignments"] = [a for a in st.get("assignments", []) if a.get("status") != "done"
                         or (a.get("due") or "9999") >= (now.date() - timedelta(days=14)).isoformat()]
    old = (now.date() - timedelta(days=14)).isoformat()
    st["tasks"] = [t for t in st.get("tasks", []) if t.get("status") == "open" or (t.get("done_at") or "9999")[:10] >= old]
    st["runs"] = st.get("runs", [])[-30:]


def save_state(st: dict, path: str):
    now = datetime.now(TZ)
    compact_state(st, now)
    st["updated"] = iso(now)
    st["planner_version"] = VERSION
    st.pop("checksum", None)
    st["checksum"] = state_checksum(st)
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_state(st))


def state_sessions_index(st: dict) -> dict:
    return {s["key"]: s for s in st.get("sessions", [])}


def register_plan(st: dict, plan: dict, event_ids: dict | None = None):
    """מוסיף למצב סשנים מהתוכנית (status=planned) ומעדכן זמנים של סשנים שעוד לא בוצעו."""
    idx = state_sessions_index(st)
    event_ids = event_ids or {}
    for s in plan["sessions"]:
        cur = idx.get(s["key"])
        if cur is None:
            cur = {"key": s["key"], "status": "planned"}
            st["sessions"].append(cur)
            idx[s["key"]] = cur
        if cur.get("status") in ("planned", None):
            cur.update({k: s.get(k, "") if k == "label" else s[k] for k in ("kind", "course", "courses", "start", "end", "lesson_day", "title", "note", "label")})
        if s["key"] in event_ids:
            cur["eventId"] = event_ids[s["key"]]


def review_data(d: date, uni: Uni, personal: list[Ev], st: dict) -> dict:
    """כל מה שהסקירה היומית צריכה, מחושב, כדי שהמודל רק ינסח."""
    ws = week_start(d)
    plan = plan_week(ws, uni, personal, st)
    idx = state_sessions_index(st)
    now = datetime.now(TZ)

    def with_status(s):
        cur = idx.get(s["key"], {})
        return {"key": s["key"], "title": s["title"], "start": s["start"], "status": cur.get("status", "planned")}

    today = [with_status(s) for s in plan["sessions"] if s["start"][:10] == d.isoformat()]
    tomorrow_d = d + timedelta(days=1)
    plan_tmr = plan if week_start(tomorrow_d) == ws else plan_week(week_start(tomorrow_d), uni, personal, st)
    tomorrow = [s for s in plan_tmr["sessions"] if s["start"][:10] == tomorrow_d.isoformat()]
    tmr_lessons = [{"course": COURSES[l.course]["name"], "kind": l.kind, "start": fmt_t(l.start), "end": fmt_t(l.end), "location": l.location}
                   for l in uni.lessons if l.day == tomorrow_d]
    # התקדמות שבועית: בוצע מול מתוכנן, לפי קורס
    week_keys = [s["key"] for s in plan["sessions"] if s["kind"] != "weekly"]
    prog = defaultdict(lambda: {"planned": 0, "done": 0})
    for s in plan["sessions"]:
        if s["kind"] == "weekly":
            continue
        prog[s["course"]]["planned"] += 1
        if idx.get(s["key"], {}).get("status") == "done":
            prog[s["course"]]["done"] += 1
    for s in st.get("sessions", []):
        if s.get("status") == "done" and s["key"] not in week_keys and s.get("course") and s.get("start", "")[:10] >= ws.isoformat() \
                and s.get("start", "")[:10] < (ws + timedelta(days=7)).isoformat():
            prog[s["course"]]["done"] += 1
    progress = {COURSES[c]["name"]: v for c, v in sorted(prog.items(), key=lambda x: COURSE_ORDER.index(x[0]))}
    unanswered = [with_status(s) for s in plan["sessions"]
                  if datetime.fromisoformat(s["end"]) < now and idx.get(s["key"], {}).get("status", "planned") == "planned"]
    soon = [a for a in st.get("assignments", []) if a.get("status") != "done" and a.get("due")
            and a["due"][:10] <= (d + timedelta(days=7)).isoformat()]
    return {
        "date": d.isoformat(), "day": HEB_DAY[d.weekday()],
        "today_sessions": today, "unanswered_past_sessions": unanswered,
        "tomorrow": {"date": tomorrow_d.isoformat(), "day": HEB_DAY[tomorrow_d.weekday()], "lessons": tmr_lessons,
                     "sessions": [{"title": s["title"], "start": fmt_t(datetime.fromisoformat(s["start"]))} for s in tomorrow]},
        "week_progress": progress,
        "assignments_next_7_days": soon,
        "exceptions_next_7_days": exceptions(d + timedelta(days=1), 7, uni, personal, plan["sessions"] + plan_tmr["sessions"]),
        "recaps_today": [r for r in st.get("recaps", []) if r.get("date") == d.isoformat()],
        "pending_questions": st.get("pending_questions", []),
    }


def week_report(ws: date, st: dict, now: datetime | None = None) -> dict:
    """סיכום שבוע לסקירה השבועית: בוצע מול מתוכנן, נושאים חלשים, נושאים לרענון."""
    now = now or datetime.now(TZ)
    we = ws + timedelta(days=7)
    per = defaultdict(lambda: {"planned": 0, "done": 0, "missed": 0, "open": 0})
    for s in st.get("sessions", []):
        sd = datetime.fromisoformat(s["start"]).date()
        if not (ws <= sd < we):
            continue
        c = s.get("course") or "weekly"
        if s.get("kind") != "rescheduled":
            per[c]["planned"] += 1
        stt = s.get("status")
        if stt == "done":
            per[c]["done"] += 1
        elif stt in ("missed", "dropped", "rescheduled"):
            per[c]["missed"] += 1
        elif stt == "planned":
            per[c]["open"] += 1
    for c, row in st.get("archive", {}).get(ws.isoformat(), {}).items():
        for k in ("planned", "done", "missed"):
            per[c][k] += row.get(k, 0)
    name = lambda c: COURSES[c]["name"] if c in COURSES else "חזרה שבועית"
    by_course = {name(c): v for c, v in sorted(per.items(), key=lambda x: COURSE_ORDER.index(x[0]) if x[0] in COURSE_ORDER else 99)}
    tot_p = sum(v["planned"] for v in per.values())
    tot_d = sum(v["done"] for v in per.values())
    recaps = st.get("recaps", [])
    weak = [r for r in recaps if ws.isoformat() <= r.get("date", "") < we.isoformat() and (r.get("rating") or 5) <= 2 or
            (ws.isoformat() <= r.get("date", "") < we.isoformat() and r.get("unclear"))]
    # רענון: נושאים מלפני 3–4 שבועות בקורסים מסוג "רענון"
    kinds = st.get("course_kind", {})
    r_from, r_to = (ws - timedelta(days=28)).isoformat(), (ws - timedelta(days=14)).isoformat()
    refresh = [r for r in recaps if kinds.get(r.get("course")) == "refresh" and r_from <= r.get("date", "") < r_to and r.get("topics")]
    # שתי שבועות ברצף מתחת ל-70% → הצעה להקל
    prev = st.get("archive", {}).get((ws - timedelta(days=7)).isoformat(), {})
    prev_p = sum(v.get("planned", 0) for v in prev.values())
    prev_d = sum(v.get("done", 0) for v in prev.values())
    rate = tot_d / tot_p if tot_p else None
    prev_rate = prev_d / prev_p if prev_p else None
    burnout = rate is not None and prev_rate is not None and rate < 0.7 and prev_rate < 0.7
    return {"week_start": ws.isoformat(), "by_course": by_course, "total_planned": tot_p, "total_done": tot_d,
            "completion_rate": round(rate, 2) if rate is not None else None,
            "weak_topics": [{"course": name(r["course"]), "date": r["date"], "rating": r.get("rating"),
                             "topics": r.get("topics"), "unclear": r.get("unclear")} for r in weak],
            "refresh_topics": [{"course": name(r["course"]), "date": r["date"], "topics": r["topics"]} for r in refresh],
            "suggest_lighter_load": burnout,
            "open_assignments": [a for a in st.get("assignments", []) if a.get("status") != "done"],
            "weeks_since_start": (ws - date(2026, 10, 18)).days // 7}


# ---------------------------------------------------------------- calendar <-> state
def existing_from_state(st: dict) -> list[dict]:
    out = []
    for s in st.get("sessions", []):
        if s.get("eventId") and s.get("status") in ("planned", "done", "missed"):
            out.append({"id": s["eventId"], "start": s["start"], "summary": _session_from_state(s).title(),
                        "description": f"מזהה: {s['key']}"})
    return out


def adopt_calendar(st: dict, events: list[dict], win_start: datetime, win_end: datetime) -> list[str]:
    """
    הבוט עובד לפי היומן: שינויים שעשית ביומן החזרות גוברים על המצב.
    הזזה = מועד חדש, מחיקה = ויתור, ✅ בכותרת = בוצע, ❌ = לא בוצע.
    """
    by_id, by_key = {}, {}
    for e in events:
        by_id[e.get("id")] = e
        m = re.search(r"מזהה: (\S+)", e.get("description", "") or "")
        if m:
            by_key[m.group(1)] = e
    changes = []
    dur = timedelta(minutes=CONFIG["session_min"])
    for s in st.get("sessions", []):
        if not s.get("eventId") or s.get("status") not in ("planned", "done", "missed"):
            continue
        st0 = datetime.fromisoformat(s["start"])
        e = by_id.get(s["eventId"]) or by_key.get(s["key"])
        if e is None:
            if win_start <= st0 < win_end and s.get("status") == "planned":
                s["status"] = "dropped"
                s["note"] = "נמחק ממך ביומן."
                changes.append(f"נמחק ביומן: {_session_from_state(s).title()} ({fmt_d(st0.date())})")
            continue
        title = (e.get("summary") or "").strip()
        if title.startswith("✅") and s["status"] != "done":
            s["status"] = "done"
            changes.append(f"סומן ביומן כבוצע: {_session_from_state(s).title()}")
        elif title.startswith("❌") and s["status"] == "planned":
            s["status"] = "missed"
            changes.append(f"סומן ביומן כלא בוצע: {_session_from_state(s).title()}")
        es = _ev_start(e)
        if es != st0 and s["status"] == "planned":
            ee = e.get("end")
            en = datetime.fromisoformat(ee.get("dateTime") if isinstance(ee, dict) else ee).astimezone(TZ) if ee else es + dur
            s["start"], s["end"] = iso(es), iso(en)
            changes.append(f"הוזז ביומן: {_session_from_state(s).title()} → {HEB_DAY[es.weekday()]} {fmt_d(es.date())} {fmt_t(es)}")
    return changes


def exc_id(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode()).hexdigest()[:6]


def exc_notify(today: date, uni: Uni, personal: list[Ev], st: dict, write: bool) -> dict:
    """חריגים ל-7 ימים: חדשים (לדווח), ותזכורת יומיים לפני למי שלא קיבל תשובה."""
    sess = []
    for k in sorted({week_start(today), week_start(today + timedelta(days=7))}):
        sess += plan_week(k, uni, personal, st)["sessions"]
    ex = exceptions(today, 8, uni, personal, sess)
    notified = st.setdefault("notified", {})
    acked = set(st.setdefault("acked", []))
    new, remind = [], []
    for x in ex:
        i = exc_id(x["text"])
        x = dict(x, id=i)
        if i in acked:
            continue
        if i not in notified:
            new.append(x)
            if write:
                notified[i] = today.isoformat()
        else:
            days_to = (date.fromisoformat(x["date"]) - today).days
            if days_to <= 2 and notified[i] < today.isoformat():
                remind.append(x)
    # ניקוי ישנים
    st["notified"] = {k: v for k, v in notified.items() if v >= (today - timedelta(days=21)).isoformat()}
    return {"new": new, "remind": remind}


# ---------------------------------------------------------------- text output
def plan_text(p: dict) -> str:
    ws = date.fromisoformat(p["week_start"])
    out = [f"שבוע {fmt_d(ws)} עד {fmt_d(ws + timedelta(days=6))}"]
    by_day = defaultdict(list)
    for s in p["sessions"]:
        by_day[datetime.fromisoformat(s["start"]).date()].append(s)
    for i in range(7):
        d = ws + timedelta(days=i)
        items = by_day.get(d, [])
        line = f"{HEB_DAY[d.weekday()]} {fmt_d(d)}: "
        line += " | ".join(f"{fmt_t(datetime.fromisoformat(s['start']))} {s['title']}" for s in items) if items else "—"
        out.append(line)
    t = p["targets"]
    out.append("יעד מול תכנון: " + ", ".join(f"{v['name']} {v['planned']}/{v['target']}" for v in t.values() if v["target"]))
    out += p["notes"]
    return "\n".join(out)


# ---------------------------------------------------------------- cli
def _plan_for(ws: date, uni: Uni, personal: list[Ev], st: dict) -> dict:
    p = plan_week(ws, uni, personal, st)
    p["exceptions"] = exceptions(ws, 7, uni, personal, p["sessions"])
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="מנוע השיבוץ של בוט הלימודים")
    ap.add_argument("cmd", choices=["plan", "sync", "exceptions", "day", "review-data", "reschedule", "week-report",
                                    "mark", "recap", "assignment", "pin", "question", "log-run", "init-state",
                                    "course-kind", "version", "set-event", "move", "adopt", "exc-notify", "exc-ack", "feed-check"])
    ap.add_argument("--events", help="אירועי יומן האוניברסיטה (JSON של list_events או ICS)")
    ap.add_argument("--personal", help="אירועי היומן האישי (JSON של list_events)")
    ap.add_argument("--existing", help="אירועי יומן 'חזרות לימודים' (JSON של list_events)")
    ap.add_argument("--state", help="קובץ מצב")
    ap.add_argument("--out", help="לאן לכתוב מצב מעודכן (ברירת מחדל: --state)")
    ap.add_argument("--week")
    ap.add_argument("--start")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--date")
    ap.add_argument("--today")
    ap.add_argument("--key")
    ap.add_argument("--status", choices=["planned", "done", "missed", "dropped"])
    ap.add_argument("--course")
    ap.add_argument("--rating", type=int)
    ap.add_argument("--topics", default="")
    ap.add_argument("--unclear", default="")
    ap.add_argument("--title")
    ap.add_argument("--due")
    ap.add_argument("--est", type=int, help="הערכת זמן בדקות")
    ap.add_argument("--id")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--text", help="טקסט חופשי (שאלה פתוחה / הערה)")
    ap.add_argument("--run")
    ap.add_argument("--ok", choices=["yes", "no"], default="yes")
    ap.add_argument("--write", action="store_true", help="לכתוב את התוצאה לקובץ המצב")
    ap.add_argument("--human", action="store_true", help="פלט קריא במקום JSON")
    a = ap.parse_args(argv)

    raw_events = load_events(a.events) if a.events else []
    uni = classify(raw_events) if a.events else Uni([], [], {}, [], [])
    personal = load_events(a.personal)
    st = load_state(a.state)
    out_path = a.out or a.state
    now = datetime.now(TZ)
    pr = lambda obj: print(json.dumps(obj, ensure_ascii=False, indent=1))

    if a.cmd == "init-state":
        save_state(new_state(), out_path)
        print(out_path)

    elif a.cmd == "plan":
        p = _plan_for(week_start(date.fromisoformat(a.week)), uni, personal, st)
        print(plan_text(p)) if a.human else pr(p)

    elif a.cmd == "sync":
        # תוכנית + השוואה ליומן החזרות. --write רושם את הסשנים במצב.
        p = _plan_for(week_start(date.fromisoformat(a.week)), uni, personal, st)
        ex = existing_from_state(st)
        if a.existing:
            data = json.load(open(a.existing, encoding="utf-8"))
            ex = data.get("events", data) if isinstance(data, dict) else data
        d = diff(p, ex, now)
        if a.write:
            ids = {}
            for e in ex:
                m = re.search(r"מזהה: (\S+)", e.get("description", "") or "")
                if m:
                    ids[m.group(1)] = e["id"]
            register_plan(st, p, ids)
            st["archived_keys"] = sorted(set(st.get("archived_keys", [])) | {u["key"] for u in p["unplaced"]})
            save_state(st, out_path)
        pr({"plan_text": plan_text(p), "diff": d, "unplaced": p["unplaced"], "exceptions": p["exceptions"],
            "touchpoints": p["touchpoints"], "notes": p["notes"]})

    elif a.cmd == "exceptions":
        s0 = date.fromisoformat(a.start)
        sess = []
        for k in sorted({week_start(s0), week_start(s0 + timedelta(days=a.days))}):
            sess += plan_week(k, uni, personal, st)["sessions"]
        ex = exceptions(s0, a.days, uni, personal, sess)
        print("\n".join(f"{x['date']} · {x['text']}" for x in ex)) if a.human else pr(ex)

    elif a.cmd == "day":
        d = date.fromisoformat(a.date)
        p = plan_week(week_start(d), uni, personal, st)
        pr({"date": d.isoformat(),
            "profile": {k: (iso(v) if isinstance(v, datetime) else v) for k, v in day_profile(d, uni).items()},
            "lessons": [{"course": l.course, "name": COURSES[l.course]["name"], "kind": l.kind, "start": iso(l.start),
                         "end": iso(l.end), "location": l.location} for l in uni.lessons if l.day == d],
            "sessions": [s for s in p["sessions"] if s["start"][:10] == d.isoformat()],
            "touchpoints": [t for t in p["touchpoints"] if t["at"][:10] == d.isoformat()]})

    elif a.cmd == "review-data":
        pr(review_data(date.fromisoformat(a.date), uni, personal, st))

    elif a.cmd == "week-report":
        pr(week_report(week_start(date.fromisoformat(a.week)), st, now))

    elif a.cmd == "course-kind":
        # --course econ --text refresh|cumulative
        st.setdefault("course_kind", {})[a.course] = a.text
        save_state(st, out_path)
        pr(st["course_kind"])

    elif a.cmd == "feed-check":
        today = date.fromisoformat(a.today) if a.today else now.date()
        r = feed_check(uni, len(raw_events) if a.events else None, st, today, now, a.write)
        ov = lesson_overrides(uni, personal)
        r["skipped"] = [dict(v, key=k) for k, v in ov["skipped"].items() if v["date"] >= today.isoformat()]
        r["duplicates"] = [dict(v, key=k) for k, v in ov["duplicates"].items() if v["date"] >= today.isoformat()]
        if a.write and not r["block"]:
            save_state(st, out_path)
        pr(r)
        if r["block"]:
            sys.exit(3)

    elif a.cmd == "version":
        print(VERSION)

    elif a.cmd == "set-event":
        idx = state_sessions_index(st)
        if a.key not in idx:
            sys.exit(f"מזהה לא קיים במצב: {a.key}")
        idx[a.key]["eventId"] = a.id
        save_state(st, out_path)
        pr(idx[a.key])

    elif a.cmd == "move":
        idx = state_sessions_index(st)
        if a.key not in idx:
            sys.exit(f"מזהה לא קיים במצב: {a.key}")
        s0 = datetime.fromisoformat(a.start)
        s0 = s0.replace(tzinfo=TZ) if s0.tzinfo is None else s0
        idx[a.key]["start"] = iso(s0)
        idx[a.key]["end"] = iso(s0 + timedelta(minutes=CONFIG["session_min"]))
        save_state(st, out_path)
        pr(idx[a.key])

    elif a.cmd == "adopt":
        data = json.load(open(a.existing, encoding="utf-8"))
        evs = data.get("events", data) if isinstance(data, dict) else data
        ws0 = datetime.fromisoformat(a.start).replace(tzinfo=TZ) if a.start else now - timedelta(days=7)
        we0 = ws0 + timedelta(days=a.days)
        ch = adopt_calendar(st, evs, ws0, we0)
        save_state(st, out_path)
        pr({"changes": ch})

    elif a.cmd == "exc-notify":
        r = exc_notify(date.fromisoformat(a.today), uni, personal, st, a.write)
        if a.write:
            save_state(st, out_path)
        pr(r)

    elif a.cmd == "exc-ack":
        st.setdefault("acked", [])
        st["acked"] = sorted(set(st["acked"]) | {a.id})
        save_state(st, out_path)
        pr(st["acked"])

    elif a.cmd == "reschedule":
        r = reschedule(date.fromisoformat(a.today), uni, personal, st, now)
        if a.write:
            apply_reschedule(st, r)
            save_state(st, out_path)
        pr(r)

    elif a.cmd == "mark":
        idx = state_sessions_index(st)
        if a.key not in idx:
            sys.exit(f"מזהה לא קיים במצב: {a.key}")
        idx[a.key]["status"] = a.status
        idx[a.key]["marked_at"] = iso(now)
        save_state(st, out_path)
        pr(idx[a.key])

    elif a.cmd == "recap":
        rec = {"date": a.date or now.date().isoformat(), "course": a.course, "rating": a.rating,
               "topics": a.topics, "unclear": a.unclear, "at": iso(now)}
        st["recaps"] = [r for r in st["recaps"] if not (r["date"] == rec["date"] and r["course"] == rec["course"])] + [rec]
        save_state(st, out_path)
        pr(rec)

    elif a.cmd == "assignment":
        if a.id and a.status:
            for x in st["assignments"]:
                if x["id"] == a.id:
                    x["status"] = a.status
            save_state(st, out_path)
            pr([x for x in st["assignments"] if x["id"] == a.id])
        else:
            aid = a.id or f"{a.course}-{len(st['assignments']) + 1}"
            x = {"id": aid, "course": a.course, "title": a.title, "due": a.due, "est_min": a.est, "status": "open", "added": iso(now)}
            st["assignments"].append(x)
            save_state(st, out_path)
            pr(x)

    elif a.cmd == "pin":
        st["pins"] = [p for p in st["pins"] if p["key"] != a.key] + [{"key": a.key, "start": a.start}]
        save_state(st, out_path)
        pr(st["pins"])

    elif a.cmd == "question":
        if a.clear:
            st["pending_questions"] = [q for q in st["pending_questions"] if q.get("id") != a.id]
        else:
            st["pending_questions"].append({"id": a.id or f"q{len(st['pending_questions']) + 1}", "text": a.text, "asked": iso(now)})
        save_state(st, out_path)
        pr(st["pending_questions"])

    elif a.cmd == "log-run":
        st["runs"].append({"run": a.run, "at": iso(now), "ok": a.ok == "yes", "notes": a.text or ""})
        save_state(st, out_path)
        pr(st["runs"][-1])


if __name__ == "__main__":
    main()
