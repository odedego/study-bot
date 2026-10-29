"""
v2: המלצות והצעות במקום שיבוץ. פונקציות טהורות (בלי רשת, בלי טלגרם, בלי יומן), כדי שאפשר יהיה לבדוק אותן לעומק.

  recommend()        אילו קורסים כדאי לחזק עכשיו, ולמה
  open_gaps()        פערים: שיעור שהיית בו (או לא) ושאחריו לא נרשם לימוד בקורס
  free_windows()     חלונות פנויים באורך משתנה ביום נתון
  evening_offer()    חלונות למחר, כשמחר יום בלי לימודים או חצי יום פנוי (למשל חמישי עד 13:45)
  minutes_for()      כמה דקות להציע: מה שעודד אמר, אחר כך מה שנלמד מהשימוש, אחר כך ברירת מחדל לפי קורס
  log_study()        רישום לימוד בפועל ביומן, וסגירת פערים

החלטות של עודד: שבת סגורה, אין הצעה אחרי 23:00, שישי עד 16:00, יום חמישי חצי יום פנוי (לימוד עד 13:45),
קיבולת 0 ליום = אין הצעות באותו יום, חשבונאות "בלי פערים".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from statistics import median

import planner as P
from bot import semester as SEM

MIN_WINDOW = 25            # חלון קצר מזה לא מוצע
MAX_WINDOW = 120           # חלון אחד מוצע עד שעתיים
BEFORE_LESSON_MIN = 10     # סיום חלון לפני שיעור
AFTER_LESSON_MIN = 30      # תחילת חלון אחרי שיעור
WINDOW_START = "09:00"
EVENING_LIMIT = "22:00"    # חלון מתחיל עד השעה הזו ומסתיים עד 23:00
HALF_DAY_FROM = 12         # יום שהשיעור הראשון בו מתחיל ב-12:00 ואילך (חמישי 13:45) הוא חצי יום פנוי
GAP_LOOKBACK_DAYS = 21


# ------------------------------------------------------------------ אורך סשן
def minutes_for(course: str, st: dict, said: int | None = None) -> int:
    """מה שעודד אמר קובע. אחרת החציון של הדיווחים האחרונים (עד 3), מעוגל ל-5. אחרת ברירת מחדל לקורס."""
    if said and said > 0:
        return int(said)
    rows = [r["min"] for r in st.get("log", []) if r.get("course") == course and r.get("min") and not r.get("est")][-3:]
    if rows:
        return max(10, int(round(median(rows) / 5.0) * 5))
    return SEM.COURSE_POLICY.get(course, {}).get("default_min", 40)


# ------------------------------------------------------------------ יומן לימוד ופערים
def log_study(st: dict, day: str, course: str, minutes: int | None, topic: str | None, src: str, at: str,
              session_key: str | None = None) -> dict:
    """רושם לימוד בפועל. דקות חסרות נרשמות כהערכה (est=True) כדי שלא ילמדו מהן אורך."""
    row = {"date": day, "course": course, "min": minutes or SEM.COURSE_POLICY.get(course, {}).get("default_min", 40),
           "est": not minutes, "topic": (topic or "").strip()[:120], "src": src, "at": at}
    if session_key:
        row["session"] = session_key
    st.setdefault("log", []).append(row)
    return row


def open_gaps(st: dict, today: date, uni: P.Uni, now: datetime) -> list[dict]:
    """שיעור שהסתיים ושאחריו (באותו יום או אחריו) אין רישום לימוד באותו קורס. גם החמצת שיעור היא פער."""
    out, seen = [], set()
    lo = today - timedelta(days=GAP_LOOKBACK_DAYS)
    studied: dict[str, list[str]] = {}
    for r in st.get("log", []):
        studied.setdefault(r.get("course"), []).append(r.get("date", ""))
    for s in st.get("sessions", []):          # סשן ישן שסומן כבוצע נחשב לימוד (תאימות לאחור)
        if s.get("status") == "done" and s.get("course"):
            studied.setdefault(s["course"], []).append(s["start"][:10])
    for l in sorted(uni.lessons, key=lambda x: x.start):
        if l.course not in P.COURSES or not (lo <= l.day <= today) or l.end > now:
            continue
        key = (l.course, l.day)
        if key in seen:
            continue
        seen.add(key)
        att = st.get("bot", {}).get("attend", {}).get(f"{l.course}|{l.day.isoformat()}")
        if any(d >= l.day.isoformat() for d in studied.get(l.course, [])):
            continue
        out.append({"course": l.course, "day": l.day.isoformat(), "absent": att == "absent", "attend": att})
    return out


# ------------------------------------------------------------------ המלצות
def recommend(st: dict, today: date, uni: P.Uni, now: datetime, limit: int = 2) -> list[dict]:
    """ציון לכל קורס. רק קורסים עם ציון חיובי. מקסימום limit."""
    per: dict[str, dict] = {}

    def row(c):
        return per.setdefault(c, {"course": c, "score": 0, "why": []})
    lo = (today - timedelta(days=14)).isoformat()
    for r in st.get("recaps", []):
        c, rt = r.get("course"), r.get("rating")
        if c not in P.COURSES or r.get("date", "") < lo:
            continue
        if rt and rt <= 3:
            x = row(c)
            x["score"] += 4 - rt
            x["why"].append(f"הבנה {rt}/5")
        if (r.get("unclear") or "").strip():
            x = row(c)
            x["score"] += 1
            if "יש מה שלא ברור" not in x["why"]:
                x["why"].append("יש מה שלא ברור")
    for g in open_gaps(st, today, uni, now)[:8]:
        policy = SEM.COURSE_POLICY.get(g["course"], {})
        x = row(g["course"])
        x["score"] += 4 if policy.get("no_gaps") else 2
        d = date.fromisoformat(g["day"])
        x["why"].append(("פער מהשיעור של " if not g["absent"] else "החמצת את השיעור של ") + f"{d.day}.{d.month}")
    for c, pol in SEM.COURSE_POLICY.items():
        if not pol.get("pop_quiz"):
            continue
        nxt = min((l.day for l in uni.lessons if l.course == c and l.day > today), default=None)
        if nxt and (nxt - today).days <= 2 and c in per:
            per[c]["score"] += 1
            per[c]["why"].append("בוחן בלי הודעה בשיעור הבא")
    for c, _dt, n in SEM.upcoming_exams(today):
        if c in per:
            bonus = 2 if n <= 21 else 1 if n <= 42 else 0
            if bonus:
                per[c]["score"] += bonus
                per[c]["why"].append(f"מבחן בעוד {n} ימים")
    for a in st.get("assignments", []):
        if a.get("status") == "open" and a.get("course") in P.COURSES and a.get("due"):
            n = (date.fromisoformat(a["due"]) - today).days
            if 0 <= n <= 3:
                x = row(a["course"])
                x["score"] += 3
                x["why"].append(f"מטלה עד {date.fromisoformat(a['due']).day}.{date.fromisoformat(a['due']).month}")
    out = [x for x in per.values() if x["score"] > 0]
    out.sort(key=lambda x: (-x["score"], P.COURSE_ORDER.index(x["course"])))
    return out[:limit]


# ------------------------------------------------------------------ חלונות
def day_kind(day: date, uni: P.Uni) -> str:
    """closed (שבת, או יום ללא לימודים לפי האוניברסיטה) | free | half (שיעור ראשון מ-12:00, כמו חמישי) | busy"""
    if day.weekday() in P.CONFIG["closed_weekdays"]:
        return "closed"
    ls = [l for l in uni.lessons if l.day == day]
    if not ls:
        return "free"
    first = min(l.start for l in ls)
    return "half" if first.hour >= HALF_DAY_FROM else "busy"


def _ceil(dt: datetime, step: int = 15) -> datetime:
    return P._round_up(dt, step)


def free_windows(day: date, uni: P.Uni, personal: list, st: dict, now: datetime, max_n: int = 3) -> list[tuple[datetime, datetime]]:
    """חלונות פנויים ביום, באורך משתנה (25 עד 120 דקות). בלי שבת, בלי 23:00, שישי עד 16:00, בלי יום עם קיבולת 0.
    חמישי: הבוקר פנוי עד 10 דקות לפני השיעור הראשון (13:45)."""
    if day_kind(day, uni) == "closed" or day in uni.no_study_days:
        return []
    if st.get("capacity", {}).get(day.isoformat()) == 0:
        return []
    wd = day.weekday()
    start = P.at(day, P.hm(WINDOW_START))
    if day == now.date():
        start = max(start, now + timedelta(minutes=30))
    end = P.at(day, P.hm(P.CONFIG["friday_end_limit"] if wd == P.FRI else P.CONFIG["hard_end"]))
    last_start = P.at(day, P.hm(EVENING_LIMIT)) if wd != P.FRI else end
    blocks: list[tuple[datetime, datetime]] = []
    for l in uni.lessons:
        if l.day == day:
            blocks.append((l.start - timedelta(minutes=BEFORE_LESSON_MIN), l.end + timedelta(minutes=AFTER_LESSON_MIN)))
    m = timedelta(minutes=P.CONFIG["personal_margin_min"])
    for e in personal:
        if e.transparent or e.all_day or not (e.start.date() <= day <= e.end.date()):
            continue
        blocks.append((e.start - m, e.end + m))
    for s in st.get("sessions", []):
        if s.get("status") == "planned" and s["start"][:10] == day.isoformat():
            blocks.append((datetime.fromisoformat(s["start"]) - timedelta(minutes=10), datetime.fromisoformat(s["end"]) + timedelta(minutes=10)))
    blocks.sort()
    gaps, t = [], start
    for b0, b1 in blocks:
        if b1 <= t:
            continue
        if b0 > t:
            gaps.append((t, b0))
        t = max(t, b1)
    if t < end:
        gaps.append((t, end))
    cands = []
    for g0, g1 in gaps:
        w0 = _ceil(g0)
        w1 = min(g1, end, w0 + timedelta(minutes=MAX_WINDOW))
        if w0 > last_start or (w1 - w0) < timedelta(minutes=MIN_WINDOW):
            continue
        cands.append((w0, w1))
    cands.sort(key=lambda w: -(w[1] - w[0]).total_seconds())
    return sorted(cands[:max_n])


def evening_offer(today: date, uni: P.Uni, personal: list, st: dict, now: datetime) -> dict | None:
    """מחר (ולא שבת) יום בלי לימודים או חצי יום פנוי: מציעים חלונות. אחרת None."""
    tm = today + timedelta(days=1)
    kind = day_kind(tm, uni)
    if kind not in ("free", "half"):
        return None
    ws = free_windows(tm, uni, personal, st, now)
    if not ws:
        return None
    if kind == "half":      # חצי יום: רק חלונות שמסתיימים לפני השיעור הראשון
        first = min(l.start for l in uni.lessons if l.day == tm)
        ws = [w for w in ws if w[1] <= first]
        if not ws:
            return None
    return {"date": tm.isoformat(), "kind": kind, "windows": [[w[0].isoformat(), w[1].isoformat()] for w in ws]}
