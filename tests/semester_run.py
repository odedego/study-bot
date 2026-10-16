"""
ריצה ארוכה: כל הסמסטר והמבחנים (17.10.2026 – 20.2.2027) ברבע-שעות, עם עודד "ממושמע" שמסמן הכול בוצע.
מחפש קריסות, הודעות כשל, כפילויות, הפרות כללים, וגדילת מצב לאורך זמן.
הרצה: python3 -m tests.semester_run   (כמה דקות)
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from tests.features_test import make_env, run_until, t, st, bot_of  # noqa: E402
from tests.edge_test import audit, sessions_from_calendar  # noqa: E402
import tests.features_test as F  # noqa: E402

FAILS = []


def check(c, m):
    print(("  PASS " if c else "  FAIL ") + m)
    if not c:
        FAILS.append(m)


def main():
    e = make_env("2026-10-17T20:55")
    tg, cal, store = e["tg"], e["cal"], e["store"]
    sizes = []
    end = t("2027-02-20T08:00")
    while e["clock"].t < end:
        nxt = min(end, e["clock"].t + timedelta(days=7))
        run_until(e, nxt, step=15)
        sizes.append(len(store.blob))
    fails = [m["text"][:120] for m in tg.sent if m["text"].startswith("⚠️") and ("נכשל" in m["text"] or "משהו נכשל" in m["text"])]
    check(not fails, f"no failure alerts ({fails[:3]})")
    per = Counter()
    for m in tg.sent:
        for p in ("☀️", "🌙", "📊", "🚆 ריקאפ"):
            if m["text"].startswith(p):
                per[(p, m["at"].date())] += 1
    check(not [k for k, v in per.items() if v > 1], "one message per job per day")
    s = st(e)
    check(max(sizes) < 200_000, f"state stays small (max {max(sizes)} bytes, last {sizes[-1]})")
    b = bot_of(e)
    b.load_state()
    uni, _ = b.load_uni()
    sess = sessions_from_calendar(cal)
    audit([x for x in sess if date(2026, 10, 18) <= x[0].date() <= date(2027, 2, 19)], uni.lessons, [], "whole semester")
    mar = Counter(x["course"] for x in s["sessions"] if x.get("kind") == "marathon" and x.get("status") == "done")
    m = s.get("marathon", {})
    done = {c: m.get("archived_done", {}).get(c, 0) + mar.get(c, 0) for c in ("intro", "math", "stats", "law", "econ", "excel")}
    print("    marathon done per course:", done)
    check(all(v > 0 for v in done.values()), "every exam course got marathon sessions")
    exam_days = {F.SEM.exam_dt(c).date() for c in F.SEM.EXAMS}
    check(not [x for x in sess if "🏃" in x[2] and x[0].date() in exam_days], "no marathon session on exam days")
    print(f"    messages: {len(tg.sent)}, calendar events: {len(cal.cals.get('reviews@cal', {}))}")
    print("\nFAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
