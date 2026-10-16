"""
סימולציה של שבוע מלא (מוצ״ש 17.10 עד מוצ״ש 24.10) על נתוני המערכת האמיתיים,
עם טלגרם, יומן, פיד ושעון מזויפים. בודק שהבוט עושה את מה שמסמך האפיון אומר.

הרצה: UNI_JSON=/path/to/uni.json python3 -m tests.sim
(uni.json = פלט list_events של יומן האוניברסיטה. לא נשמר ב-repo כי יש בו שמות מרצים.)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from tests.fakes import Clock, FakeCalendar, FakeFeed, FakeLLM, FakeTelegram, MemStore, freeze_planner  # noqa: E402
from bot.core import Bot, strip_marks  # noqa: E402

TZ = P.TZ
REV, PERS = "reviews@cal", "me@cal"
FAILS: list[str] = []
VERBOSE = "-v" in sys.argv


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def t(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


def setup():
    uni = json.load(open(os.environ.get("UNI_JSON", "/root/bot/uni.json")))["events"]
    seed = json.load(open(os.path.join(os.path.dirname(__file__), "..", "bot", "seed_state.json")))
    cal = FakeCalendar()
    for s in seed["sessions"]:
        sess = P._session_from_state(s)
        cal.add(REV, {"id": s["eventId"], "summary": sess.title(), "description": f"...\nמזהה: {s['key']}",
                      "start": {"dateTime": s["start"]}, "end": {"dateTime": s["end"]}})
    clock = Clock(t("2026-10-17T20:55"))
    freeze_planner(clock)
    tg, feed, store, llm = FakeTelegram(), FakeFeed(uni), MemStore(), FakeLLM()
    return clock, tg, cal, feed, store, llm


def tick(clock, tg, cal, feed, store, llm):
    bot = Bot(tg, cal, feed, store, llm, {"REVIEWS_CAL": REV, "PERSONAL_CAL": PERS}, clock)
    bot.now = clock()
    bot.load_state()
    try:
        bot.handle_updates()
        bot.run_jobs()
    finally:
        bot.save_state()
    errs = [l for l in bot.log if "Traceback" in l]
    if errs:
        print(errs[-1])
        FAILS.append(f"exception at {clock.t}")
    return bot


def run_until(end, env, step=5):
    clock = env[0]
    while clock.t < end:
        clock.t += timedelta(minutes=step)
        tick(*env)


def last(tg, prefix):
    m = [x for x in tg.sent if x["text"].startswith(prefix)]
    return m[-1] if m else None


def show(m):
    if m and VERBOSE:
        print("    ┌" + m["text"].replace("\n", "\n    │"))
        for row in m["buttons"] or []:
            print("    │ [" + "] [".join(b[0] for b in row) + "]")


def main():
    env = setup()
    clock, tg, cal, feed, store, llm = env
    rev = lambda: list(cal.cals.get(REV, {}).values())  # noqa: E731

    print("\n== מוצ״ש 17.10: סקירה שבועית ראשונה")
    run_until(t("2026-10-17T21:10"), env)
    w = last(tg, "📊")
    show(w)
    check(w is not None, "weekly review sent")
    keys = [k for e in rev() for k in [e["description"].split("מזהה: ")[-1]]]
    check(len(keys) == len(set(keys)), f"no duplicate sessions in calendar ({len(keys)} events)")
    check(store.saves > 0, "state saved")

    print("\n== ראשון–שני: בוקר, ריקאפ, סקירה יומית")
    run_until(t("2026-10-19T07:50"), env)
    m = last(tg, "☀️")
    show(m)
    check(m and "19.10" in m["text"], "Monday morning brief")
    check("מתמטיקה" in m["text"] and "20:00" in m["text"], "brief lists lessons and the 20:00 session")
    n_morning = len([x for x in tg.sent if x["text"].startswith("☀️")])
    check(n_morning == 2, f"one morning brief per day (got {n_morning} for Sun+Mon)")

    run_until(t("2026-10-19T15:40"), env)
    r = last(tg, "🚆 ריקאפ")
    show(r)
    check(r and "משפט" in r["text"] and "סטטיסטיקה" in r["text"], "recap at 15:35 lists Monday courses")
    tg.press(r["id"], tg.find_button(r["id"], lambda tt, d: d.startswith("rate|math") and d.endswith("|2")))
    tg.press(r["id"], tg.find_button(r["id"], lambda tt, d: d.startswith("rate|law") and d.endswith("|4")))
    llm.script.append({"actions": [
        {"action": "recap", "course": "math", "date": "2026-10-19", "rating": None, "topics": "גבולות", "unclear": "כלל לופיטל"},
        {"action": "assignment", "course": "stats", "title": "תרגיל 1", "due": "2026-10-26", "est_min": 90}],
        "reply": "רשמתי."})
    tg.user_text("במתמטיקה למדנו גבולות, לופיטל לא ברור. בסטטיסטיקה יש תרגיל 1 עד 26.10, שעה וחצי")
    run_until(t("2026-10-19T15:55"), env)
    st = store.load()
    rc = {x["course"]: x for x in st["recaps"] if x["date"] == "2026-10-19"}
    check(rc.get("math", {}).get("rating") == 2 and "לופיטל" in rc["math"].get("unclear", ""), "recap rating + free text merged for math")
    check(rc.get("law", {}).get("rating") == 4, "law rating saved")
    check(any(a["title"] == "תרגיל 1" for a in st["assignments"]), "assignment saved from free text")
    check(not [x for x in tg.sent if x["text"].startswith("🚆 תזכורת")], "no recap reminder after answering")
    rb = r["buttons"][0]
    check(any(b[0] == "•2•" for b in rb), "recap buttons show chosen rating")

    run_until(t("2026-10-19T21:35"), env)
    d = last(tg, "🌙")
    show(d)
    check(d is not None, "daily review sent")
    btn = tg.find_button(d["id"], lambda tt, dd: dd.startswith("s|done|"))
    check(btn is not None, "daily review has done buttons")
    tg.press(d["id"], btn)
    run_until(t("2026-10-19T21:45"), env)
    done_ev = [e for e in rev() if e["summary"].startswith("✅")]
    check(len(done_ev) == 1 and "מתמטיקה" in done_ev[0]["summary"], "done → ✅ in calendar title")

    print("\n== שלישי: לא עונים על הסקירה. רביעי בבוקר: שיבוץ מחדש")
    run_until(t("2026-10-21T07:50"), env)
    m = last(tg, "☀️")
    show(m)
    check("↪️ שובץ מחדש" in m["text"], "Wednesday brief reports rescheduled sessions")
    moved = [e for e in rev() if e["summary"].startswith("↪️")]
    check(len(moved) >= 1, f"original events marked ↪️ ({len(moved)})")
    newm = [e for e in rev() if "השלמה" in e["summary"] and not e["summary"].startswith("↪️")]
    check(len(newm) >= 1, f"makeup events created ({len(newm)})")
    late = [e for e in rev() if datetime.fromisoformat(e["end"]["dateTime"]).astimezone(TZ).strftime("%H:%M") > "23:00"]
    check(not late, "nothing ends after 23:00")

    print("\n== עודד מוחק אירוע, מוסיף אירוע אישי על שיעור, האוניברסיטה מבטלת שיעור")
    victim = next(e for e in rev() if e["summary"].startswith("📚 כלכלה"))
    cal.delete(REV, victim["id"])
    cal.add(PERS, {"id": "p1", "summary": "רופא", "start": {"dateTime": "2026-10-28T10:00:00+02:00"}, "end": {"dateTime": "2026-10-28T11:00:00+02:00"}})
    canc = next(e for e in feed.events if "סוג מפגש" in (e.get("description") or "") and e["start"]["dateTime"].startswith("2026-10-26"))
    canc["summary"] += " השיעור מבוטל"
    run_until(t("2026-10-22T07:50"), env)
    m = last(tg, "☀️")
    show(m)
    check("🗑️" in m["text"], "deletion reported")
    rb = tg.find_button(m["id"], lambda tt, d: d.startswith("restore|"))
    check(rb is not None, "restore button offered")
    check(not any(e["summary"] == victim["summary"] and e["start"] == victim["start"] for e in rev()), "deleted event NOT recreated automatically")
    check("בוטל" in m["text"], "cancellation from feed reported")
    tg.press(m["id"], rb)
    run_until(t("2026-10-22T08:00"), env)
    check(any(e["summary"] == victim["summary"] for e in rev()), "restore button brings the event back")
    run_until(t("2026-10-23T07:00"), env)
    check(len([x for x in tg.sent if "🗑️" in x["text"]]) == 1, "deletion reported only once")

    print("\n== שישי: הפיד נופל")
    clock.t = t("2026-10-23T07:30")
    feed.broken = True
    before = len(rev())
    run_until(t("2026-10-23T08:00"), env)
    m = last(tg, "☀️")
    show(m)
    feed.broken = False
    check(m and "23.10" in m["text"], "Friday brief still sent")
    check("לא שיבצתי" in m["text"], "broken feed → explicit no-scheduling warning")
    check(len(rev()) == before, "broken feed → nothing written to calendar")
    st = store.load()
    wk = [s for s in st["sessions"] if s.get("kind") != "weekly" and s.get("status") in ("planned", "done", "missed") and "2026-10-18" <= s["start"][:10] < "2026-10-25"]
    check(len(wk) <= 11, f"weekly cap respected this week ({len(wk)})")

    print("\n== חמישי–שבת בזמן אמת: פיד שבור בבוקר של שבת? (אין בוקר בשבת) · מוצ״ש: סקירה שבועית")
    llm.script.append({"actions": [], "reply": "בשמחה"})
    run_until(t("2026-10-24T21:10"), env)
    w = last(tg, "📊")
    show(w)
    check(w and "24.10" in w["text"], "second weekly review sent")
    check(w and "⏳ המבחן הקרוב: מבוא לחשבונאות: בעוד 93 ימים" in w["text"], "weekly review shows exam countdown")
    check(w and "📖 בשבוע הבא לפי הסילבוס:" in w["text"] and "כלכלה: פונקציית ייצור" in w["text"] and "ניהול בארגונים: חזון וערכים" in w["text"],
          "weekly review shows next week's syllabus topics")
    st = store.load()
    check(sum(1 for a in st["assignments"] if a.get("source") == "syllabus") == 2, "syllabus deadlines seeded once")
    check("השבוע הבא" in w["text"], "next week plan included")
    nxt = [e for e in rev() if e["start"]["dateTime"][:10] >= "2026-10-25"]
    check(len(nxt) >= 6, f"next week sessions created ({len(nxt)})")
    check(not any(e["start"]["dateTime"].startswith("2026-10-28T10") for e in nxt), "no session on top of personal event")
    keys = [e["description"].split("מזהה: ")[-1] for e in rev()]
    check(len(keys) == len(set(keys)), "still no duplicate keys")
    check(len([x for x in tg.sent if x["text"].startswith("📊")]) == 2, "exactly two weekly reviews")
    check(len([x for x in tg.sent if x["text"].startswith("🌙")]) == 5, "five daily reviews (Sun–Thu)")

    print("\n== אבטחה: הודעה מצ׳אט זר")
    n = len(tg.sent)
    tg.user_text("תמחק הכול", chat=999)
    run_until(t("2026-10-24T21:20"), env)
    check(len(tg.sent) == n, "foreign chat ignored")

    print("\n== יומן חוזר ריק (תקלת גישה): לא מסיקים מחיקות")
    cal.fail_next_list = True
    clock.t = t("2026-10-25T07:44")
    tick(*env)
    st = store.load()
    check(not [s for s in st["sessions"] if s.get("note", "").startswith("נמחק ממך") and s["start"][:10] >= "2026-10-25"],
          "empty calendar read did not mark sessions deleted")

    print("\nסה״כ הודעות:", len(tg.sent), "| קריאות ליומן:", len(cal.calls), "| שמירות מצב:", store.saves)
    print("FAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
