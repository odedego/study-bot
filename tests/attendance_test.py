"""נוכחות והיעדרויות. הרצה: python3 -m tests.attendance_test"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from tests.fakes import Clock, FakeCalendar, FakeFeed, FakeLLM, FakeTelegram, MemStore, freeze_planner  # noqa: E402
from bot.core import Bot  # noqa: E402

TZ = P.TZ
FAILS = []


def check(c, m):
    print(("  PASS " if c else "  FAIL ") + m)
    if not c:
        FAILS.append(m)


def main():
    clock = Clock(datetime(2026, 10, 25, 13, 0, tzinfo=TZ))
    freeze_planner(clock)
    tg, cal, feed, store, llm = FakeTelegram(), FakeCalendar(), FakeFeed([]), MemStore(), FakeLLM()

    def tick():
        b = Bot(tg, cal, feed, store, llm, {"REVIEWS_CAL": "r", "PERSONAL_CAL": ""}, clock)
        b.now = clock()
        b.load_state()
        try:
            b.handle_updates()
        finally:
            b.save_state()
        return b

    def press(d):
        tg.press(100, d)
        tick()

    texts = lambda: [m["text"] for m in tg.sent]
    for i in range(1, 4):
        press(f"att|math|2026-10-{25 + i - 1:02d}|absent")
    check(not any("האחרונה" in t or "עברת" in t for t in texts()[:0]), "setup")
    check(any("זו ההיעדרות האחרונה המותרת" in t and "נעדרת 3 מתוך 3 מותרות" in t for t in texts()), "warning at the last allowed absence (math 3/3)")
    press("att|math|2026-10-28|absent")
    check(any("מעבר למגבלת ההיעדרויות" in t and "נעדרת 4 מתוך 3 מותרות" in t for t in texts()), "over-limit alert")
    press("att|econ|2026-10-25|reserve")
    b = tick()
    check(b.absences("econ") == 0, "reserve duty does not count as absence")
    press("att|mgmt|2026-10-28|absent")
    check(any("ניהול בארגונים: נוכחות חובה בכל מפגש" in t for t in texts()), "mgmt: attendance is mandatory, no exam claim")
    check(not any("ניהול" in t and "מבחן" in t and "חוסם" in t for t in texts()), "mgmt: never says an exam is blocked")
    press("att|math|2026-10-25|present")
    check(tick().absences("math") == 3, "changing a day from absent to present updates count")
    tg.user_text("היעדרויות")
    tick()
    check("מתמטיקה: נעדרת 3 מתוך 3 מותרות" in tg.sent[-1]["text"] and "כלכלה: נעדרת 0 מתוך 4 מותרות (+1 מילואים)" in tg.sent[-1]["text"], "command shows table")
    llm.script.append({"actions": [{"action": "attendance", "course": "intro", "date": "2026-10-28", "status": "absent"}], "reply": "רשמתי"})
    tg.user_text("לא הגעתי היום למבוא לחשבונאות")
    tick()
    check(tick().absences("intro") == 1, "free-text attendance action")
    print("FAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
