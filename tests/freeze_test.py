"""
מצב freeze (PR B): הבוט לא משבץ ולא מזיז לבד. בודק על שלושה שבועות מדומים:
  בלי אירועים חדשים ביומן החזרות, בלי "לא מצאתי מקום", בלי תוכנית שבוע, בלי כיול, בלי 🔧 בהצלחה,
  שאלת q1 הוסרה, ברירת המחדל של מצב קיים בלי שדה mode היא freeze.
הרצה: python3 -m tests.freeze_test
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bot.core import Bot  # noqa: E402
from tests.features_test import REV, make_env, run_until, st, t, tick, bot_of  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def freeze_env(start):
    e = make_env(start)
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["mode"] = "freeze"
    b.save_state()
    return e


def events(e):
    return [x for x in e["cal"].cals.get(REV, {}).values()] if isinstance(e["cal"].cals.get(REV), dict) else list(e["cal"].cals.get(REV, []))


def main():
    print("== freeze: שלושה שבועות בלי שיבוץ אוטומטי")
    e = freeze_env("2026-10-29T07:00")
    before = len(events(e))
    run_until(e, t("2026-11-18T23:00"), step=15)
    after = len(events(e))
    tg = e["tg"]
    texts = [m["text"] for m in tg.sent]
    writes = [c for c in e["cal"].calls if c[0] in ("create", "patch", "revive", "dup", "delete") and c[1] == REV]
    check(after <= before and not writes, f"no create/patch/delete on the reviews calendar ({before} → {after}, writes={len(writes)})")
    check(not any("לא מצאתי מקום" in x for x in texts), "no 'couldn't find a slot' decisions")
    check(not any("השבוע מלא" in x for x in texts), "no 'week is full' lines")
    check(not any("ישובץ מחדש" in x for x in texts), "daily summary does not promise rescheduling")
    check(not any("קיבולת למחר" in x for x in texts), "no capacity question in freeze")
    check(any(x.startswith("🗓") or "סיכום שבוע" in x for x in texts) or any("אני לא משבץ לבד" in x for x in texts), "weekly summary ran")
    check(any("אני לא משבץ לבד" in x for x in texts), "weekly summary explains there is no auto plan")
    check(not any("שאלה לכל קורס: החומר מצטבר" in x for x in texts), "no per-course calibration questions")
    check(st(e)["bot"]["mode"] == "freeze", "mode stays freeze")

    print("== עדכון קוד: בדיקת חיבורים בצ'אט רק בכשל")
    e = freeze_env("2026-10-29T07:00")
    b = bot_of(e)
    b.load_state()
    n = len(e["tg"].sent)
    b.health_text = lambda head: head + "\n✅ טלגרם\n✅ Claude: תקין"
    b.after_deploy("abc1234")
    check(len(e["tg"].sent) == n, "healthy deploy sends nothing to the chat")
    b.health_text = lambda head: head + "\n✅ טלגרם\n❌ Upstash: תקלה"
    b.after_deploy("abc5678")
    check(len(e["tg"].sent) == n + 1 and "❌" in e["tg"].sent[-1]["text"], "failed health check does reach the chat")

    print("== קיבולת במצב freeze: לא מזיזים עודף לבד")
    e = make_env("2026-10-29T07:00")
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["mode"] = "v1"
    b.save_state()
    run_until(e, t("2026-10-31T23:00"), step=15)      # v1 מייצר סשנים, ואז עוברים ל-freeze
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["mode"] = "freeze"
    days = {}
    for s_ in b.st["sessions"]:
        if s_.get("status") == "planned" and s_["start"] > e["clock"]().isoformat():
            days.setdefault(s_["start"][:10], []).append(s_)
    day = next((d for d, v in sorted(days.items()) if len(v) >= 2), None)
    if day:
        n_calls = len(e["cal"].calls)
        out = b.apply_capacity(day, 0, False, "בלי סשנים")
        moved = [c for c in e["cal"].calls[n_calls:] if c[0] in ("create", "patch", "revive", "dup", "delete") and c[1] == REV]
        check(not moved and "לא מזיז" in out, "capacity limit in freeze moves nothing and says so")
    else:
        check(False, "setup: a day with 2+ planned sessions exists in the seed")

    print("== מצב קיים עם q1 ובלי mode (כמו ב-Upstash)")
    seed = json.load(open(os.path.join(os.path.dirname(__file__), "..", "bot", "seed_state.json"), encoding="utf-8"))
    seed["pending_questions"] = [{"id": "q1", "text": "ניהול בארגונים: מבחן או עבודה? (יתברר בשבוע הראשון)", "asked": "2026-10-15T02:24+03:00"},
                                 {"id": "q2", "text": "שאלה אחרת", "asked": "2026-10-20T10:00+03:00"}]
    seed.get("bot", {}).pop("mode", None)
    e = make_env("2026-10-29T07:00", state=seed)
    b = bot_of(e)
    os.environ.pop("STUDY_BOT_MODE", None)
    try:
        b.load_state()
    finally:
        os.environ["STUDY_BOT_MODE"] = "v1"
    ids = [q["id"] for q in b.st["pending_questions"]]
    check("q1" not in ids and "q2" in ids and b.frozen, f"stale q1 removed, other questions kept, freeze default ({ids})")
    b.st["bot"]["mode"] = "V1"
    check(b.frozen, "unknown mode value is treated as freeze")

    print("== נוכחות בטקסט חופשי: הודעה אחת")
    e = freeze_env("2026-10-29T07:00")
    b = bot_of(e)
    b.load_state()
    n = len(e["tg"].sent)
    out = b.set_attendance("mgmt", "2026-10-28", "absent", quiet=True)
    check(len(e["tg"].sent) == n and "נוכחות חובה בכל מפגש" in out, "quiet attendance returns the note inside the reply, sends nothing")
    b.set_attendance("math", "2026-10-26", "absent")
    n = len(e["tg"].sent)
    b.set_attendance("math", "2026-10-26", "absent")
    check(len(e["tg"].sent) == n, "pressing the same absence twice does not resend the count")

    print("== מצב קיים בלי שדה mode")
    e = make_env("2026-10-29T07:00")
    b = bot_of(e)
    os.environ.pop("STUDY_BOT_MODE", None)
    try:
        b.load_state()
        check(b.st["bot"]["mode"] == "freeze" and b.frozen, "missing mode defaults to freeze")
    finally:
        os.environ["STUDY_BOT_MODE"] = "v1"
    check(not any(q.get("id") == "q1" for q in b.st.get("pending_questions", [])), "q1 'exam or assignment' question is gone")

    print("== v1 עדיין עובד כשמבקשים אותו במפורש")
    e = make_env("2026-10-29T07:00")
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["mode"] = "v1"
    check(not b.frozen, "v1 is not frozen")

    print("== בדיקת ביקורת: v1 באותם תנאים כן יוצר אירועים (הבדיקה באמת מודדת)")
    e = make_env("2026-10-29T07:00")
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["mode"] = "v1"
    b.save_state()
    n0 = len(events(e))
    run_until(e, t("2026-11-08T23:00"), step=15)
    check(len(events(e)) > n0, f"v1 control creates events ({n0} → {len(events(e))})")

    print("FAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
