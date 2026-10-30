"""
שלב F: ההודעות הקבועות בנוסח v2 (מצב freeze): סיכום 21:30 עם הצעת ערב, חיזוק, סיכום שבוע, בוקר, מטלות.
הרצה: python3 -m tests.msgs_test
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.features_test import bot_of, make_env, run_until, st, t, tick  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def env(start, mode="freeze"):
    e = make_env(start)
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["mode"] = mode
    b.save_state()
    return e


def sent(e, prefix, after=0):
    return [m for m in e["tg"].sent[after:] if m["text"].startswith(prefix)]


def main():
    print("== ערב רביעי: מחר חמישי חצי יום פנוי ==")
    e = env("2026-11-04T07:00")
    run_until(e, t("2026-11-04T21:40"), step=10)
    d = sent(e, "🌙")
    check(len(d) == 1, "סיכום יום נשלח")
    txt = d[0]["text"] if d else ""
    check("מחר אפשר ללמוד עד 13:45" in txt and "מתי נוח לך ללמוד?" in txt, "הצעת ערב לחמישי עד 13:45")
    sa = [dd for r in (d[0]["buttons"] or []) for _, dd in r if dd.startswith("sa|")] if d else []
    check(len(sa) >= 1 and all(x.startswith("sa|2026-11-05|") for x in sa), "כפתורי חלון למחר")
    check("ישובץ מחדש" not in txt and "/11" not in txt, "אין שפה של שיבוץ אוטומטי או יעד 11")
    check(txt.count("?") == 1, "שאלה אחת בהודעה")
    if sa:
        tm = [int(x.split("|")[2]) for x in sa]
        check(all(a < 1335 for a in tm), "חלונות חמישי מתחילים לפני השיעורים")
        e["tg"].press(d[0]["id"], sa[0])
        tick(e)
        sess = [s for s in st(e)["sessions"] if s.get("src") == "button"]
        check(len(sess) == 1 and sess[0]["start"][:10] == "2026-11-05" and sess[0]["end"][11:16] <= "13:45", "נקבע לחמישי ומסתיים לפני 13:45")
    print("== ערב חמישי: מחר שישי פנוי ==")
    run_until(e, t("2026-11-05T21:40"), step=10)
    d = sent(e, "🌙")
    check(len(d) == 2 and "מחר אין לימודים" in d[-1]["text"], "ערב חמישי: מחר אין לימודים")
    print("== ערב ראשון: מחר שני עמוס, אין הצעה ==")
    run_until(e, t("2026-11-08T21:40"), step=10)
    d = sent(e, "🌙")
    check(d and "מתי נוח לך ללמוד?" not in d[-1]["text"], "יום עמוס: בלי הצעה")
    print("== סיכום שבוע ושני חיזוק ==")
    b = bot_of(e)
    b.load_state()
    b.st["recaps"].append({"date": "2026-11-09", "course": "math", "rating": 2, "topics": "", "unclear": "נגזרות", "cause": "הקצב מהיר מדי", "at": "x"})
    b.save_state()
    run_until(e, t("2026-11-09T21:50"), step=10)
    r = sent(e, "💪")
    check(r and "הפריע: הקצב מהיר מדי" in r[-1]["text"] and "מתי נוח לך לחזק" in r[-1]["text"] and "תוסיף חיזוק" not in r[-1]["text"],
          "חיזוק: סיבה ושאלה אחת, בלי הצעת שיבוץ")
    e2 = env("2026-11-07T20:00")
    run_until(e2, t("2026-11-07T21:10"), step=10)
    w = sent(e2, "📊")
    check(w and ("למדת" in w[-1]["text"] or "לא נרשם לימוד" in w[-1]["text"]) and "בוצעו" not in w[-1]["text"], "סיכום שבוע לפי לימוד בפועל, לא מול יעד")
    print("== בוקר: המלצה אחת ==")
    b = bot_of(e2)
    b.load_state()
    b.st["recaps"].append({"date": "2026-11-05", "course": "econ", "rating": 2, "topics": "", "unclear": "ביקוש", "at": "x"})
    b.save_state()
    run_until(e2, t("2026-11-08T08:00"), step=10)
    m = sent(e2, "☀️")
    check(m and "💪 כדאי לחזק היום" in m[-1]["text"], "הודעת בוקר כוללת המלצה")
    print("== מטלה: בלי הצעת שיבוץ ==")
    b = bot_of(e2)
    b.load_state()
    b.apply_action({"action": "assignment", "course": "econ", "title": "תרגיל 3", "due": "2026-11-12", "est_min": 120})
    check(not b.st["bot"].get("xp"), "מטלה גדולה לא פותחת הצעת שיבוץ אוטומטית")
    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
