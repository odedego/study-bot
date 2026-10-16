"""
היומן האישי: הבוט משנה רק אחרי לחיצה על "אשר", ורק אם האירוע לא השתנה מאז ההצעה.
הרצה: python3 -m tests.personal_test
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from tests.fakes import Clock, FakeCalendar, FakeFeed, FakeLLM, FakeTelegram, MemStore, freeze_planner  # noqa: E402
from bot.core import Bot, sid  # noqa: E402

TZ = P.TZ
REV, PERS = "reviews@cal", "me@cal"
FAILS = []


def check(c, m):
    print(("  PASS " if c else "  FAIL ") + m)
    if not c:
        FAILS.append(m)


def t(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def main():
    clock = Clock(t("2026-10-20T12:00"))
    freeze_planner(clock)
    tg, cal, feed, store, llm = FakeTelegram(), FakeCalendar(), FakeFeed([]), MemStore(), FakeLLM()
    cal.add(PERS, {"id": "p1", "etag": "1", "summary": "רופא", "start": {"dateTime": "2026-10-28T10:00:00+02:00"}, "end": {"dateTime": "2026-10-28T11:00:00+02:00"}})
    cal.add(PERS, {"id": "p2", "etag": "1", "summary": "אימון", "start": {"dateTime": "2026-10-21T18:00:00+03:00"}, "end": {"dateTime": "2026-10-21T19:30:00+03:00"}})
    cal.add(PERS, {"id": "p3", "etag": "1", "summary": "חתונה", "start": {"date": "2026-10-23"}, "end": {"date": "2026-10-24"}})
    P1, P2, P3 = ("p" + sid(x)[:7] for x in ("p1", "p2", "p3"))

    def tick():
        b = Bot(tg, cal, feed, store, llm, {"REVIEWS_CAL": REV, "PERSONAL_CAL": PERS}, clock)
        b.now = clock()
        b.load_state()
        try:
            b.handle_updates()
        finally:
            b.save_state()
        return b

    def snapshot():
        return {k: dict(v) for k, v in cal.cals.get(PERS, {}).items()}

    def last_pp():
        return [m for m in tg.sent if m["text"].startswith("🗓️")][-1]

    print("== חסימה קשיחה: כתיבה ישירה ליומן האישי בלי אישור")
    b = tick()
    for fn in (lambda: b.cal.patch(PERS, "p1", {"summary": "x"}), lambda: b.cal.delete(PERS, "p1"),
               lambda: b.cal.create(PERS, {"summary": "x"})):
        try:
            fn()
            check(False, "write without approval was blocked")
        except PermissionError:
            check(True, "write without approval was blocked")
    check(cal.cals[PERS]["p1"]["summary"] == "רופא", "personal event untouched")

    print("== הזזה: הצעה, ואז אישור")
    before = snapshot()
    llm.script.append({"actions": [{"action": "personal", "op": "move", "event": P1, "start": "2026-10-28T12:00"}], "reply": "שלחתי הצעה לאישור"})
    tg.user_text("תזיז את הרופא של רביעי הבא ל-12")
    tick()
    check(snapshot() == before, "proposal alone changed nothing")
    m = last_pp()
    check("12:00" in m["text"] and "10:00" in m["text"], "proposal shows from/to")
    ok = tg.find_button(m["id"], lambda tx, d: d.startswith("pp|ok"))
    check(bool(ok), "approve button present")
    tg.press(m["id"], ok)
    tick()
    e = cal.cals[PERS]["p1"]
    check(e["start"]["dateTime"].startswith("2026-10-28T12:00") and e["end"]["dateTime"].startswith("2026-10-28T13:00"), "moved, duration kept")
    check("בוצע" in tg.msg(m["id"])["text"], "message says done")
    n = len(cal.calls)
    tg.press(m["id"], ok)
    tick()
    check(len(cal.calls) == n, "second press does nothing")

    print("== מחיקה: האירוע השתנה בין ההצעה לאישור → לא נוגעים")
    llm.script.append({"actions": [{"action": "personal", "op": "delete", "event": P2}], "reply": "שלחתי הצעה"})
    tg.user_text("תבטל את האימון מחר")
    tick()
    m = last_pp()
    cal.cals[PERS]["p2"]["etag"] = "9"   # עודד שינה את האירוע בעצמו בינתיים
    tg.press(m["id"], tg.find_button(m["id"], lambda tx, d: d.startswith("pp|ok")))
    tick()
    check("p2" in cal.cals[PERS], "changed event not deleted")
    check("השתנה" in tg.msg(m["id"])["text"], "user told it changed")

    print("== מחיקה רגילה עם אישור")
    llm.script.append({"actions": [{"action": "personal", "op": "delete", "event": P2}], "reply": "שלחתי הצעה"})
    tg.user_text("תבטל את האימון מחר")
    tick()
    m = last_pp()
    tg.press(m["id"], tg.find_button(m["id"], lambda tx, d: d.startswith("pp|ok")))
    tick()
    check("p2" not in cal.cals[PERS], "deleted after approval")

    print("== הוספה ואז ביטול")
    before = snapshot()
    llm.script.append({"actions": [{"action": "personal", "op": "create", "title": "ספר", "start": "2026-10-22T17:00", "end": "2026-10-22T17:30"}], "reply": "שלחתי"})
    tg.user_text("תוסיף לי ספר ביום חמישי ב-17")
    tick()
    m = last_pp()
    tg.press(m["id"], tg.find_button(m["id"], lambda tx, d: d.startswith("pp|no")))
    tick()
    check(snapshot() == before, "cancelled create added nothing")
    check("בוטל" in tg.msg(m["id"])["text"], "message says cancelled")

    print("== הצעה שפג תוקפה")
    llm.script.append({"actions": [{"action": "personal", "op": "create", "title": "ספר", "start": "2026-10-23T09:00"}], "reply": "שלחתי"})
    tg.user_text("תוסיף ספר בשישי ב-9")
    tick()
    m = last_pp()
    clock.t += timedelta(hours=25)
    tg.press(m["id"], tg.find_button(m["id"], lambda tx, d: d.startswith("pp|ok")))
    tick()
    check(snapshot() == before, "expired proposal not executed")

    print("== אירוע של יום שלם: לא מציעים")
    n = len([x for x in tg.sent if x["text"].startswith("🗓️")])
    llm.script.append({"actions": [{"action": "personal", "op": "delete", "event": P3}], "reply": "שלחתי"})
    tg.user_text("תמחק את החתונה")
    tick()
    check(len([x for x in tg.sent if x["text"].startswith("🗓️")]) == n, "no proposal for all-day event")
    check("יום שלם" in tg.sent[-1]["text"], "user told why")

    print("== הוספה עם אישור")
    clock.t = t("2026-10-21T08:00")
    llm.script.append({"actions": [{"action": "personal", "op": "create", "title": "ספר", "start": "2026-10-22T17:00"}], "reply": "שלחתי"})
    tg.user_text("תוסיף ספר בחמישי ב-17")
    tick()
    m = last_pp()
    tg.press(m["id"], tg.find_button(m["id"], lambda tx, d: d.startswith("pp|ok")))
    tick()
    new = [e for e in cal.cals[PERS].values() if e["summary"] == "ספר"]
    check(len(new) == 1 and new[0]["end"]["dateTime"].startswith("2026-10-22T18:00"), "created after approval (default 60 min)")

    check(not [c for c in cal.calls if c[0] in ("create", "patch") and c[1] == REV], "reviews calendar untouched by these flows")
    print("FAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
