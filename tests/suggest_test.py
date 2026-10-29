"""
PR C/D: מודל נתונים v2 והפונקציות הטהורות של ההצעות (bot/suggest.py). בלי רשת, בלי טלגרם.
הרצה: python3 -m tests.suggest_test
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot import suggest as S  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def L(course, d, s, e):
    return P.Lesson(course, P.at(d, P.hm(s)), P.at(d, P.hm(e)), "שיעור", "", [])


def uni(lessons, nsd=None):
    return P.Uni(lessons, [], nsd or {}, [], [])


def hhmm(dt):
    return dt.strftime("%H:%M")


SUN, MON, THU, FRI, SAT = date(2026, 11, 1), date(2026, 11, 2), date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 7)
courses = list(P.COURSES)
C1, C2 = courses[0], courses[1]


def main():
    print("== ימים וחלונות ==")
    u = uni([L(C1, MON, "09:00", "11:00"), L(C2, THU, "13:45", "15:15")])
    check(S.day_kind(SAT, u) == "closed", "שבת סגורה")
    check(S.day_kind(THU, u) == "half", "חמישי עם שיעור ב-13:45 = חצי יום פנוי")
    check(S.day_kind(MON, u) == "busy", "יום עם שיעור בבוקר = עמוס")
    check(S.day_kind(SUN, u) == "free", "יום בלי שיעורים = פנוי")
    now = P.at(date(2026, 10, 29), P.hm("21:30"))
    w = S.free_windows(THU, u, [], {}, now)
    check(bool(w) and all(b <= P.at(THU, P.hm("13:35")) for _, b in w[:1]), "חמישי: חלון הבוקר מסתיים עד 13:35")
    check(w and hhmm(w[0][0]) == "09:00", "חמישי: חלון ראשון מתחיל ב-09:00")
    check(all((b - a) <= timedelta(minutes=S.MAX_WINDOW) and (b - a) >= timedelta(minutes=S.MIN_WINDOW) for a, b in w), "אורכי חלון בטווח")
    check(S.free_windows(SAT, u, [], {}, now) == [], "שבת: אין חלונות")
    check(S.free_windows(SUN, u, [], {"capacity": {SUN.isoformat(): 0}}, now) == [], "קיבולת 0: אין חלונות")
    check(S.free_windows(SUN, uni([], {SUN: "חג"}), [], {}, now) == [], "יום בלי לימודים רשמי: אין חלונות")
    fw = S.free_windows(FRI, u, [], {}, now)
    check(all(b <= P.at(FRI, P.hm("16:00")) for _, b in fw), "שישי עד 16:00")
    sw = S.free_windows(SUN, u, [], {}, now, max_n=10)
    check(all(b <= P.at(SUN, P.hm("23:00")) for _, b in sw), "לא עובר 23:00")
    personal = [P.Ev("p", "רופא", "", "", P.at(SUN, P.hm("10:00")), P.at(SUN, P.hm("12:00")))]
    pw = S.free_windows(SUN, u, personal, {}, now, max_n=10)
    check(all(not (a < P.at(SUN, P.hm("12:00")) and b > P.at(SUN, P.hm("10:00"))) for a, b in pw), "אירוע פרטי חוסם")
    sess = {"sessions": [{"status": "planned", "start": P.at(SUN, P.hm("09:00")).isoformat(), "end": P.at(SUN, P.hm("10:00")).isoformat()}]}
    check(all(a >= P.at(SUN, P.hm("10:10")) for a, _ in S.free_windows(SUN, u, [], sess, now)), "סשן קיים חוסם")

    print("== הצעת ערב ==")
    wed = date(2026, 11, 4)
    o = S.evening_offer(wed, u, [], {}, P.at(wed, P.hm("21:30")))
    check(o and o["kind"] == "half" and o["windows"], "ערב רביעי: מציע לחמישי")
    check(o and all(datetime.fromisoformat(b) <= P.at(THU, P.hm("13:45")) for _, b in o["windows"]), "חלונות חמישי לפני 13:45")
    check(S.evening_offer(THU, u, [], {}, P.at(THU, P.hm("21:30"))) is not None, "ערב חמישי: שישי פנוי")
    check(S.evening_offer(FRI, u, [], {}, P.at(FRI, P.hm("21:30"))) is None, "ערב שישי: שבת סגורה, אין הצעה")
    check(S.evening_offer(date(2026, 11, 1), u, [], {}, P.at(date(2026, 11, 1), P.hm("21:30"))) is None, "מחר עמוס: אין הצעה")

    print("== אורך סשן ==")
    st = {"log": []}
    check(S.minutes_for("intro", st) == 45, "ברירת מחדל לפי קורס")
    check(S.minutes_for("intro", st, 20) == 20, "מה שנאמר גובר")
    for m in (30, 40, 50, 60):
        S.log_study(st, "2026-10-30", "intro", m, None, "t", "x")
    check(S.minutes_for("intro", st) == 50, "חציון 3 אחרונים")
    S.log_study(st, "2026-10-30", "intro", None, None, "t", "x")
    check(S.minutes_for("intro", st) == 50 and st["log"][-1]["est"], "הערכה לא משפיעה על למידה")

    print("== פערים והמלצות ==")
    t0 = date(2026, 11, 3)
    now2 = P.at(t0, P.hm("20:00"))
    u2 = uni([L("intro", MON, "09:00", "11:00"), L("math", MON, "11:15", "13:00"), L("intro", date(2026, 11, 4), "09:00", "11:00")])
    st2 = {"log": [], "sessions": [], "bot": {"attend": {f"math|{MON.isoformat()}": "absent"}}, "recaps": [], "assignments": []}
    gaps = S.open_gaps(st2, t0, u2, now2)
    check({g["course"] for g in gaps} == {"intro", "math"}, "פער בכל קורס שהיה לו שיעור בלי לימוד")
    check(any(g["course"] == "math" and g["absent"] for g in gaps), "החמצה מסומנת")
    S.log_study(st2, MON.isoformat(), "intro", 40, None, "t", "x")
    check({g["course"] for g in S.open_gaps(st2, t0, u2, now2)} == {"math"}, "לימוד סוגר פער")
    rec = S.recommend(st2, t0, u2, now2, limit=3)
    check(rec and rec[0]["course"] == "math" and rec[0]["why"], "המלצה: הקורס עם הפער הפתוח")
    st3 = dict(st2, recaps=[{"date": "2026-11-02", "course": "econ", "rating": 2, "unclear": "ביקוש", "topics": ""}])
    r3 = S.recommend(st3, t0, u2, now2, limit=3)
    check(any(x["course"] == "econ" for x in r3), "הבנה נמוכה מזנקת להמלצה")
    check(S.recommend({"log": [], "sessions": [], "bot": {}, "recaps": [], "assignments": []}, t0, uni([]), now2) == [], "בלי נתונים: אין המלצה")

    print("== מצב ותחזוקה ==")
    ns = P.new_state()
    check(ns["offers"] == {} and ns["log"] == [] and ns["prefs"] == {"dur": {}}, "new_state כולל מפתחות v2")
    ns["log"] = [{"date": "2020-01-01", "course": "intro"}, {"date": "2026-10-01", "course": "intro"}]
    ns["offers"] = {"a": {"date": "2020-01-01", "state": "expired"}, "b": {"date": "2020-01-01", "state": "pending"}}
    P.compact_state(ns, P.at(date(2026, 10, 29), P.hm("21:00")))
    c = ns
    check(len(c["log"]) == 1, "יומן ישן נגזם")
    check("a" not in c["offers"] and "b" in c["offers"], "הצעה ישנה שפגה נגזמת, ממתינה נשארת")
    check(P.SESSION_FIELDS[-2:] == ("min", "src") or {"min", "src"} <= set(P.SESSION_FIELDS), "min/src שורדים דחיסה")

    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
