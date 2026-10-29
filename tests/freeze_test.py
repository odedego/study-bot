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
    check(after <= before, f"no new events in the reviews calendar ({before} → {after})")
    check(not any("לא מצאתי מקום" in x for x in texts), "no 'couldn't find a slot' decisions")
    check(not any("השבוע מלא" in x for x in texts), "no 'week is full' lines")
    check(not any("ישובץ מחדש" in x for x in texts), "daily summary does not promise rescheduling")
    check(not any("קיבולת למחר" in x for x in texts), "no capacity question in freeze")
    check(any(x.startswith("🗓") or "סיכום שבוע" in x for x in texts) or any("אני לא משבץ לבד" in x for x in texts), "weekly summary ran")
    check(any("אני לא משבץ לבד" in x for x in texts), "weekly summary explains there is no auto plan")
    check(not any("שאלה לכל קורס: החומר מצטבר" in x for x in texts), "no per-course calibration questions")
    check(not any(x.startswith("🔧") for x in texts), "no deploy health message when all is well")
    check(st(e)["bot"]["mode"] == "freeze", "mode stays freeze")

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
