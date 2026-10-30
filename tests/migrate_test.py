"""
שלב G: מעבר ל-v2 באישור אחד. מוחק רק סשנים עתידיים שהבוט קבע לבד. הרצה: python3 -m tests.migrate_test
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.features_test import REV, bot_of, make_env, run_until, st, t, tick  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def evs(e):
    cal = e["cal"].cals.get(REV, {})
    return [x for x in (cal.values() if isinstance(cal, dict) else cal) if x.get("status") != "cancelled"]


def main():
    e = make_env("2026-11-01T07:00")
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["mode"] = "v1"
    b.save_state()
    run_until(e, t("2026-11-02T07:30"), step=10)       # v1 יוצר סשנים
    b = bot_of(e)
    b.load_state()
    # סשן שעודד קבע
    r = b.book_study("math", t("2026-11-04T19:00"), 40, "button")
    b.st["bot"]["mode"] = "freeze"
    b.save_state()
    print("== הצעה ==")
    n_before = len([s for s in st(e)["sessions"] if s["status"] == "planned" and s["start"] > "2026-11-02T08"])
    ev_before = len(evs(e))
    e["tg"].user_text("מעבר")
    n0 = len(e["tg"].sent)
    tick(e)
    p = [m for m in e["tg"].sent[n0:] if "מעבר למצב החדש" in m["text"]]
    check(len(p) == 1 and "mig|ok" in str(p[0]["buttons"]), "הצעת מעבר עם כפתור אישור")
    check(len(evs(e)) == ev_before and st(e)["bot"]["mode"] == "freeze", "ההצעה לא משנה כלום")
    check(r.startswith("✅") and "1 סשנים שקבעת" in p[0]["text"], "סשן של עודד מוגן וסופר")
    print("== אישור ==")
    e["tg"].press(p[0]["id"], "mig|ok")
    tick(e)
    s = st(e)
    check(s["bot"]["mode"] == "v2", "המצב עבר ל-v2")
    left = [x for x in s["sessions"] if x["status"] == "planned" and x["start"] > "2026-11-02T08"]
    check(len(left) == 1 and left[0].get("src") == "button", "נשאר רק הסשן שעודד קבע")
    check(len(evs(e)) < ev_before and any("מתמטיקה" in (x.get("summary") or "") for x in evs(e)), "נמחקו אירועי הבוט, נשאר שלו")
    check(any(m["text"].startswith("✅ עברנו") for m in e["tg"].sent), "דווח על התוצאה")
    e["tg"].press(p[0]["id"], "mig|ok")
    tick(e)
    check(len([m for m in e["tg"].sent if m["text"].startswith("✅ עברנו")]) == 1, "לחיצה חוזרת לא מבצעת שוב")
    print("== v2 לא משבץ לבד ==")
    n_ev = len(evs(e))
    run_until(e, t("2026-11-09T22:00"), step=30)
    check(len(evs(e)) == n_ev, "שבוע שלם ב-v2: אין אירועים חדשים ביומן")
    e["tg"].user_text("מעבר")
    n0 = len(e["tg"].sent)
    tick(e)
    check(any("כבר במצב v2" in m["text"] for m in e["tg"].sent[n0:]), "פקודה במצב v2 אומרת שכבר עברנו")
    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
