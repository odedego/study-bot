"""מבחנים וסילבוס. הרצה: python3 -m tests.semester_test"""
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
    clock = Clock(datetime(2026, 10, 16, 13, 0, tzinfo=TZ))
    freeze_planner(clock)
    tg, cal, feed, store, llm = FakeTelegram(), FakeCalendar(), FakeFeed([]), MemStore(), FakeLLM()

    def bot():
        b = Bot(tg, cal, feed, store, llm, {"REVIEWS_CAL": "r", "PERSONAL_CAL": ""}, clock)
        b.now = clock()
        b.load_state()
        return b

    b = bot()
    tg.user_text("מבחנים")
    b.handle_updates()
    b.save_state()
    t = tg.sent[-1]["text"]
    check("מבוא לחשבונאות: בעוד 101 ימים (שני 25.1, 09:45)" in t, "exams command: countdown and weekday")
    check("אקסל: בעוד 126 ימים" in t and "מועד ב':" in t, "exams command lists all + moed B")
    check("הסמסטר נגמר ב-15.1 (בעוד 91 ימים)" in t, "semester end shown")

    clock.t = datetime(2027, 1, 10, 7, 44, tzinfo=TZ)
    b = bot()
    b.job_morning(clock.t.date())
    b.save_state()
    check("⏳ מבוא לחשבונאות: בעוד 15 ימים" in tg.sent[-1]["text"], "morning countdown within 21 days")

    clock.t = datetime(2027, 1, 25, 7, 44, tzinfo=TZ)
    b = bot()
    b.job_morning(clock.t.date())
    check("🍀 בהצלחה! מבוא לחשבונאות: היום" in tg.sent[-1]["text"], "exam-day good luck")

    clock.t = datetime(2026, 12, 12, 21, 0, tzinfo=TZ)
    b = bot()
    b.job_weekly(clock.t.date())
    b.save_state()
    check("🎯" not in tg.sent[-1]["text"], "no prep notice at 44 days")
    clock.t = datetime(2026, 12, 19, 21, 0, tzinfo=TZ)
    b = bot()
    b.job_weekly(clock.t.date())
    b.save_state()
    check("🎯 נשארו פחות מ-6 שבועות" in tg.sent[-1]["text"], "prep notice at 37 days")
    clock.t = datetime(2026, 12, 26, 21, 0, tzinfo=TZ)
    b = bot()
    b.job_weekly(clock.t.date())
    check("🎯" not in tg.sent[-1]["text"], "prep notice only once")
    a = [x for x in b.st["assignments"] if x.get("source") == "syllabus"]
    check(len(a) == 2 and {x["due"] for x in a} == {"2027-01-03", "2027-01-13"}, "mgmt deadlines present once")
    print("FAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
