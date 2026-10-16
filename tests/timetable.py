"""
מערכת שעות סינתטית של סמסטר א' (אותה תבנית שבועית כמו הפיד האמיתי, בלי שמות מרצים),
כדי שהבדיקות ירוצו גם בלי uni.json. מחזיר אירועים בפורמט של Google Calendar.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import planner as P

LESSON = "סוג מפגש: שיעור חדר לימוד: (מתקיים) קורס"
CANCELLED = "סוג מפגש: שיעור חדר לימוד: השיעור בוטל"

# weekday של פייתון: 0=שני ... 6=ראשון
WEEKLY = [
    (0, "08:00", "09:30", "משפט לחשבונאים א'", "A206"),
    (0, "09:45", "13:00", "מתמטיקה לחשבונאים", "C.BO1"),
    (0, "13:45", "15:15", "סטטיסטיקה א' לחשבונאים", "C.BO1"),
    (2, "09:45", "13:00", "מבוא לחשבונאות", "C.L05"),
    (2, "13:45", "15:15", "ניהול בארגונים", "L101"),
    (3, "13:45", "15:15", "אקסל לחשבונאים א'", "A107"),
    (3, "15:45", "19:00", "כלכלה א' לחשבונאים", "C.B07"),
]
MGMT_DATES = {"2026-10-21", "2026-10-28", "2026-11-04", "2026-11-11", "2026-11-18", "2026-11-25",
              "2026-12-02", "2026-12-09", "2026-12-16", "2026-12-23", "2026-12-30"}


def _ev(uid, d, s, e, summary, desc, loc):
    a = datetime.combine(d, P.hm(s), tzinfo=P.TZ)
    b = datetime.combine(d, P.hm(e), tzinfo=P.TZ)
    return {"id": uid, "summary": summary, "description": desc, "location": loc,
            "start": {"dateTime": a.isoformat(), "timeZone": "Asia/Jerusalem"},
            "end": {"dateTime": b.isoformat(), "timeZone": "Asia/Jerusalem"}}


def semester(start=date(2026, 10, 18), end=date(2027, 1, 15)) -> list[dict]:
    out = []
    d = start
    while d <= end:
        for wd, s, e, name, loc in WEEKLY:
            if d.weekday() != wd:
                continue
            if name == "ניהול בארגונים" and d.isoformat() not in MGMT_DATES:
                continue
            uid = f"t{d:%m%d}{s.replace(':', '')}"
            if d == date(2026, 10, 22) and name.startswith("כלכלה"):
                out.append(_ev(uid, d, s, e, name + " ** השיעור מבוטל **", CANCELLED, loc))
            else:
                out.append(_ev(uid, d, s, e, name, LESSON, loc))
        d += timedelta(days=1)
    # השלמה חד-פעמית בשלישי 20.10 (כמו בפיד האמיתי)
    out.append(_ev("t1020mk", date(2026, 10, 20), "08:00", "11:15", "כלכלה א' לחשבונאים", LESSON, "ZOOM"))
    return out
