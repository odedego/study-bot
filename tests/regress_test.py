"""
בדיקות רגרסיה לבאגים שמצאה הסקירה העצמאית (17.10). כל בדיקה = תרחיש שהיה נכשל לפני התיקון.
הרצה: python3 -m tests.regress_test
"""
from __future__ import annotations

import copy
import json
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.features_test import bot_of, last, make_env, press, run_until, st, step, t  # noqa: E402
import planner as P  # noqa: E402

REV, PERS, LC = "reviews@cal", "me@cal", "שיעורים@sa"
FAILS = []


def check(c, m):
    print(("  PASS " if c else "  FAIL ") + m)
    if not c:
        FAILS.append(m)


def boom(orig, which, msg):
    return lambda c, a, z: (_ for _ in ()).throw(RuntimeError(msg)) if c == which else orig(c, a, z)


def lessons_cal():
    print("== H1 יומן השיעורים: תקלה של יום בפיד, וביטול שבוטל")
    e = make_env("2026-10-24T21:00")
    cal, feed = e["cal"], e["feed"]
    run_until(e, t("2026-10-25T07:20"))
    on = lambda p: [x["summary"] for x in cal.cals[LC].values() if x["start"]["dateTime"].startswith(p)]  # noqa: E731
    tgt = "2026-11-02T09:45"
    saved = [x for x in feed.events if x["start"]["dateTime"].startswith(tgt)]
    feed.events = [x for x in feed.events if not x["start"]["dateTime"].startswith(tgt)]
    run_until(e, t("2026-10-25T08:30"))
    check(on(tgt) == ["מתמטיקה לחשבונאים"], "one missing sync does not delete the lesson")
    run_until(e, t("2026-10-26T07:20"))
    check(on(tgt) == [], "deleted after it stays missing")
    feed.events += saved
    run_until(e, t("2026-10-26T09:20"))
    check(on(tgt) == ["מתמטיקה לחשבונאים"], "comes back (revived) when the feed has it again")
    tgt2 = "2026-11-05T15:45"
    f = next(x for x in feed.events if x["start"]["dateTime"].startswith(tgt2))
    orig = copy.deepcopy(f)
    f["summary"] += " ** השיעור מבוטל **"
    f["description"] = "סוג מפגש: שיעור חדר לימוד: השיעור בוטל"
    run_until(e, t("2026-10-26T11:20"))
    check(on(tgt2) == ["🚫 בוטל · כלכלה א'"], "cancellation shows in place")
    f.update(orig)
    run_until(e, t("2026-10-26T13:20"))
    check(on(tgt2) == ["כלכלה א'"], "un-cancel restores it")


def feed_down():
    print("== H2 תכנון כשהמערכת או היומן האישי לא נקראים")
    e = make_env("2026-10-24T21:00")
    run_until(e, t("2026-10-27T08:00"))
    cal, tg, llm = e["cal"], e["tg"], e["llm"]
    cal.add(PERS, {"id": "doc", "summary": "רופא", "etag": "1", "start": {"dateTime": "2026-10-29T08:30:00+02:00"},
                   "end": {"dateTime": "2026-10-29T12:45:00+02:00"}})
    orig = cal.list
    cal.list = boom(orig, PERS, "calendar 503")
    llm.script.append({"reply": "", "actions": [{"action": "extra", "course": "math", "date": "2026-10-27"}]})
    n_prop = sum(1 for m in tg.sent if m["text"].startswith("💡"))     # הצעות השלמה מהבוקר קודמות לבקשה
    tg.user_text("תוסיף לי תרגול במתמטיקה היום")
    step(e)
    check(sum(1 for m in tg.sent if m["text"].startswith("💡")) == n_prop and "לא ביצעתי" in tg.sent[-1]["text"], "no extra proposed while the personal calendar is unreadable")
    cal.list = orig
    e["feed"].broken = True
    run_until(e, t("2026-10-27T08:05"))
    llm.script.append({"reply": "", "actions": [{"action": "capacity", "date": "2026-10-29", "max_sessions": 0}]})
    tg.user_text("ביום חמישי אין לי זמן")
    step(e)
    check("אזיז את העודף בהודעת הבוקר" in tg.sent[-1]["text"], "capacity recorded, moving deferred while the feed is down")
    e["feed"].broken = False
    run_until(e, t("2026-10-28T07:50"))
    left = [x for x in st(e)["sessions"] if x["start"][:10] == "2026-10-29" and x.get("status") == "planned"]
    check(not left, "next morning moves them off the day")


