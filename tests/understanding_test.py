"""
הבנת הודעות חופשיות: בלי "קיבלתי." שקט. פלט מובנה, ניסיון חוזר, כשלים כנים, שורת "מה קרה", הפקודה "למה",
שאלות הסבר, אירועים רב-יומיים וקיבולת לטווח ימים. כולל התרחישים מצילומי המסך (חיפה, החתונה).
הרצה: python3 -m tests.understanding_test
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot import adapters as A  # noqa: E402
from bot.core import Bot  # noqa: E402
from tests.fakes import FakeLLM  # noqa: E402
from tests.features_test import bot_of, last, make_env, press, run_until, st, step, t  # noqa: E402

PERS = "me@cal"
IMG = b"\xff\xd8" + b"x" * 3000
FAILS: list[str] = []


def check(c, m):
    print(("  PASS " if c else "  FAIL ") + m)
    if not c:
        FAILS.append(m)


class Seq(FakeLLM):
    """תשובות לפי הסדר: dict = JSON, מחרוזת = טקסט גולמי, חריגה = כשל. אחרי שנגמרו: תשובה ריקה."""
    def __init__(self, *items):
        super().__init__()
        self.items, self.kw = list(items), []

    def complete(self, system, user, max_tokens=800, media=None, tool=None):
        self.calls.append(user)
        self.kw.append({"max_tokens": max_tokens, "tool": tool})
        it = self.items.pop(0) if self.items else {"actions": [], "reply": ""}
        if isinstance(it, Exception):
            raise it
        return it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)


def env(*items, start="2026-10-24T21:00", until="2026-10-25T07:20"):
    e = make_env(start)
    e["llm"] = Seq(*items)
    run_until(e, t(until))
    return e


def say(e, text):
    """התשובה לאחר ההודעה (ההצעות לאישור נשלחות אחריה)."""
    n = len(e["tg"].sent)
    e["tg"].user_text(text)
    b = step(e)
    return e["tg"].sent[n]["text"], b


def ai(e):
    return st(e)["bot"].get("last_ai", {})


def failures():
    print("== כשלים של Claude: בכולם הודעה כנה, לא \"קיבלתי.\"")
    bad_json = '{"actions":[],"reply":"הבנתי: כל הסופ"ש בחיפה. מה לרשום?"}'
    good = {"actions": [], "reply": "הבנתי: כל הסופ\"ש בחיפה. מה לרשום?"}
    e = env(bad_json, good)
    r, b = say(e, 'כל הסופ"ש הבא אני בחיפה')
    check(r.startswith("כל הסופ") and "הבנתי" not in r and "קיבלתי" not in r, f"unparsable first answer is retried, no echo prefix ({r[:40]!r})")
    check(len(e["llm"].calls) == 2 and "החזר שוב באותו מבנה" in e["llm"].calls[1] and "החזר שוב" not in e["llm"].calls[0], "retry carries a note")
    check(any("unparsable" in l for l in b.log), "failure reason logged")
    check(all(k["tool"] and k["max_tokens"] >= 2000 for k in e["llm"].kw), "structured tool + 2000+ tokens on every call")

    e = env(A.Truncated("cut"), {"actions": [], "reply": "בסדר, הבנתי"})
    r, b = say(e, "תסביר")
    check("הבנתי" in r and e["llm"].kw[1]["max_tokens"] == 2 * e["llm"].kw[0]["max_tokens"], "truncated: retry with a doubled limit")

    e = env(A.Truncated("cut"), A.Truncated("cut"))
    r, b = say(e, "הודעה ארוכה")
    check("לא הצלחתי לפענח" in r and "שמרתי" in r and "קיבלתי" not in r, f"truncated twice: honest message ({r[:50]!r})")
    check(ai(e)["status"] == "truncated" and any("truncated" in l for l in b.log), "status and log")
    check(st(e)["bot"]["notes"][-1]["text"] == "הודעה ארוכה", "message kept as a note")

    e = env("זה לא JSON בכלל", "גם לא זה")
    r, b = say(e, "משהו")
    check("לא הצלחתי לפענח" in r and ai(e)["status"] == "parse", "unparsable twice: honest message")

    e = env({"actions": [], "reply": ""}, {"actions": [], "reply": ""})
    r, b = say(e, "בלה בלה")
    check("לא הבנתי מה לעשות" in r and "קיבלתי" not in r and ai(e)["status"] == "empty", f"empty twice ({r[:40]!r})")
    check(any("empty plan" in l for l in b.log), "empty plan logged")

    e = env({"actions": [], "reply": ""}, {"actions": [], "reply": "לא ברור לי איזה יום. חמישי הקרוב?"})
    r, b = say(e, "בחמישי")
    check("חמישי הקרוב" in r, "empty first, useful second")

    e = env(A.Permanent("claude: 400 tools not supported"), {"actions": [], "reply": "הבנתי, ממשיך בלי כלי"})
    r, b = say(e, "שלום")
    check("ממשיך בלי כלי" in r and e["llm"].kw[0]["tool"] and e["llm"].kw[1]["tool"] is None and any("tool rejected" in l for l in b.log),
          "tool request rejected (4xx): falls back to plain text and still answers")
    e = env(A.Permanent("claude: 401 bad key"), A.Permanent("claude: 401 bad key"))
    r, b = say(e, "שלום")
    check("לא הצלחתי לפענח אותה עכשיו" in r and ai(e)["status"] == "error" and len(e["llm"].calls) == 1,
          "a real auth failure is reported at once, no pointless second request")

    e = env(RuntimeError("claude: 529"))
    r, b = say(e, "שלום")
    check("לא הצלחתי לפענח אותה עכשיו" in r and ai(e)["status"] == "error" and len(e["llm"].calls) == 1, "Claude down: saved, no pointless retry")

    e = env({"actions": [], "reply": ""}, {"actions": [], "reply": ""})
    e["tg"].user_media("photo", "f", IMG, caption="זה מה?")
    step(e)
    check("לא הצלחתי להוציא מהקובץ" in e["tg"].sent[-1]["text"], "photo with an empty answer: honest message")


def outcome_line():
    print("== בכל הודעה שורה שאומרת מה קרה")
    e = env({"actions": [], "reply": "כרגע יש לך 3 חזרות כי היעד במתמטיקה הוא 3."})
    r, _ = say(e, "למה 3 חזרות?")
    check(r.endswith("ℹ️ לא שיניתי כלום.") and "היעד במתמטיקה" in r, "answer only: 'nothing changed'")
    e = env({"actions": [{"action": "note", "text": "x"}], "reply": "רשמתי לעצמי"})
    r, _ = say(e, "תזכור")
    check("רשמתי הערה" in r, "note only")
    e = env({"actions": [{"action": "personal", "op": "create", "title": "פגישה", "start": "2026-10-27T10:00", "end": "2026-10-27T11:00"}],
             "reply": "הבנתי: פגישה"})
    r, _ = say(e, "פגישה בשלישי ב-10")
    check("📨 שלחתי הצעה אחת לאישור" in r and any(m["text"].startswith("🗓️") for m in e["tg"].sent[-2:]), "proposal only")
    e = env({"actions": [{"action": "done", "id": "nope"}], "reply": ""})
    r, _ = say(e, "עשיתי")
    check("לא ביצעתי" in r and r.endswith("ℹ️ לא שיניתי כלום."), "failed action shows its error, then says nothing changed")
    e = env({"actions": [{"action": "dance"}], "reply": "רקדתי"})
    r, _ = say(e, "רקוד")
    check("פעולה לא מוכרת" in r and "ℹ️ בוצע" not in r and r.endswith("ℹ️ לא שיניתי כלום."), f"unknown action is an error, not 'done' ({r[-60:]!r})")
    e = env({"actions": [{"action": "cancel_proposal", "id": "nope"}], "reply": "ביטלתי"})
    r, _ = say(e, "בטל את ההצעה")
    check(r.endswith("ℹ️ לא שיניתי כלום."), "cancelling a proposal that is not pending changes nothing, and says so")
    e = env({"actions": [{"action": "personal", "op": "create", "title": "פגישה", "start": "2026-10-27T10:00", "end": "2026-10-27T11:00"}], "reply": "הצעתי"},
            {"actions": [{"action": "cancel_proposal", "id": "PID"}], "reply": "ביטלתי"})
    say(e, "פגישה בשלישי")
    pid = next(iter(st(e)["bot"]["pp"]))
    e["llm"].items[0]["actions"][0]["id"] = pid
    r, _ = say(e, "לא משנה, בטל")
    check("ביטלתי את ההצעה שחיכתה לאישור" in r and st(e)["bot"]["pp"][pid]["state"] == "cancelled", f"real cancellation is reported ({r[-50:]!r})")
    long_reply = "מילה " * 1100
    e = env({"actions": [], "reply": long_reply})
    r, _ = say(e, "ספר לי הכול")
    check(len(r) <= 4000 and r.endswith("ℹ️ לא שיניתי כלום."), f"a very long reply is trimmed so the last line survives ({len(r)} chars)")
    e = env({"actions": [{"action": "note", "text": "x"}, {"oops": 1}, "junk"], "reply": "רשמתי"})
    r, b = say(e, "תזכור")
    check("דילגתי על 2 פעולות" in r and any("dropped 2" in l for l in b.log), "malformed actions are reported, not dropped silently")
    e = env({"actions": [{"action": "capacity", "date": "2026-10-29", "max_sessions": 0, "text": "x"}], "reply": ""})
    r, _ = say(e, "ביום חמישי אין לי זמן")
    check("עודכן" in r and "ℹ️ לא שיניתי" not in r, "executed action: its own result, no 'nothing changed'")
    e = env({"actions": [], "reply": "מה קורה"})
    say(e, "היי")
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["chat"] = [{"w": "u", "t": "מה קיבלת?", "at": P.iso(b.now)}, {"w": "b", "t": "קיבלתי.", "at": P.iso(b.now)},
                           {"w": "b", "t": "כן, קיבלתי את הבקשה", "at": P.iso(b.now)}]
    txt = b.chat_text()
    check("כן, קיבלתי את הבקשה" in txt and txt.count("קיבלתי.") == 0 and "מה קיבלת?" in txt,
          "old bare 'קיבלתי.' replies are not fed back to Claude")


def why_command():
    print("== הפקודה \"למה\"")
    e = env()
    e["tg"].user_text("מה עשית?")
    step(e)
    check("עוד לא פענחתי" in e["tg"].sent[-1]["text"], "no message yet")
    e = env({"actions": [], "reply": "כי עוד לא הספקת לסמן שיעור."})
    e["tg"].user_text("למה?")
    step(e)
    check(len(e["llm"].calls) == 1 and "עוד לא הספקת" in e["tg"].sent[-1]["text"],
          "a bare 'למה?' with nothing recently decoded is a question for Claude, not the debug view")
    e = env({"actions": [{"action": "capacity", "date": "2026-10-29", "max_sessions": 0, "text": "x"},
                         {"action": "personal", "op": "create", "title": "תור", "start": "2026-10-27T10:00", "end": "2026-10-27T11:00"}],
             "reply": "הבנתי: חמישי פנוי"})
    say(e, "ביום חמישי אין לי זמן ויש לי תור בשלישי")
    e["tg"].user_text("למה")
    step(e)
    w = e["tg"].sent[-1]["text"]
    check(len(e["llm"].calls) == 1, "'למה' right after a decoded message shows what was understood (no new Claude call)")
    check(w.startswith("🔍") and "ביום חמישי אין לי זמן" in w and "capacity 2026-10-29" in w and "✅ הובנה" in w, "shows the message, status and actions")
    check("מה קרה:" in w and "עודכן" in w and "הצעות לאישור" in w, "shows what happened")
    check(ai(e)["text"].startswith("ביום חמישי"), "command does not overwrite the last analysis")
    for cmd in ("מה עשית", "/why", "מה הבנת?"):
        e["tg"].user_text(cmd)
        step(e)
        check(e["tg"].sent[-1]["text"].startswith("🔍"), f"'{cmd}' works")
    blob = st(e)
    blob["bot"]["last_ai"]["at"] = P.iso(e["clock"].t - timedelta(hours=2))      # הניתוח האחרון ישן
    e["store"].blob = json.dumps(blob, ensure_ascii=False)
    e["llm"].items.append({"actions": [], "reply": "כי ביקשת."})
    e["tg"].user_text("למה?")
    step(e)
    check(e["tg"].sent[-1]["text"] == "כי ביקשת." + "\nℹ️ לא שיניתי כלום.", "two hours later a bare 'למה?' goes to Claude")
    e["tg"].user_text("מה עשית")
    step(e)
    check(e["tg"].sent[-1]["text"].startswith("🔍") and "(" in e["tg"].sent[-1]["text"].split("\n")[0], "'מה עשית' always works; shows the time")
    e = env(A.Truncated("x"), A.Truncated("x"))
    say(e, "ארוך")
    e["tg"].user_text("למה")
    step(e)
    check("התשובה נחתכה" in e["tg"].sent[-1]["text"], "failure status is shown")


def health_check():
    print("== בדיקת חיבורים כוללת את הפלט המובנה")
    e = env()
    b = bot_of(e)
    b.load_state()
    txt = b.health_text("🔧")
    check("✅ Claude (פלט מובנה): תקין" in txt, "health lists the structured-output step")


def explain_context():
    print("== שאלות הסבר: עובדות התכנון בהקשר")
    e = env()
    b = bot_of(e)
    b.load_state()
    ctx = json.loads(b.context_for_llm())
    check(ctx["weekly_targets"]["math"] == 3 and ctx["course_kind"]["math"] == "cumulative", "weekly targets and course kind")
    check("11 סשנים" in ctx["planning_rules"] and "kind=review" in ctx["planning_rules"], "planning rules")
    rev = [s for s in ctx["sessions"] if s.get("kind") == "review"]
    check(rev and all(s.get("lesson_day") for s in rev), f"each review session says which lesson it repeats ({len(rev)} sessions)")
    check(ctx["last_lesson_end"] and all(len(v) == 5 for v in ctx["last_lesson_end"].values()), "last lesson end per day")
    sysmsg = Bot.__module__ and __import__("bot.core", fromlist=["LLM_SYSTEM"]).LLM_SYSTEM
    check("שאלת \"למה\"" in sysmsg and "לעולם לא reply ריק" in sysmsg, "prompt asks for an explanation and a non-empty reply")


def multi_day():
    print("== אירוע רב-יומי, קיבולת לטווח, \"אחרי הלימודים\" (התרחיש של חיפה)")
    e = env()
    b = bot_of(e)
    b.load_state()
    ctx = json.loads(b.context_for_llm())
    thu = next(d for d in sorted(ctx["last_lesson_end"]) if date.fromisoformat(d).weekday() == 3 and d > "2026-10-25")
    end = ctx["last_lesson_end"][thu]
    fri, sat = (date.fromisoformat(thu) + timedelta(days=i) for i in (1, 2))
    e["llm"] = Seq({"reply": "הבנתי: חיפה בסופ\"ש, ובחמישי נסיעה אחרי הלימודים", "actions": [
        {"action": "personal", "op": "create", "title": "נסיעה לחיפה", "start": f"{thu}T{end}", "end": f"{thu}T{int(end[:2]) + 2}:{end[3:]}"},
        {"action": "personal", "op": "create", "all_day": True, "date": fri.isoformat(), "end_date": sat.isoformat(), "title": "בחיפה"},
        {"action": "capacity", "date": thu, "to": sat.isoformat(), "max_sessions": 0, "text": "חיפה"}]})
    r, _ = say(e, 'כל הסופ"ש הבא אני בחיפה, תקשום לי ביומן שאני צריך לנסוע אחרי הלימודים בחמישי')
    props = [m for m in e["tg"].sent if m["text"].startswith("🗓️")]
    check(len(props) == 2, f"two proposals ({len(props)})")
    multi = next(m for m in props if "בחיפה»" in m["text"])
    check("מ-" in multi["text"] and "עד" in multi["text"] and "כל הימים" in multi["text"], f"multi-day wording ({multi['text'][-60:]!r})")
    trip = next(m for m in props if "נסיעה" in m["text"])
    check(end in trip["text"], f"trip starts at the last lesson's end ({end})")
    cap = st(e).get("capacity", {})
    check(set(cap) == {thu, fri.isoformat(), sat.isoformat()} and all(v == 0 for v in cap.values()), f"capacity 0 for Thu, Fri and Sat ({sorted(cap)})")
    check("רשמתי: אין סשנים ב-3 ימים" in r, f"range summarised in one line, counting every day ({r[:70]!r})")
    left = [x for x in st(e)["sessions"] if x["status"] == "planned" and x["start"][:10] in cap]
    check(not left, f"nothing left planned on the trip days, and nothing moved onto another day of the range ({len(left)})")
    check(not e["cal"].cals.get(PERS), "nothing written to the personal calendar yet")
    press(e, multi, lambda tx, d: d.startswith("pp|ok"))
    ev = list(e["cal"].cals[PERS].values())[0]
    check(ev["start"] == {"date": fri.isoformat()} and ev["end"] == {"date": (sat + timedelta(days=1)).isoformat()},
          f"approval creates one all-day event Fri–Sat (end exclusive): {ev['start']} {ev['end']}")
    for bad, why in (({"end_date": "2026-10-20"}, "end before start"), ({"end_date": "2026-12-30"}, "over 30 days"),
                     ({"end_date": "2026-12-02"}, "31 days inclusive")):
        e["llm"].items.append({"reply": "", "actions": [dict({"action": "personal", "op": "create", "all_day": True, "date": "2026-11-02",
                                                               "title": "x"}, **bad)]})
        r, _ = say(e, "אירוע")
        check("לא ביצעתי" in r, f"rejected: {why}")
    e["llm"].items.append({"reply": "", "actions": [{"action": "capacity", "date": "2026-10-26", "to": "2026-12-30", "max_sessions": 0}]})
    r, _ = say(e, "מילואים")
    check("לא ביצעתי" in r and "טווח" in r, "capacity range over 14 days rejected")
    e["llm"].items.append({"reply": "", "actions": [{"action": "personal", "op": "create", "all_day": True, "date": "2026-11-02", "end_date": "2026-12-01",
                                                      "title": "מילואים"}]})
    n = len(e["tg"].sent)
    r, _ = say(e, "מילואים חודש")
    check("לא ביצעתי" not in r, "exactly 30 days inclusive is accepted")
    cap_cases(e)


def cap_cases(e):
    """קלט קיבולת לא תקין או חסר, בלי להפיל את ההודעה."""
    for bad, word in (({"max_sessions": -1}, "שלילי"), ({"max_sessions": "הרבה"}, "מספר"), ({"date": "אתמול בערב"}, "תאריך")):
        e["llm"].items.append({"reply": "", "actions": [dict({"action": "capacity", "date": "2026-10-28", "max_sessions": 1}, **bad)]})
        r, _ = say(e, "קיבולת")
        check("לא ביצעתי" in r and word in r, f"capacity input {bad}: Hebrew error ({r[-50:]!r})")
    e["llm"].items.append({"reply": "", "actions": [{"action": "capacity", "date": "2026-10-28", "max_sessions": "0", "text": "x"}]})
    say(e, "אין")
    check(st(e)["capacity"].get("2026-10-28") == 0, "'0' as a string is understood")
    qs = len(st(e)["pending_questions"])
    e["llm"].items.append({"reply": "", "actions": [{"action": "capacity", "date": "2026-11-03", "to": "2026-11-06", "max_sessions": None, "text": "עסוק"}]})
    r, _ = say(e, "אני עסוק בימים האלה")
    check(len(st(e)["pending_questions"]) == qs + 1 and "כמה סשנים" in r and "2026-11-03" not in st(e).get("capacity", {}),
          "a range without a number: one question, nothing guessed")


def surplus_cases():
    print("== העברת עודף: חזרה שבועית, ושיבוץ מחדש לא לפני השיעור")
    e = env()
    e["llm"].items.append({"reply": "", "actions": [{"action": "capacity", "date": "2026-10-25", "max_sessions": 2, "text": "ביום ראשון רק 2"}]})
    r, _ = say(e, "ביום ראשון רק שתי חזרות")
    weekly = [x for x in st(e)["sessions"] if x["kind"] == "weekly" and x["status"] == "planned"]
    check("מרתון" not in r and "עודכן" in r, f"weekly surplus message is not about the marathon ({r[:90]!r})")
    check(len([x for x in weekly if x["start"][:10] == "2026-10-25"]) <= 2, "Sunday is down to the limit")
    check(all(x["status"] in ("planned", "dropped") for x in st(e)["sessions"] if x["kind"] == "weekly"), "weekly sessions moved or dropped, never marked missed")

    e = env()
    b = bot_of(e)
    b.load_state()
    uni, _ = b.load_uni()
    today = date(2026, 10, 27)
    lesson = date(2026, 10, 29)
    state = dict(st(e), sessions=[{"key": "review:math:x", "kind": "review", "course": "math", "courses": ["math"], "status": "missed",
                                    "start": "2026-10-27T20:00+01:00", "end": "2026-10-27T20:45+01:00", "lesson_day": lesson.isoformat()}])
    r = P.reschedule(today, uni, [], state, datetime(2026, 10, 27, 8, 0, tzinfo=P.TZ))
    days = [x["start"][:10] for x in r["rescheduled"]]
    check(all(d >= lesson.isoformat() for d in days), f"a missed review is never moved to before its lesson ({days})")


def mail_tool():
    print("== סיכום מיילים: פלט מובנה, חיתוך וגיבוי")
    mail = [json.dumps({"id": "m1", "from": "dean@runi.ac.il", "subject": "הרשמה", "date": "2026-10-24", "body": "עד 15.11"})]
    ans = {"items": [{"id": "m1", "important": True, "summary": "הרשמה עד 15.11", "actions": []}]}

    def run(*items):
        e = make_env("2026-10-24T21:00")
        e["llm"] = Seq(*items)
        e["store"].mail = list(mail)
        run_until(e, t("2026-10-25T08:10"))
        return e

    e = run(A.Truncated("cut"), ans)
    kw = e["llm"].kw
    morning = next((m for m in e["tg"].sent if m["text"].startswith("☀️")), None)
    check(kw and kw[0]["tool"] and "items" in kw[0]["tool"]["properties"] and kw[0]["max_tokens"] >= 4000, "mail digest uses a structured tool and a large limit")
    check(len(kw) == 2 and kw[1]["max_tokens"] == 2 * kw[0]["max_tokens"] and morning and "הרשמה עד 15.11" in morning["text"],
          "truncated digest is retried with a doubled limit")
    e = run(A.Permanent("claude: 400 tools are not supported"), ans)
    morning = next((m for m in e["tg"].sent if m["text"].startswith("☀️")), None)
    check(e["llm"].kw[1]["tool"] is None and morning and "הרשמה עד 15.11" in morning["text"], "tool rejected: falls back to plain text")


def health_cases():
    print("== בדיקת חיבורים: פינג קצר שנחתך אינו כשל")
    from bot import health

    class Cut:
        def complete(self, system, user, max_tokens=800, media=None, tool=None):
            if tool:
                return json.dumps({"ok": True})
            raise A.Truncated("cut")

    out = "\n".join(health.run(type("T", (), {})(), type("C", (), {"email": "x", "list": lambda *a: [], "access_role": lambda *a: "writer",
                                                                  "create": lambda *a: {"id": "1"}, "delete": lambda *a: None})(),
                              type("F", (), {"fetch": lambda s: ""})(), type("S", (), {})(), Cut(),
                              {"REVIEWS_CAL": "r", "PERSONAL_CAL": "p"}, datetime(2026, 10, 25, 8, 0, tzinfo=P.TZ)))
    check("✅ Claude: מגיב" in out and "✅ Claude (פלט מובנה): תקין" in out and "❌ Claude" not in out, "a cut-off ping is not a failure")


def media_blocked():
    print("== קובץ: פעולות חסומות מסומנות ומדווחות")
    e = env({"actions": [{"action": "done", "id": "x"}, {"action": "note", "text": "ראיתי תמונה"}], "reply": "ראיתי"})
    e["tg"].user_media("photo", "f", IMG, caption="זה מה?")
    step(e)
    r = e["tg"].sent[-1]["text"]
    check("מקובץ אני מציע רק" in r and "פעולה אחת" in r, f"blocked action is reported ({r[-60:]!r})")
    w = bot_of(e)
    w.load_state()
    check("נחסם" in w.why_text() and "(קובץ)" in w.why_text(), "the debug view marks it as blocked")
    check(any(x["w"] == "u" and "[תמונה]" in x["t"] for x in st(e)["bot"]["chat"]), "the photo caption enters the chat history")


def history_on_failure():
    print("== הודעה שנכשלה נשארת בהיסטוריית השיחה")
    e = env("לא JSON", "גם לא")
    say(e, "כל הסופ\"ש בחיפה")
    check(any(x["w"] == "u" and "בחיפה" in x["t"] for x in st(e)["bot"]["chat"]), "failed message is in the history, so 'מה קיבלת?' can see it")
    e["llm"].items.append({"actions": [], "reply": "ראיתי"})
    say(e, "מה קיבלת?")
    ctx = e["llm"].calls[-1]
    check("בחיפה" in ctx, "the next message gets the failed one as context")
    b = bot_of(e)
    b.load_state()
    c = json.loads(b.context_for_llm())
    check(any("בחיפה" in n for n in c["recent_notes"]), "recent notes reach the context")


def pending_view():
    print("== הצעות ממתינות: סוף אירוע יום שלם כולל")
    e = env({"actions": [{"action": "personal", "op": "create", "all_day": True, "date": "2026-10-30", "end_date": "2026-10-31", "title": "בחיפה"}], "reply": "הצעתי"})
    say(e, "בחיפה בסופש")
    b = bot_of(e)
    b.load_state()
    p = b.pending_for_llm()
    check(p and p[0]["all_day"] and p[0]["start"] == "2026-10-30" and p[0]["end"] == "2026-10-31", f"inclusive last day ({p})")


def adapter():
    print("== Claude.complete: כלי מובנה וחיתוך (בלי רשת)")
    sent = {}

    class R:
        def __init__(self, data):
            self.status_code, self.data = 200, data

        def json(self):
            return self.data

    def post_ok(url, timeout, headers, json):  # noqa: A002
        sent.update(json, _timeout=timeout)
        return R({"stop_reason": "tool_use", "content": [{"type": "text", "text": "ignored"},
                                                        {"type": "tool_use", "name": "respond", "input": {"actions": [], "reply": 'כל הסופ"ש'}}]})

    real = A.requests.post
    try:
        A.requests.post = post_ok
        c = A.Claude("k", "m")
        out = c.complete("s", "u", 2000, tool={"type": "object"})
        check(json.loads(out) == {"actions": [], "reply": 'כל הסופ"ש'}, "tool input comes back as valid JSON, quote and all")
        check(sent["tool_choice"] == {"type": "auto"} and sent["tools"][0]["input_schema"] == {"type": "object"} and "respond" in sent["system"],
              "request offers the tool and tells the model to use it (forced tool_choice is rejected by the live model)")
        check(sent["_timeout"] == 60, "normal request: 60s")
        c.complete("s", "u", 4000, tool={"type": "object"})
        check(sent["_timeout"] == 120, "a large output limit gets a longer timeout")
        A.requests.post = lambda *a, **k: R({"stop_reason": "end_turn", "content": [{"type": "text", "text": "טקסט"}]})
        check(c.complete("s", "u") == "טקסט", "no tool: plain text as before")
        A.requests.post = lambda *a, **k: R({"stop_reason": "max_tokens", "content": [{"type": "tool_use", "input": {"actions": [{"ac": 1}]}}]})
        try:
            c.complete("s", "u", 800, tool={"type": "object"})
            ok = False
        except A.Truncated:
            ok = True
        check(ok, "output cut at max_tokens raises Truncated (not retried as a network error)")
    finally:
        A.requests.post = real


def parse_unit():
    print("== parse_plan")
    p = Bot.parse_plan
    check(p('בטח!\n```json\n{"actions":[],"reply":"אוקיי"}\n```')["reply"] == "אוקיי", "fenced JSON with text around it")
    check(p('{"actions":"","reply":5}') == {"actions": [], "reply": "5", "dropped": 0}, "bad shapes are normalised")
    check(p('{"actions":"x","reply":"a"}') is None, "an unparsable non-empty actions string is a parse failure (retried), not 'no actions'")
    check(p(json.dumps({"actions": json.dumps([{"action": "note", "text": "ש"}]), "reply": "r"}))["actions"] == [{"action": "note", "text": "ש"}],
          "actions delivered as a JSON string are decoded")
    check(p('{"actions":{"action":"note"},"reply":"r"}')["actions"] == [{"action": "note"}], "a single action object is wrapped")
    check(p('{"actions":[1,{"action":""},{"x":1},{"action":"note"}],"reply":"r"}')["dropped"] == 3, "dropped actions are counted")
    check(p('{"actions":[1,"a",{"action":"note"}],"reply":null}')["actions"] == [{"action": "note"}], "non-dict actions dropped")
    check(p("[1,2]") is None and p("") is None and p(None) is None and p("{oops") is None, "garbage = None")


REV = "reviews@cal"
SCREENSHOT = "אני רוצה להקדים את הסיכום השבועי ולראות אותו עכשיו"


def after(e, n):
    return [m for m in e["tg"].sent[n:]]


def deletion_policy():
    print("== מחיקה: החלטה של עודד = מבצעים ומדווחים. יוזמה של הבוט = רק הצעה")
    from bot.core import sid
    # 1) עודד מבטל סשן במפורש: בלי כפתור אישור, נמחק מהיומן, ודיווח "עודכן"
    e = env()
    s0 = st(e)
    tgt = min((x for x in s0["sessions"] if x.get("status") == "planned" and x.get("eventId") and x["start"][:10] == "2026-10-26"),
              key=lambda x: x["start"], default=None)
    check(tgt is not None, "a planned session tomorrow to cancel")
    if tgt:
        e["llm"].items.append({"actions": [{"action": "cancel", "id": sid(tgt["key"])}], "reply": "מבטל את התרגול של מחר."})
        n = len(e["tg"].sent)
        r, _ = say(e, "תבטל את תרגול מחר")
        x = next(y for y in st(e)["sessions"] if y["key"] == tgt["key"])
        check("🗑 עודכן" in r and tgt["start"][11:16] in r and "ℹ️ לא שיניתי" not in r, f"cancel reported as done ({r[-80:]!r})")
        check(not any(m["buttons"] for m in after(e, n)), "no approval buttons for a cancel Oded asked for")
        check(x["status"] == "dropped" and tgt["eventId"] not in e["cal"].cals[REV], "dropped and removed from the calendar")
        run_until(e, t("2026-10-26T08:00"))
        mb = last(e["tg"], "☀️")
        check(tgt["eventId"] not in e["cal"].cals[REV] and next(y for y in st(e)["sessions"] if y["key"] == tgt["key"])["status"] == "dropped",
              "the next morning does not recreate it")
        check(mb and "נמחק ממך ביומן" not in mb["text"], "not re-reported as a calendar deletion")
        e["llm"].items.append({"actions": [{"action": "restore", "id": sid(tgt["key"])}], "reply": "מחזיר."})
        r, _ = say(e, "תחזיר")
        check("↩️ הוחזר" in r and next(y for y in st(e)["sessions"] if y["key"] == tgt["key"])["status"] == "planned", "undo with 'תחזיר'")
        e["llm"].items.append({"actions": [{"action": "cancel", "id": "nope"}], "reply": "מבטל."})
        r, _ = say(e, "תבטל את זה")
        check("⚠️ לא ביצעתי" in r, "unknown session id: honest failure, nothing deleted")

    # 2) הבוט לבד (שבוע בלי לסמן כלום): משבץ מחדש או מציע, אבל לא מוחק שום דבר מהיומן
    e = make_env("2026-10-24T21:00")
    run_until(e, t("2026-10-31T08:00"), answer_daily=False)
    ss = st(e)["sessions"]
    check(not e["cal"].deleted, f"nothing deleted from the calendar on the bot's own initiative ({len(e['cal'].deleted)})")
    check(not [x for x in ss if x.get("status") == "dropped"], "no session dropped without Oded")
    check([x for x in ss if x.get("status") == "rescheduled"], "unconfirmed sessions were rescheduled instead")
    unpl = [x for x in ss if x.get("status") == "unplaced"]
    check(all(x.get("eventId") in e["cal"].cals[REV] or not x.get("eventId") for x in unpl),
          f"sessions without a slot stay in the calendar ({len(unpl)})")
    # הצעה שהבוט יוזם (השלמה) היא kind=rescheduled: כפתור אישור, ולא נספרת בתקציב התוספות
    b = bot_of(e)
    b.load_state()
    ws = P.week_start(b.now.date())
    cand = next((x for x in b.st["sessions"] if x.get("course") and x.get("kind") == "review"), None)
    before = b.extras_in_week(ws)
    n = len(e["tg"].sent)
    ok = b.propose_unplaced(cand["key"], "חזרה", b.now.date()) if cand else False
    xp = [p for p in b.st["bot"].get("xp", {}).values() if p.get("ref") == (cand or {}).get("key")]
    check(not ok or (xp and xp[0]["kind"] == "rescheduled" and b.extras_in_week(ws) == before
                     and any(m["buttons"] and m["buttons"][0][0][1].startswith("xp|ok") for m in after(e, n))),
          "bot-initiated: a proposal with ✅ buttons, extras budget untouched")


def report_now():
    print("== \"להקדים את הסיכום השבועי ולראות עכשיו\" (צילום המסך)")
    from bot.core import LLM_SYSTEM
    check("send_report" in LLM_SYSTEM and SCREENSHOT in LLM_SYSTEM and "set_job_time" in LLM_SYSTEM, "actions documented for Claude")
    # אמצע השבוע: סיכום מלא לקריאה בלבד (לא כותב את השבוע הבא ליומן, והסיכום של מוצ״ש עדיין יגיע)
    e = env({"actions": [{"action": "send_report", "report": "weekly"}], "reply": "הנה הסיכום השבועי עד עכשיו."},
            until="2026-10-29T16:00")
    evs = len(e["cal"].cals[REV])
    n = len(e["tg"].sent)
    r, _ = say(e, SCREENSHOT)
    msgs = after(e, n)
    check("bot_schedule" in e["llm"].calls[-1], "Claude sees the current message schedule")
    check("📨 שולח עכשיו: הסיכום השבועי" in r and "לא שיניתי" not in r and "אין לי" not in r, f"reply says it is sending ({r!r})")
    rep = msgs[1]["text"] if len(msgs) > 1 else ""
    check(rep.startswith("📊 סיכום שבוע") and "🗓 השבוע הבא:" in rep and "📈" in rep, "the full weekly summary arrives right after the reply")
    check(len(e["cal"].cals[REV]) == evs and not st(e)["bot"]["jobs"].get("weekly:2026-10-29"), "mid-week: read-only")
    # מוצ״ש לפני השעה: הסיכום המלא עכשיו, ולא שוב ב-21:00
    e = env({"actions": [{"action": "send_report", "report": "weekly"}], "reply": "שולח."}, until="2026-10-31T19:00")
    say(e, SCREENSHOT)
    check(st(e)["bot"]["jobs"].get("weekly:2026-10-31") == "done", "Saturday: the scheduled weekly is done early")
    run_until(e, t("2026-10-31T22:00"))
    weekly = [m for m in e["tg"].sent if m["text"].startswith("📊 סיכום שבוע") and m["at"].date() == date(2026, 10, 31)]
    check(len(weekly) == 1 and weekly[0]["at"] < t("2026-10-31T20:00") and "לשנות משהו בשבוע הבא" in weekly[0]["text"],
          f"sent once, early, as the full weekly ({len(weekly)})")
    # דוחות נוספים לפי בקשה
    e = env({"actions": [{"action": "send_report", "report": "reinforce"}, {"action": "send_report", "report": "week_plan"},
                         {"action": "send_report", "report": "nope"}], "reply": "שולח."})
    n = len(e["tg"].sent)
    r, _ = say(e, "תראה לי מה צריך חיזוק ואת התוכנית")
    texts = [m["text"] for m in after(e, n)]
    check(any(x.startswith("💪") for x in texts) and any(x.startswith("📅") or "שבוע" in x.split("\n")[0] for x in texts[2:]),
          "reinforce and week plan sent on request")
    check("⚠️ לא ביצעתי" in r, "unknown report: honest failure")


def job_times():
    print("== שינוי שעה של הודעה קבועה")
    e = env({"actions": [{"action": "set_job_time", "job": "daily", "time": "20:00"}], "reply": "בסדר."},
            {"actions": [{"action": "send_report", "report": "schedule"}], "reply": "הנה."})
    r, _ = say(e, "תשלח לי את הסיכום היומי ב-20:00")
    check("⏰ עודכן: הסיכום היומי יישלח ב-20:00 במקום 21:30." in r, f"old → new time confirmed ({r[-60:]!r})")
    check(st(e)["bot"]["job_times"] == {"daily": {"time": "20:00"}}, "persisted in state")
    run_until(e, t("2026-10-26T22:30"))
    daily = [m for m in e["tg"].sent if m["text"].startswith("🌙") and m["at"].date() == date(2026, 10, 26)]
    check(len(daily) == 1 and "20:00" <= daily[0]["at"].strftime("%H:%M") < "20:10", f"scheduler honours it ({[m['at'] for m in daily]})")
    n = len(e["tg"].sent)
    say(e, "מתי נשלחות ההודעות?")
    sched = after(e, n)[-1]["text"]
    check(sched.startswith("⏰ הודעות קבועות") and "הסיכום היומי: ראשון עד חמישי 20:00 (שינית)" in sched
          and "הסיכום השבועי: מוצ״ש 21:00" in sched, "schedule answered from JOBS + overrides")
    e["tg"].user_text("תזמונים")
    step(e)
    check(e["tg"].sent[-1]["text"].startswith("⏰ הודעות קבועות"), "\"תזמונים\" command")

    bad = [({"job": "daily", "time": "23:30"}, "22:30"), ({"job": "weekly", "time": "19:00", "days": ["fri"]}, "מוצ״ש"),
           ({"job": "weekly", "time": "12:00"}, "18:00"), ({"job": "morning", "time": "08:00", "days": ["sat"]}, "בשבת"),
           ({"job": "daily", "time": "25:00"}, "השעה"), ({"job": "recap", "time": "18:00"}, "ריקאפ"), ({"job": "morning", "off": True}, "מכבה")]
    for a, why in bad:
        e["llm"].items.append({"actions": [dict(a, action="set_job_time")], "reply": "בסדר."})
        r, _ = say(e, "שנה שעה")
        check("⚠️ לא ביצעתי" in r and why in r and "עודכן" not in r, f"rejected {a} ({r[-70:]!r})")
    check(st(e)["bot"]["job_times"] == {"daily": {"time": "20:00"}}, "rejected changes leave the state alone")
    e["llm"].items.append({"actions": [{"action": "set_job_time", "job": "weekly", "time": "19:30"},
                                       {"action": "set_job_time", "job": "reinforce", "time": "21:00", "days": ["חמישי"]},
                                       {"action": "set_job_time", "job": "reinforce", "off": True}], "reply": "בסדר."})
    r, _ = say(e, "סיכום שבועי ב-19:30, חיזוק רק בחמישי ב-21, בעצם תכבה אותו")
    jt = st(e)["bot"]["job_times"]
    check("במקום 21:00" in r and jt["weekly"] == {"time": "19:30"} and jt["reinforce"] == {"off": True}, f"several changes ({jt})")
    e["llm"].items.append({"actions": [{"action": "set_job_time", "job": "all", "reset": True}], "reply": "בסדר."})
    r, _ = say(e, "חזור לברירת מחדל")
    check("חזרו לברירת המחדל" in r and "הסיכום היומי: ראשון עד חמישי 21:30" in r and not st(e)["bot"]["job_times"], "reset to default")
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["job_times"] = {"daily": {"time": "22:30"}}
    b.now = t("2026-10-27T22:40")
    check([x for x in b.due_jobs() if x[0] == "daily"], "a changed time fires inside its window")
    b.now = t("2026-10-27T23:10")
    b.st["bot"]["jobs"].pop("daily:2026-10-27", None)
    check(not [x for x in b.due_jobs() if x[0] == "daily"], "…and never after 23:00")


def main():
    failures()
    outcome_line()
    why_command()
    health_check()
    explain_context()
    multi_day()
    surplus_cases()
    mail_tool()
    health_cases()
    media_blocked()
    history_on_failure()
    pending_view()
    adapter()
    parse_unit()
    deletion_policy()
    report_now()
    job_times()
    print("\nFAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
