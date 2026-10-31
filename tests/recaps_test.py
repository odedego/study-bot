"""
ריקאפים שמורים וחסרים: הקשר ל-Claude, פקודת "ריקאפים", כפתור השלמה לשיעור ישן.
הרצה: python3 -m tests.recaps_test
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot import suggest as SUG  # noqa: E402
from tests.conv_test import env, press, recap_msg  # noqa: E402
from tests.features_test import bot_of, st, tick  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    e = env()
    b = bot_of(e)
    b.load_state()
    uni, _ = b.load_uni()
    now = b.now
    print("== חסרים ==")
    miss = SUG.missing_recaps(b.st, now.date(), uni, now)
    check(len(miss) >= 2 and all(m["attend"] in (None, "present") for m in miss), "זוהו שיעורים בלי ריקאפ")
    first = miss[0]
    b.add_recap(first["date"], first["course"], rating=4, topics="נגזרות")
    after = SUG.missing_recaps(b.st, now.date(), uni, now)
    check(len(after) == len(miss) - 1 and (first["course"], first["date"]) not in {(m["course"], m["date"]) for m in after}, "ריקאפ שנשמר יוצא מהחסרים")
    second = after[0]
    b.st["bot"]["attend"][f"{second['course']}|{second['date']}"] = "absent"
    check(second not in SUG.missing_recaps(b.st, now.date(), uni, now), "היעדרות אינה ריקאפ חסר")
    print("== הקשר ל-Claude ==")
    ctx = json.loads(b.context_for_llm())
    check(ctx["missing_recaps"] and ctx["missing_recaps"][0].keys() >= {"course", "date"}, "missing_recaps בהקשר")
    check(any(r["course"] == first["course"] and r["date"] == first["date"] and r.get("topics") == "נגזרות" and r.get("rating") == 4
              for r in ctx["recaps"]), "תוכן הריקאפ השמור בהקשר")
    check(len(json.dumps(ctx["recaps"], ensure_ascii=False)) < 6000, "גודל סביר")
    print("== פקודה וכפתור השלמה ==")
    b.save_state()
    n = len(e["tg"].sent)
    e["tg"].user_text("ריקאפים")
    tick(e)
    ov = [m for m in e["tg"].sent[n:] if m["text"].startswith("📝 ריקאפים")]
    check(len(ov) == 1 and "חסרים" in ov[0]["text"] and "נשמרו" in ov[0]["text"], "סקירת ריקאפים: חסרים ושמורים")
    bt = e["tg"].find_button(ov[0]["id"], lambda tx, d: d.startswith("rcb|")) if ov else None
    check(bt is not None, "כפתור השלמה לכל שיעור חסר")
    if bt:
        _, c, ds = bt.split("|")
        n = len(e["tg"].sent)
        press(e, ov[0]["id"], bt)
        card = [m for m in e["tg"].sent[n:] if "ריקאפ לשיעור שעבר" in m["text"]]
        check(len(card) == 1, "נשלח כרטיס ריקאפ לשיעור הישן")
        press(e, card[0]["id"], f"att|{c}|{ds}|present")
        press(e, card[0]["id"], f"rate|{c}|{ds}|5")
        q = [m for m in e["tg"].sent[n:] if "מה עברתם" in m["text"]]
        check(len(q) == 1, "הדירוג מביא את שאלת מה עברתם על השיעור הישן")
        check(not any("מתי יהיה לך נוח" in m["text"] for m in e["tg"].sent[n:]), "אין שאלת 'מתי נוח ללמוד היום' על שיעור ישן")
        check(any(r["course"] == c and r["date"] == ds and r["rating"] == 5 for r in st(e)["recaps"]), "הדירוג נשמר על התאריך הישן")
    print("== ריקאפ פתוח של היום לא נדרס ==")
    e2 = env()
    b2 = bot_of(e2)
    b2.load_state()
    today = b2.now.date().isoformat()
    b2.st["bot"]["rq"] = {"date": today, "courses": ["math"], "queue": [], "cur": {"c": "math", "k": "topics", "mid": 1, "text": "x", "sug": "", "at": b2.now.isoformat()},
                          "asked": [], "final": False, "closed": False}
    old = [m for m in SUG.missing_recaps(b2.st, b2.now.date(), b2.load_uni()[0], b2.now) if m["date"] != today][0]
    n2 = len(e2["tg"].sent)
    b2.backfill_recap(old["course"], old["date"])
    check(b2.st["bot"]["rq"]["date"] == today and not b2.st["bot"]["rq"]["closed"], "שיחת היום נשארת פעילה")
    check(any("יש ריקאפ פתוח" in m["text"] for m in e2["tg"].sent[n2:]), "הבוט מסביר ומציע לכתוב חופשי")
    check(b2.st["bot"]["pending"]["date"] == old["date"], "טקסט חופשי ישויך לתאריך הישן")
    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
