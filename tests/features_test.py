"""
התוספות של 16.10 בערב, על שבועיים-שלושה מדומים עם המערכת הסינתטית:
  חיזוק אחרי ריקאפ חלש, זמן עבודה למטלה, קיבולת יומית, כיול אחרי 3 שבועות,
  יומן "שיעורים" נקי, סריקת מייל, ומרתון מבחנים (28.12 ואילך).
הרצה: python3 -m tests.features_test
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot import semester as SEM  # noqa: E402
from bot.core import Bot  # noqa: E402
from tests.edge_test import TimedTelegram, audit, sessions_from_calendar  # noqa: E402
from tests.fakes import Clock, FakeCalendar, FakeFeed, FakeLLM, MemStore, freeze_planner  # noqa: E402
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


class FlakyLLM(FakeLLM):
    down = False

    def complete(self, system, user, max_tokens=800, media=None, tool=None):
        if self.down:
            raise RuntimeError("claude: 529")
        return super().complete(system, user, max_tokens, media, tool)


def make_env(start, state=None):
    clock = Clock(t(start))
    freeze_planner(clock)
    tg = TimedTelegram(clock)
    store = MemStore()
    if state is not None:
        store.blob = json.dumps(state, ensure_ascii=False)
    cal = FakeCalendar()
    if state is None:
        seed = json.load(open(os.path.join(os.path.dirname(__file__), "..", "bot", "seed_state.json"), encoding="utf-8"))
        for s in seed["sessions"]:
            cal.add(REV, {"id": s["eventId"], "summary": P._session_from_state(s).title(), "description": f"...\nמזהה: {s['key']}",
                          "start": {"dateTime": s["start"]}, "end": {"dateTime": s["end"]}})
    return {"clock": clock, "tg": tg, "cal": cal, "feed": FakeFeed(semester()), "store": store, "llm": FlakyLLM()}


def bot_of(e):
    b = Bot(e["tg"], e["cal"], e["feed"], e["store"], e["llm"], {"REVIEWS_CAL": REV, "PERSONAL_CAL": PERS}, e["clock"])
    b.now = e["clock"]()
    return b


def tick(e):
    b = bot_of(e)
    b.load_state()
    try:
        b.handle_updates()
        b.run_jobs()
    finally:
        b.save_state()
    return b


def run_until(e, end, step=5, answer_daily=True):
    tg = e["tg"]
    while e["clock"].t < end:
        e["clock"].t += timedelta(minutes=step)
        n = len(tg.sent)
        tick(e)
        if answer_daily:
            for m in tg.sent[n:]:
                if m["text"].startswith(("🌙", "🗓 סמן")) and m["buttons"]:
                    d = tg.find_button(m["id"], lambda tx, dt: dt.startswith("alldone"))
                    if d:
                        tg.press(m["id"], d)


def step(e, minutes=1):
    e["clock"].t += timedelta(minutes=minutes)
    return tick(e)


def last(tg, prefix):
    m = [x for x in tg.sent if x["text"].startswith(prefix)]
    return m[-1] if m else None


def press(e, m, pred):
    d = e["tg"].find_button(m["id"], pred)
    e["tg"].press(m["id"], d)
    return step(e)


def st(e):
    return json.loads(e["store"].blob)


def semester_part():
    e = make_env("2026-10-24T21:00")
    tg, cal, llm, store = e["tg"], e["cal"], e["llm"], e["store"]

    print("\n== יומן \"שיעורים\" נקי (מ-25.10)")
    run_until(e, t("2026-10-25T07:20"))
    run_until(e, t("2026-10-25T07:50"))
    mb = last(tg, "☀️")
    body = mb["text"] if mb else ""
    check("❓ צריך החלטה:" in body and body.index("❓ צריך החלטה:") < body.index("📅 היום:") < body.index("🗓️ חזרה שבועית"),
          "morning: decisions first, then today's schedule")
    check("חזרה על 19.10 (1)" in body and "חזרה על 19.10 (2)" in body, "duplicate unplaced titles numbered")
    lc = "שיעורים@sa"
    evs = cal.cals.get(lc, {})
    check(len(evs) > 60 and cal.shares == [(lc, PERS, "reader")], f"calendar created, shared read-only ({len(evs)} lessons)")
    check(all(e2["summary"] in SEM.FULL_NAMES.values() or e2["summary"].endswith("· תרגול") or e2["summary"].startswith("🚫") for e2 in evs.values()),
          "titles are course names only")
    check(any(m["text"].startswith("📅 יש לך עכשיו יומן") for m in tg.sent), "Oded told how to add it and hide the old one")
    f = next(x for x in e["feed"].events if x["start"]["dateTime"].startswith("2026-10-29T15:45"))
    f["summary"] += " ** השיעור מבוטל **"
    f["description"] = "סוג מפגש: שיעור חדר לימוד: השיעור בוטל"
    run_until(e, t("2026-10-26T07:50"))
    day = [x["summary"] for x in cal.cals[lc].values() if x["start"]["dateTime"].startswith("2026-10-29T15:45")]
    check(day == ["🚫 בוטל · כלכלה א'"], f"cancellation mirrored next day ({day})")

    print("\n== חיזוק אחרי ריקאפ בציון 2 (שני 26.10)")
    run_until(e, t("2026-10-26T15:40"))
    rc = last(tg, "🚆 ריקאפ")
    check(rc and not any(d.startswith("rate|") for row in rc["buttons"] for _, d in row), "recap starts with attendance only")
    press(e, rc, lambda tx, d: d == "att|math|2026-10-26|present")
    check(any(d == "rate|math|2026-10-26|2" for row in tg.msg(rc["id"])["buttons"] for _, d in row), "after 'present' → rating row")
    press(e, rc, lambda tx, d: d == "rate|math|2026-10-26|2")
    xp = last(tg, "💡")
    check(xp and "מתמטיקה: הבנה 2/5" in xp["text"], "reinforcement proposed for math")
    slot_day = None
    if xp:
        b = bot_of(e)
        b.load_state()
        p = next(x for x in b.st["bot"]["xp"].values() if x["kind"] == "extra")
        slot_day = date.fromisoformat(p["slots"][0][0][:10])
        check(slot_day.weekday() not in (P.MON, P.WED, P.SAT) and date(2026, 10, 27) <= slot_day <= date(2026, 10, 30),
              f"slot within 4 days, not Mon/Wed/Sat ({slot_day})")
        press(e, xp, lambda tx, d: d.startswith("xp|ok"))
    ev = [x for x in cal.cals[REV].values() if x["summary"].startswith("💪 חיזוק · מתמטיקה")]
    check(len(ev) == 1, "approved → event in calendar")
    check(any(s.get("kind") == "extra" for s in st(e)["sessions"]), "stored as extra (outside the weekly cap)")
    n = len([m for m in tg.sent if m["text"].startswith("💡")])
    press(e, rc, lambda tx, d: d == "rate|math|2026-10-26|2")
    check(len([m for m in tg.sent if m["text"].startswith("💡")]) == n, "same lesson rated again → no second proposal")
    press(e, rc, lambda tx, d: d == "att|law|2026-10-26|present")
    press(e, rc, lambda tx, d: d == "rate|law|2026-10-26|1")
    xp2 = last(tg, "💡")
    press(e, xp2, lambda tx, d: d.startswith("xp|ok"))
    press(e, rc, lambda tx, d: d == "att|stats|2026-10-26|present")
    press(e, rc, lambda tx, d: d == "rate|stats|2026-10-26|2")
    check("2 תוספות" in tg.sent[-1]["text"] or "לא מצאתי מקום" in tg.sent[-1]["text"], "third extra this week → not proposed, told why")

    print("\n== מטלה של שעתיים: זמן עבודה לפני ההגשה")
    llm.script.append({"reply": "רשמתי.", "actions": [{"action": "assignment", "course": "excel", "title": "תרגיל 2", "due": "2026-11-05", "est_min": 120}]})
    tg.user_text("מטלה באקסל עד 5.11, שעתיים")
    step(e)
    xp3 = last(tg, "💡")
    check(xp3 and "תרגיל 2" in xp3["text"] and xp3["text"].count("• ") == 2, "two work sessions proposed")
    b = bot_of(e)
    b.load_state()
    p = next(v for v in b.st["bot"]["xp"].values() if v["kind"] == "assign")
    days = [date.fromisoformat(a[:10]) for a, _ in p["slots"]]
    check(all(P.week_start(d) == date(2026, 11, 1) and d < date(2026, 11, 5) for d in days),
          f"this week already has 2 extras → slots next week, before the due date ({days})")
    a0 = datetime.fromisoformat(p["slots"][0][0])
    cal.add(PERS, {"id": "clash", "summary": "פגישה", "etag": "1", "start": {"dateTime": a0.isoformat()},
                   "end": {"dateTime": (a0 + timedelta(hours=1)).isoformat()}})
    press(e, xp3, lambda tx, d: d.startswith("xp|ok"))
    ws = [x for x in st(e)["sessions"] if x.get("kind") == "assign"]
    check(len(ws) == 2 and all(datetime.fromisoformat(x["start"]) != a0 for x in ws), "a slot that got taken meanwhile was moved, still 2 sessions")

    print("\n== קיבולת: \"רק סשן אחד\" ביום עמוס")
    run_until(e, t("2026-10-27T12:00"))
    wk = [m for m in tg.sent if m["text"].startswith("💪 מה צריך חיזוק")]
    check(len(wk) == 1 and wk[0]["at"].strftime("%Y-%m-%d %H:%M") >= "2026-10-26 21:45", "reinforcement report Monday evening")
    if wk:
        lines = [x for x in wk[0]["text"].split("\n") if x.startswith("• ")]
        # מתמטיקה: הבנה 2/5 וגם סשנים מהשבוע הקודם שלא בוצעו → הכי חלש. אחריה משפט (1/5) ואז סטטיסטיקה (2/5)
        check([x.split(":")[0] for x in lines[:3]] == ["• מתמטיקה", "• משפט", "• סטטיסטיקה"] and "לא בוצעו" in lines[0],
              f"worst first: {lines[:3]}")
        check(len(wk[0]["text"]) < 500, "report is short")
    s0 = st(e)
    by = Counter(x["start"][:10] for x in s0["sessions"] if x.get("status") == "planned" and x["start"][:10] > "2026-10-27")
    busy = max((d for d in by if by[d] >= 2), key=lambda d: by[d], default=None)
    check(busy is not None, f"found a day with 2+ sessions ({busy}: {by.get(busy)})")
    if busy:
        llm.script.append({"reply": "", "actions": [{"action": "capacity", "date": busy, "max_sessions": 1, "text": "רק סשן אחד"}]})
        tg.user_text("ביום הזה רק סשן אחד")
        step(e)
        s1 = st(e)
        left = [x for x in s1["sessions"] if x.get("status") == "planned" and x["start"][:10] == busy]
        check(len(left) == 1, f"one session left on {busy}")
        check("הזזתי" in tg.sent[-1]["text"], "told what moved")
        txt = tg.sent[-1]["text"]
        check(txt.count("• ") == by[busy] - 1, f"each removed session accounted for ({txt.count('• ')})")
        moved_ex = [x for x in s1["sessions"] if x.get("kind") in ("extra", "assign") and x["start"][:10] != busy
                    and x.get("status") == "planned" and x.get("key", "").endswith(busy)]
        check(not [x for x in s1["sessions"] if x.get("kind") in ("extra", "assign") and x.get("status") == "dropped"] or "הורד" in txt,
              "extras moved to another day, not silently dropped")
    llm.script.append({"reply": "", "actions": [{"action": "capacity", "date": "2026-10-30", "free_day": True, "text": "פנוי"}]})
    tg.user_text("שישי פנוי כל היום")
    step(e)
    check("2 תוספות" in tg.sent[-1]["text"], "free day but extras budget used → told, nothing added")

    print("\n== סריקת מייל (חמישי 29.10)")
    run_until(e, t("2026-10-29T07:30"))
    store.mail = [json.dumps({"id": "m1", "from": "excel@runi.ac.il", "subject": "מטלה 3", "date": "2026-10-28", "body": "הגשה עד 12.11"}),
                  json.dumps({"id": "m2", "from": "news@runi.ac.il", "subject": "ניוזלטר", "date": "2026-10-28", "body": "אירועים"}),
                  json.dumps({"id": "m3", "from": "office@runi.ac.il", "subject": "שינוי חדר", "date": "2026-10-28", "body": "סטטיסטיקה עוברת לחדר A1"})]
    llm.script.append({"items": [
        {"id": "m1", "important": True, "summary": "אקסל: מטלה 3, הגשה עד 12.11",
         "actions": [{"action": "assignment", "course": "excel", "title": "מטלה 3", "due": "2026-11-12"},
                     {"action": "personal", "op": "create", "all_day": True, "date": "2026-11-12", "title": "⏰ דדליין: אקסל מטלה 3"},
                     {"action": "personal", "op": "delete", "event": "pxxx"}]},
        {"id": "m2", "important": False, "summary": "ניוזלטר"},
        {"id": "m3", "important": True, "summary": "סטטיסטיקה עוברת לחדר A1", "actions": []}]})
    n0 = len(tg.sent)
    run_until(e, t("2026-10-29T07:50"))
    out = tg.sent[n0:]
    mb = next((m for m in out if m["text"].startswith("☀️")), None)
    check(mb and "📧 מהמייל:" in mb["text"] and "מטלה 3" in mb["text"] and "חדר A1" in mb["text"] and "ועוד 1" in mb["text"],
          "morning brief has the important mails and counts the rest")
    check(any(a["title"] == "מטלה 3" for a in st(e)["assignments"]), "assignment from mail recorded")
    pp_i = next((i for i, m in enumerate(out) if m["text"].startswith("🗓️")), None)
    check(pp_i is not None and mb and out.index(mb) < pp_i, "calendar proposal sent after the brief")
    check(not [m for m in out if "למחוק" in m["text"]], "delete request inside a mail ignored")
    check(store.mail == [], "processed mails removed from the queue")
    store.mail = [json.dumps({"id": "m4", "from": "x@runi.ac.il", "subject": "בוחן", "date": "2026-10-29", "body": "בוחן ביום ראשון"})]
    llm.down = True
    run_until(e, t("2026-10-30T07:50"))
    llm.down = False
    mb = last(tg, "☀️")
    check("לא הצלחתי לסכם" in mb["text"] and len(store.mail) == 1, "Claude down → mail kept for tomorrow")

    print("\n== כיול אחרי 3 שבועות (מוצ״ש 7.11)")
    run_until(e, t("2026-11-07T21:10"))
    w = last(tg, "📊")
    check(w and "🎛️ כיול" in w["text"] and any(d.startswith("tgt|math") for row in w["buttons"] for _, d in row), "calibration with buttons")
    press(e, w, lambda tx, d: d == "tgt|math|1")
    s2 = st(e)
    check(s2.get("weekly_targets", {}).get("math") == 4, "math target raised to 4")
    check(any(tx == "יעד 4" for row in tg.msg(w["id"])["buttons"] for tx, _ in row), "button row shows the new target")
    b = bot_of(e)
    b.load_state()
    uni, _ = b.load_uni()
    p = P.plan_week(date(2026, 11, 8), uni, [], b.st)
    check(p["targets"]["math"]["target"] == 4, "next week's plan uses the new target")
    audit([x for x in sessions_from_calendar(cal) if x[0].date() >= date(2026, 10, 25)], uni.lessons,
          [(datetime.fromisoformat(x["start"]["dateTime"]), datetime.fromisoformat(x["end"]["dateTime"]))
           for x in cal.cals[PERS].values() if "dateTime" in x["start"]], "calendar with extras")


def absence_part():
    print("\n== ריקאפ בשני שלבים: היעדרות → הצעת השלמה (שני 26.10)")
    e = make_env("2026-10-24T21:00")
    tg = e["tg"]
    run_until(e, t("2026-10-26T15:40"))
    rc = last(tg, "🚆 ריקאפ")
    check(rc and "היית בשיעור" in rc["text"], "recap asks attendance first")
    press(e, rc, lambda tx, d: d == "att|math|2026-10-26|absent")
    s0 = st(e)
    check(s0["bot"]["attend"].get("math|2026-10-26") == "absent", "absence recorded")
    row = next((r for r in tg.msg(rc["id"])["buttons"] if r and r[0][0].startswith("מתמטיקה")), [])
    check(not any(d.startswith("rate|") for _, d in row), "absent → no rating row for that course")
    xp = last(tg, "💡")
    check(xp and "השלמה" in xp["text"] and "מעבר ל-11" not in xp["text"], f"make-up proposed, not as an extra ({xp and xp['text'][:80]})")
    p = next((v for v in s0["bot"].get("xp", {}).values() if v.get("ref") == "abs:2026-10-26"), None)
    check(p and p["kind"] == "rescheduled", "proposal kind is rescheduled")
    n = len([m for m in tg.sent if m["text"].startswith("💡")])
    press(e, rc, lambda tx, d: d == "att|math|2026-10-26|present")
    press(e, rc, lambda tx, d: d == "att|math|2026-10-26|absent")
    check(len([m for m in tg.sent if m["text"].startswith("💡")]) == n, "same absence again → no second proposal")
    if xp:
        press(e, xp, lambda tx, d: d.startswith("xp|ok"))
    mk = [x for x in st(e)["sessions"] if x.get("kind") == "rescheduled" and x["key"].startswith("rescheduled:math:abs:")]
    check(len(mk) == 1 and mk[0].get("lesson_day") == "2026-10-26", "approved → make-up session (kind rescheduled)")
    b = bot_of(e)
    b.load_state()
    check(b.extras_in_week(P.week_start(date(2026, 10, 26))) == 0, "weekly extras budget untouched")
    run_until(e, t("2026-10-26T16:30"), answer_daily=False)
    check(not [m for m in tg.sent if m["text"].startswith("🚆 תזכורת")], "attendance answered → no recap reminder")


def weekly_part():
    print("\n== סיכום שבועי בבלוקים, ומספור סשנים כפולים")
    e = make_env("2026-10-24T21:00")
    tg = e["tg"]
    run_until(e, t("2026-10-26T15:40"))
    rc = last(tg, "🚆 ריקאפ")
    press(e, rc, lambda tx, d: d.startswith("attall"))
    check(all(d.startswith(("rate|", "noop", "rateall")) for row in tg.msg(rc["id"])["buttons"] for _, d in row),
          "'present for all' → only rating buttons left")
    press(e, rc, lambda tx, d: d == "rate|math|2026-10-26|2")
    e["llm"].script.append({"reply": "רשמתי", "actions": [{"action": "recap", "course": "law", "date": "2026-10-26", "rating": 3,
                                                          "topics": "חוזים: הצעה וקיבול", "unclear": ""}]})
    tg.user_text("במשפט למדנו הצעה וקיבול")
    step(e)
    run_until(e, t("2026-10-31T21:10"))
    w = last(tg, "📊")
    first = w["text"].split("\n")[0] if w else ""
    check(first.startswith("📊 סיכום שבוע 25.10 עד 31.10: שבוע") and first.endswith(".") and "מתוך" in first, f"first line is a verdict ({first})")
    txt = w["text"] if w else ""
    pos = [txt.find(x) for x in ("📈 בוצעו", "📚 מה נלמד:", "📝 ריקאפים:", "🔍 צריך חיזוק:", "🗓 השבוע הבא:")]
    check(all(p > 0 for p in pos) and pos == sorted(pos), f"blocks in order 📈 📚 📝 🔍 🗓 ({pos})")
    check("• משפט: חוזים: הצעה וקיבול" in txt, "topics studied, by course")
    check("חסר ריקאפ: סטטיסטיקה 26.10" in txt, "lessons without a recap listed")
    check("• מתמטיקה: הבנה 2/5" in txt.split("🔍")[1], "reinforcement block")
    mb = [m for m in tg.sent if m["text"].startswith("☀️") and "30.10" in m["text"]]
    body = mb[0]["text"] if mb else ""
    check("כלכלה · חזרה על 29.10 (1)" in body and "כלכלה · חזרה על 29.10 (2)" in body, "same-title sessions on one day numbered in the morning list")
    nxt = txt.split("🗓 השבוע הבא:")[1] if "🗓 השבוע הבא:" in txt else ""
    check("(1)" in nxt and "(2)" in nxt, "and in the next-week plan")
    b = bot_of(e)
    b.load_state()
    check(b.number_dups(["שלישי 3.11: 09:00 📚 א | 10:05 📚 ב | 11:10 📚 א"]) == ["שלישי 3.11: 09:00 📚 א (1) | 10:05 📚 ב | 11:10 📚 א (2)"],
          "number_dups unit")
    keys = [x["key"] for x in st(e)["sessions"]]
    check(not [k for k in keys if k.endswith(("(1)", "(2)"))], "keys unchanged")

    print("\n== \"סיכום\" / \"תכנון\" בלי Claude")
    llm = e["llm"]
    for text, prefix in (("סיכום", "🌙 סיכום יום"), ("סכם לי את היום", "🌙 סיכום יום"), ("סיכום שבוע", "📊 סיכום שבוע"),
                         ("תכנון", "📅 "), ("מה התכנון להיום?", "📅 "), ("מה התכנון לשבוע", "שבוע "), ("מה התוכנית למחר", "📅 ")):
        n = len(llm.calls)
        tg.user_text(text)
        step(e)
        check(tg.sent[-1]["text"].startswith(prefix) and len(llm.calls) == n, f"\"{text}\" → {prefix.strip()} without the LLM")
    n = len(llm.calls)
    tg.user_text("סיכום של מה שלמדנו במתמטיקה: גבולות")
    step(e)
    check(len(llm.calls) == n + 1, "a longer message with content still goes to Claude")
    check("\"סיכום\"" in __import__("bot.core", fromlist=["HELP"]).HELP, "help mentions it")


def marathon_part():
    print("\n== מרתון מבחנים")
    e = make_env("2026-12-26T20:30", P.new_state())
    tg, cal, llm = e["tg"], e["cal"], e["llm"]
    run_until(e, t("2026-12-26T21:10"))
    w = last(tg, "📊")
    check(w and "🏃 מרתון מבחנים · מתחיל ב-שני 28.12" in w["text"], "weekly review two days before announces the marathon")
    check(w and any(d.startswith("mar|shift") for row in w["buttons"] for _, d in row), "buttons to move the start")
    run_until(e, t("2026-12-28T07:50"))
    mb = last(tg, "☀️")
    check(mb and "🏃 מרתון ·" in mb["text"] and "➕ מרתון" in mb["text"], "first marathon morning")
    ms = [x for x in st(e)["sessions"] if x.get("kind") == "marathon"]
    check(ms and {x["start"][:10] for x in ms} <= {"2026-12-28", "2026-12-29", "2026-12-30"}, f"3 days committed ({len(ms)} sessions)")
    first_intro = min((x for x in ms if x["course"] == "intro"), key=lambda x: x["start"], default=None)
    check(first_intro and "סיכום: תיאוריה חשבונאית" in first_intro.get("label", ""), "first intro session starts the syllabus topics")
    check(not [x for x in st(e)["sessions"] if x.get("kind") == "weekly" and x["start"][:10] >= "2026-12-28"], "no Sunday weekly review during the marathon")

    run_until(e, t("2027-01-02T12:00"))
    llm.script.append({"reply": "", "actions": [{"action": "marathon_block", "from": "2027-01-03", "to": "2027-01-06", "reason": "מילואים"}]})
    tg.user_text("מילואים 3.1 עד 6.1")
    step(e)
    check("🎖️ רשמתי" in tg.sent[-1]["text"], "reserve days recorded")
    run_until(e, t("2027-01-05T12:00"))
    llm.script.append({"reply": "", "actions": [{"action": "practice_exam", "course": "intro", "score": 92}]})
    tg.user_text("עשיתי מבחן תרגול במבוא, 92")
    step(e)
    check(any("שליטה" in m["text"] for m in tg.sent[-2:]), "92 → mastery message")
    fr = last(tg, "ביומן כבר יש")
    check(fr is not None, "offers to free the intro sessions already in the calendar")
    if fr:
        press(e, fr, lambda tx, d: d == "mar|free|intro")
        check(tg.sent[-1]["text"].startswith("🔄 פיניתי"), "freed and re-planned")
    run_until(e, t("2027-01-12T21:40"))
    s = st(e)
    mar = [x for x in s["sessions"] if x.get("kind") == "marathon"]
    blocked = [x for x in mar if "2027-01-03" <= x["start"][:10] <= "2027-01-06" and x.get("status") != "dropped"]
    check(not blocked, f"nothing left on reserve days ({len(blocked)})")
    check(not [k for k in cal.cals[REV].values() if "🏃" in k["summary"] and "2027-01-03" <= k["start"]["dateTime"][:10] <= "2027-01-06"],
          "their calendar events removed")
    late_intro = [x for x in mar if x["course"] == "intro" and x["start"][:10] > "2027-01-05" and x.get("status") != "dropped"]
    check(len(late_intro) <= 1 and all("חזרה אחרונה" in x.get("label", "") for x in late_intro),
          f"after mastery intro gets only the final review ({len(late_intro)})")
    done = Counter(x["course"] for x in mar if x.get("status") == "done")
    check(sum(done.values()) >= 10 and len(done) >= 2, f"daily ✅ counts toward progress, several courses ({dict(done)})")
    b = bot_of(e)
    b.load_state()
    uni, _ = b.load_uni()
    sess = [(datetime.fromisoformat(x["start"]), datetime.fromisoformat(x["end"]), x.get("label", ""), "מזהה: " + x["key"])
            for x in s["sessions"] if x.get("status") in ("planned", "done", "missed") and x["start"][:10] >= "2026-12-28"]
    over_lesson = [x[2] for x in sess for l in uni.lessons if x[0] < l.end and l.start < x[1]]
    check(not over_lesson, "no marathon session on top of a lesson")
    check(not [x for x in sess if x[1].time() > time(21, 30)], "nothing ends after 21:30")
    check(not [x for x in sess if x[0].weekday() == P.FRI and x[1].time() > time(16, 0)], "Friday ends by 16:00")
    check(not [x for x in sess if x[0].weekday() == P.SAT], "nothing on Saturday")
    ov = [(a[2], c[2]) for i, a in enumerate(sorted(sess)) for c in sorted(sess)[i + 1:] if c[0] < a[1] and a[0] < c[1]]
    check(not ov, f"no overlaps {ov[:2]}")
    mt = b.marathon_text()
    check("✅" in mt and "התוכנית הקרובה" in mt and "🎖️ ימים חסומים" not in mt, "marathon text: mastery mark, upcoming plan, past blocks hidden")
    tg.user_text("מרתון")
    step(e)
    check(tg.sent[-1]["text"].startswith("🏃 מרתון מבחנים"), "\"מרתון\" command")
    plan, short = b.mar_allocate()
    days = sorted(plan)
    check(days and days[-1] < date(2027, 2, 19), "allocation ends before the last exam")
    exam_days = {SEM.exam_dt(c).date() for c in SEM.EXAMS}
    check(not [d for d in plan if d in exam_days], "no marathon sessions on exam days")
    per = Counter(c for v in plan.values() for c in v)
    print("    allocation:", dict(per), "short:", short)
    math_window = sum(1 for d, v in plan.items() for c in v if c == "math" and date(2027, 1, 26) <= d <= date(2027, 1, 30))
    check(math_window >= 10, f"math practice sits between the intro and math exams ({math_window})")
    check(max(Counter(v).most_common(1)[0][1] for d, v in plan.items() if d >= date(2027, 1, 16)) <= 4 or short,
          "no day crams more than 4 sessions of one course")
    print("    plan:", {d.isoformat(): dict(Counter(v)) for d, v in sorted(plan.items()) if d >= date(2027, 1, 16)})


def main():
    semester_part()
    absence_part()
    weekly_part()
    marathon_part()
    print("\nFAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
