"""
חזרה מרווחת: נושא שנלמד ועוד לא נחזר במרווחים 2 · 7 · 21 ימים (מהלימוד או מהחזרה האחרונה) מוצע לחזרה.
פונקציות טהורות על מפת החומר. רק מציע. עודד קובע מתי.
"""
from __future__ import annotations

from datetime import date

import planner as P
from bot import topics as TOP

INTERVALS = (2, 7, 21)


def due(st: dict, today: date, course: str | None = None, limit: int = 3) -> list[dict]:
    """נושאים שהגיע זמן לחזור עליהם. חלשים קודם, אחר כך הכי מאוחרים. כל נושא פעם אחת."""
    out = []
    for c in ([course] if course else P.COURSE_ORDER):
        for i, t in TOP.live(st, c):
            if not t["learned"]:
                continue
            base = t["learned"][-1]                      # לימוד חוזר מאפס את המרווחים
            revs = sorted({r["d"] for r in t["reviewed"] if r["d"] >= base})
            n = len(revs)
            if n >= len(INTERVALS):
                continue
            last = max([base] + revs)
            days = (today - date.fromisoformat(last)).days
            if days >= INTERVALS[n]:
                out.append({"course": c, "idx": i, "name": t["name"], "days": days, "n": n, "late": days - INTERVALS[n], "weak": TOP.is_weak(t)})
    out.sort(key=lambda x: (not x["weak"], -x["late"]))
    return out[:limit]


def short(name: str) -> str:
    return name.split(":")[0][:40]
