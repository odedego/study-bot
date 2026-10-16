"""
בדיקות קצה על שבועיים מלאים (17.10–31.10), כולל המעבר לשעון חורף ב-25.10:
  - ביקורת על היומן שנוצר: תקרה שבועית, חפיפות, 23:00, שישי, שבת, ימים ארוכים, רווחים בין שיעורים
  - שעון חורף: הודעות הבוקר, הריקאפ והסיכום היומי יוצאים בשעה המקומית הנכונה
  - טלגרם נופל באמצע משימה: אין הודעה כפולה ואין אירוע כפול
  - כפתור מצ׳אט זר, הודעות שאינן טקסט

הרצה: python3 -m tests.edge_test   (לא צריך uni.json: משתמש במערכת סינתטית, tests/timetable.py)
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot.core import Bot  # noqa: E402
from tests.fakes import Clock, FakeCalendar, FakeFeed, FakeLLM, FakeTelegram, MemStore, freeze_planner  # noqa: E402
from tests.timetable import semester  # noqa: E402

TZ = P.TZ
REV, PERS = "reviews@cal", "me@cal"
FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def t(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


class TimedTelegram(FakeTelegram):
    """כמו FakeTelegram, אבל זוכר מתי כל הודעה נשלחה, ויודע להיכשל לפי בקשה."""
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.fail_sends = 0

    def send(self, text, buttons=None, chat_id=None):
        if self.fail_sends:
            self.fail_sends -= 1
            raise RuntimeError("telegram send: 502")
        mid = super().send(text, buttons, chat_id)
        self.sent[-1]["at"] = self.clock.t
        return mid


def setup(start="2026-10-17T20:55"):
    seed = json.load(open(os.path.join(os.path.dirname(__file__), "..", "bot", "seed_state.json"), encoding="utf-8"))
    cal = FakeCalendar()
    for s in seed["sessions"]:
        sess = P._session_from_state(s)
        cal.add(REV, {"id": s["eventId"], "summary": sess.title(), "description": f"...\nמזהה: {s['key']}",
                      "start": {"dateTime": s["start"]}, "end": {"dateTime": s["end"]}})
    # אירוע אישי בשלישי בבוקר (יום פנוי): אסור לשבץ עליו
    cal.add(PERS, {"id": "dentist", "summary": "רופא שיניים", "etag": "1",
                   "start": {"dateTime": "2026-10-27T10:00:00+02:00"}, "end": {"dateTime": "2026-10-27T11:00:00+02:00"}})
    clock = Clock(t(start))
    freeze_planner(clock)
    tg = TimedTelegram(clock)
    return clock, tg, cal, FakeFeed(semester()), MemStore(), FakeLLM()


def tick(env):
    clock, tg, cal, feed, store, llm = env
    bot = Bot(tg, cal, feed, store, llm, {"REVIEWS_CAL": REV, "PERSONAL_CAL": PERS}, clock)
    bot.now = clock()
    bot.load_state()
    try:
        bot.handle_updates()
        bot.run_jobs()
    finally:
        bot.save_state()
    return bot


def run_until(env, end, step=5, answer_daily=True):
    clock, tg = env[0], env[1]
    while clock.t < end:
        clock.t += timedelta(minutes=step)
        n = len(tg.sent)
        tick(env)
        if answer_daily:   # עודד מסמן "הכול בוצע" בסיכום היומי, כמו ביום רגיל
            for m in tg.sent[n:]:
                if m["text"].startswith(("🌙", "🗓 סמן")):
                    d = m["buttons"] and tg.find_button(m["id"], lambda tx, dt: dt.startswith("alldone"))
                    if d:
                        tg.press(m["id"], d)


def sessions_from_calendar(cal):
    out = []
    for e in cal.cals.get(REV, {}).values():
        if e["summary"].startswith("↪️"):
            continue   # הועבר: האירוע המקורי נשאר מסומן, לא תופס זמן
        out.append((datetime.fromisoformat(e["start"]["dateTime"]).astimezone(TZ),
                    datetime.fromisoformat(e["end"]["dateTime"]).astimezone(TZ), e["summary"], e.get("description", "")))
    return sorted(out)


def audit(sess, lessons, personal, label):
    """הכללים מהאפיון, על מה שבאמת יושב ביומן."""
    by_week = defaultdict(int)
    by_day = defaultdict(list)
    for s, e, title, desc in sess:
        if " · חזרה על " in title or " · השלמה על " in title:   # רק החזרות הרגילות נספרות בתקרה (תוספות ומרתון לא)
            by_week[P.week_start(s.date())] += 1
        by_day[s.date()].append((s, e, title))
    check(all(n <= 11 for n in by_week.values()), f"{label}: ≤11 sessions per week {dict((k.isoformat(), v) for k, v in by_week.items())}")
    over = [(a[2], b[2]) for i, a in enumerate(sess) for b in sess[i + 1:] if b[0] < a[1] and a[0] < b[1]]
    check(not over, f"{label}: no overlapping sessions {over[:2]}")
    clash = [(x[2], l.course) for x in sess for l in lessons if x[0] < l.end and l.start < x[1]]
    check(not clash, f"{label}: no session on top of a lesson {clash[:2]}")
    pclash = [x[2] for x in sess for p in personal if x[0] < p[1] and p[0] < x[1]]
    check(not pclash, f"{label}: no session on top of a personal event {pclash[:2]}")
    late = [x[2] for x in sess if x[1].time() > time(23, 0) or x[1].date() != x[0].date()]
    check(not late, f"{label}: nothing ends after 23:00 {late[:2]}")
    fri = [x[2] for x in sess if x[0].weekday() == P.FRI and x[1].time() > time(16, 0)]
    check(not fri, f"{label}: Friday ends by 16:00 {fri[:2]}")
    sat = [x[2] for x in sess if x[0].weekday() == P.SAT and x[0].time() < time(12, 0)]
    check(not sat, f"{label}: nothing on Saturday morning {sat[:2]}")
    gaps = []
    for d, xs in by_day.items():
        ls = sorted([l for l in lessons if l.day == d], key=lambda l: l.start)
        if len(ls) >= 2:
            for x in xs:
                if ls[0].start < x[0] < ls[-1].end:
                    gaps.append(x[2])
    check(not gaps, f"{label}: nothing in gaps between lessons {gaps[:2]}")
    long_days = [d for d, xs in by_day.items() if d.weekday() in (P.MON, P.WED)
                 and len([x for x in xs if x[0].time() >= time(17, 0)]) > 1]
    check(not long_days, f"{label}: Mon/Wed evenings hold at most one session {long_days}")
    keys = [x[3].split("מזהה: ")[-1] for x in sess]
    check(len(keys) == len(set(keys)), f"{label}: no duplicate session keys")


def first_at(tg, prefix, d):
    return next((m for m in tg.sent if m["text"].startswith(prefix) and m["at"].date() == d), None)


def main():
    env = setup()
    clock, tg, cal, feed, store, llm = env
    uni = P.classify(P._parse_ics(feed.fetch()))

    print("\n== שבועיים רצופים (17.10–31.10), כולל מעבר לשעון חורף ב-25.10")
    # טלגרם נופל בדיוק בהודעת הבוקר של רביעי 28.10: המשימה צריכה לחזור על עצמה בלי כפילויות
    run_until(env, t("2026-10-28T07:40"))
    tg.fail_sends = 1
    run_until(env, t("2026-10-31T21:20"))

    personal = [(datetime.fromisoformat(e["start"]["dateTime"]).astimezone(TZ),
                 datetime.fromisoformat(e["end"]["dateTime"]).astimezone(TZ)) for e in cal.cals[PERS].values()]
    sess = sessions_from_calendar(cal)
    audit([x for x in sess if x[0].date() >= date(2026, 10, 18)], uni.lessons, personal, "calendar")

    print("\n== סשנים של שישי: נשאלים במוצ״ש ולא משובצים מחדש")
    wk = [m for m in tg.sent if m["text"].startswith("🗓 סמן") and m["at"].date() == date(2026, 10, 24)]
    check(len(wk) == 1 and wk[0]["at"].time() >= time(21, 0), "Saturday night asks about Friday's sessions")
    fri = [x for x in json.loads(store.blob)["sessions"] if x["start"][:10] == "2026-10-30" and x.get("course")]
    check(fri and all(x["status"] == "done" for x in fri), f"confirmed Friday sessions stay done ({[x['status'] for x in fri]})")
    sun = next(m for m in tg.sent if m["text"].startswith("☀️") and m["at"].date() == date(2026, 10, 25))
    check("לא מצאתי מקום" not in sun["text"], "no 'couldn't place' noise on Sunday about Friday")

    print("\n== שעון חורף (25.10)")
    for d in (date(2026, 10, 22), date(2026, 10, 25), date(2026, 10, 26)):
        m = first_at(tg, "☀️", d)
        check(m and time(7, 44) <= m["at"].time() <= time(7, 50), f"morning brief {d} at local 07:44 (got {m and m['at'].time()})")
        m = first_at(tg, "🌙", d) if d.weekday() != P.FRI else None
        check(m and time(21, 30) <= m["at"].time() <= time(21, 36), f"daily review {d} at local 21:30 (got {m and m['at'].time()})")
    m = first_at(tg, "🚆 ריקאפ", date(2026, 10, 26))
    check(m and time(15, 35) <= m["at"].time() <= time(15, 41), f"Monday 26.10 recap 20 min after 15:15 (got {m and m['at'].time()})")
    mon = [x for x in sess if x[0].date() == date(2026, 10, 26)]
    check(mon and all(x[0].utcoffset() == timedelta(hours=2) for x in mon), "winter-time sessions stored with +02:00")
    check(mon and all(x[0].time() >= time(20, 0) for x in mon), f"Monday 26.10 session in the evening, local time ({[x[0].time() for x in mon]})")

    print("\n== טלגרם נפל בהודעת הבוקר של 28.10")
    mornings = [m for m in tg.sent if m["text"].startswith("☀️") and m["at"].date() == date(2026, 10, 28)]
    check(len(mornings) == 1, f"morning brief retried once, not duplicated ({len(mornings)})")
    check(mornings and mornings[0]["at"] < t("2026-10-28T08:00"), "retry went out within minutes")

    print("\n== הודעות קבועות: בדיוק אחת לכל משימה ביום")
    per = defaultdict(int)
    for m in tg.sent:
        for p in ("☀️", "🌙", "📊", "🚆 ריקאפ"):
            if m["text"].startswith(p):
                per[(p, m["at"].date())] += 1
    dup = {f"{k[0]} {k[1]}": v for k, v in per.items() if v > 1}
    check(not dup, f"no duplicate scheduled messages {dup}")
    check(not any(m["text"].startswith("☀️") and m["at"].weekday() == P.SAT for m in tg.sent), "no morning brief on Saturday")
    check(not any(m["text"].startswith("⚠️") and "נכשל" in m["text"] for m in tg.sent), "no job failure alerts")

    print("\n== אבטחה: כפתור מצ׳אט זר")
    n_calls = len(cal.calls)
    mid = next(m["id"] for m in reversed(tg.sent) if m["text"].startswith("🌙"))
    tg.inbox.append({"update_id": 10 ** 6, "callback_query": {"id": "x", "data": "alldone|2026-10-29",
                                                               "message": {"message_id": mid, "chat": {"id": 999}}}})
    clock.t += timedelta(minutes=1)
    tick(env)
    check(len(cal.calls) == n_calls, "foreign callback changed nothing in the calendar")

    print("\nFAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