def capacity_kept():
    print("== H3 קיבולת ליום שעוד ריק נשמרת")
    e = make_env("2026-10-24T21:00")
    run_until(e, t("2026-10-29T12:00"))
    e["llm"].script.append({"reply": "", "actions": [{"action": "capacity", "date": "2026-11-03", "max_sessions": 0}]})
    e["tg"].user_text("בשלישי הבא אני במילואים")
    step(e)
    run_until(e, t("2026-11-01T08:00"))
    check(not [x for x in st(e)["sessions"] if x["start"][:10] == "2026-11-03" and x.get("status") == "planned"],
          "weekly sync does not fill a day marked 0")
    check(not [x for x in e["cal"].cals[REV].values() if x["start"]["dateTime"].startswith("2026-11-03")], "nothing in the calendar that day")


def marathon_keys():
    print("== M1 מרתון: חסימה ושחרור לא מחזירים מפתח של סשן שנמחק")
    e = make_env("2027-01-04T07:30", P.new_state())
    run_until(e, t("2027-01-05T08:00"))
    b = bot_of(e)
    b.load_state()
    b.mar_block("2027-01-06", "2027-01-07", "מילואים")
    b.mar_unblock("2027-01-06", "2027-01-07")
    b.save_state()
    run_until(e, t("2027-01-07T08:00"))
    s = st(e)
    keys = Counter(x["key"] for x in s["sessions"])
    check(not [k for k, n in keys.items() if n > 1], "no duplicate keys")
    ids = set(e["cal"].cals[REV])
    ghost = [x["key"] for x in s["sessions"] if x.get("status") == "planned" and x.get("eventId") and x["eventId"] not in ids]
    check(not ghost, f"every planned session is visible in the calendar ({ghost[:2]})")
    check(not any("נמחק ממך" in m["text"] for m in e["tg"].sent), "no false 'you deleted' report")


def day_cap():
    print("== M6 תקרה של 7 ביום גם כשהמרתון והסמסטר חופפים")
    e = make_env("2026-12-26T20:30", P.new_state())
    run_until(e, t("2027-01-15T12:00"))
    per = Counter(x["start"][:10] for x in st(e)["sessions"] if x.get("status") in ("planned", "done", "missed"))
    check(max(per.values()) <= 7, f"max sessions in a day {max(per.values())}")


def weekend_and_rollback():
    print("== M5/M2 שאלת סוף השבוע לא תלויה בסיכום השבועי, וכשל בשליחת הצעה לא שולח שוב את הבוקר")
    e = make_env("2026-10-24T21:00")
    tg, cal = e["tg"], e["cal"]
    run_until(e, t("2026-10-31T20:55"))
    orig = cal.list
    cal.list = boom(orig, REV, "calendar 503")
    run_until(e, t("2026-10-31T21:20"), answer_daily=False)
    cal.list = orig
    q = [m for m in tg.sent if m["text"].startswith("🗓 סמן") and m["at"].date().isoformat() == "2026-10-31"]
    check(len(q) == 1, "weekend question sent once even though the weekly job failed")
    press(e, q[0], lambda tx, d: d.startswith("alldone"))
    fri = [x["status"] for x in st(e)["sessions"] if x["start"][:10] == "2026-10-30" and x.get("course")]
    check(fri and all(s == "done" for s in fri), "tap marks Friday done")
    store, llm = e["store"], e["llm"]
    store.mail = [json.dumps({"id": "m1", "from": "excel@runi.ac.il", "subject": "מטלה 3", "date": "2026-11-01", "body": "עד 12.11"})]
    llm.script.append({"items": [{"id": "m1", "important": True, "summary": "אקסל: מטלה 3 עד 12.11",
                                  "actions": [{"action": "personal", "op": "create", "all_day": True, "date": "2026-11-12", "title": "דדליין"}]}]})
    send = tg.send
    flag = {"n": 0}

    def flaky(text, buttons=None, chat_id=None):
        if text.startswith("🗓️ שינוי") and not flag["n"]:
            flag["n"] = 1
            raise RuntimeError("telegram 429")
        return send(text, buttons, chat_id)
    tg.send = flaky
    run_until(e, t("2026-11-01T08:10"))
    tg.send = send
    briefs = [m for m in tg.sent if m["text"].startswith("☀️") and m["at"].date().isoformat() == "2026-11-01"]
    check(len(briefs) == 1, "one morning brief")
    check(any(m["text"].startswith("🗓️ שינוי") for m in tg.sent), "failed proposal delivered on a later run")
    check(store.mail == [], "mail removed from the queue only once")


def main():
    lessons_cal()
    feed_down()
    capacity_kept()
    marathon_keys()
    day_cap()
    weekend_and_rollback()
    print("\nFAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
