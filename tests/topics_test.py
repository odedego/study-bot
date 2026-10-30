"""
מפת חומר (יעד 1): זריעה מהסילבוס, שיוך מהריקאפ, חזרה בהערכה, נושא חלש, תיקון, פקודת "חומר", המלצות וסיכום שבועי.
הרצה: python3 -m tests.topics_test
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot import suggest as SUG  # noqa: E402
from bot import topics as TOP  # noqa: E402
from tests.conv_test import env, press, recap_msg  # noqa: E402
from tests.features_test import bot_of, st, t, tick  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    ds = "2026-11-02"
    print("== זריעה והתאמה ==")
    s = {}
    for c in P.COURSE_ORDER:
        check(len(TOP.ensure(s, c)) >= 4, f"{c}: נושאי סילבוס נזרעו")
    check(len(TOP.ensure(s, "econ")) == 13 and TOP.ensure(s, "econ")[0]["name"].startswith("מחסור"), "כלכלה לפי שבועות")
    m = TOP.match(s, "math", "עברנו היום נגזרות")
    check(m and "נגזרות" in TOP.ensure(s, "math")[m[0]]["name"], "התאמת מילים: נגזרות")
    check(TOP.match(s, "math", "היה כיף") == [], "טקסט בלי נושא: אין שיוך")
    check(TOP.valid_ids(s, "math", ["1", 99, "x", 1]) == [1], "מספרים לא חוקיים נזרקים")
    print("== זרימת ריקאפ ==")
    e = env()
    rc = recap_msg(e)
    press(e, rc["id"], f"att|math|{ds}|present")
    press(e, rc["id"], f"rate|math|{ds}|5")
    q = [m for m in e["tg"].sent if "מה עברתם" in m["text"] and "מתמטיקה" in m["text"]][-1]
    d = e["tg"].find_button(q["id"], lambda tx, dd: dd.endswith("|t0"))
    check(d is not None, "מוצעים הנושאים הבאים בסילבוס")
    press(e, q["id"], d)
    mt = st(e)["topics"]["math"][0]
    check(mt["learned"] == [ds] and mt["rating"] == 5, "נושא נלמד עם הדירוג")
    press(e, rc["id"], f"att|law|{ds}|present")
    press(e, rc["id"], f"rate|law|{ds}|2")
    q2 = [m for m in e["tg"].sent if "מה לא הבנת" in m["text"] and "משפט" in m["text"]][-1]
    e["llm"].script.append({"reply": "רשמתי", "actions": [{"action": "recap", "course": "law", "date": ds, "unclear": "הבדל בין הצעה לקיבול", "topic_ids": [0]}]})
    e["tg"].user_text("לא הבנתי הצעה וקיבול")
    tick(e)
    lt = st(e)["topics"]["law"][0]
    check(lt["learned"] == [ds] and lt["rating"] == 2 and "קיבול" in lt["unclear"], "נושא חלש עם מה שלא היה ברור")
    check(TOP.is_weak(lt), "נושא בהבנה 2 הוא חלש")
    print("== הקשר ל-Claude ==")
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["rq"]["cur"] = {"c": "intro", "k": "topics", "mid": 1, "text": "x: מה עברתם?", "sug": "", "at": b.now.isoformat()}
    rq = json.loads(b.context_for_llm())["recap_question"]
    check(rq and len(rq["topics"]) >= 4 and rq["topics"][0]["id"] == 0, "רשימת נושאים עם מספרים בהקשר")
    b.st["bot"]["rq"]["cur"] = None
    print("== חזרה ותיקון ==")
    b.studied_action({"course": "math", "date": "2026-11-03", "minutes": 30})
    mt = b.st["topics"]["math"][0]
    check(mt["reviewed"] and mt["reviewed"][0]["est"], "דיווח בלי נושא: חזרה בהערכה")
    row = b.st["log"][-1]
    check(row.get("tinf") == [0], "ההערכה נשמרת בשורת היומן")
    sess = b.book_study("math", t("2026-11-03T21:00"), 30, "text")
    check(sess.startswith("✅"), "קביעת סשן")
    b.studied_action({"course": "law", "date": "2026-11-03", "minutes": 20, "topic": "הצעה וקיבול"})
    lt = b.st["topics"]["law"][0]
    check(TOP.reviewed_real(lt) == ["2026-11-03"] and not TOP.is_weak(lt), "חזרה אמיתית אחרי דירוג נמוך סוגרת את החולשה")
    r = b.topic_set_action({"course": "math", "ids": [0], "op": "clear"})
    check(r.startswith("🗺️") and not b.st["topics"]["math"][0]["learned"], "תיקון: הנושא הוסר")
    check("לא הצלחתי" in b.topic_set_action({"course": "math", "ids": [99], "op": "clear"}), "תיקון עם מספר לא חוקי: הודעה")
    print("== תצוגה והמלצות ==")
    txt = TOP.material_text(b.st, date(2026, 11, 3))
    check("מפת החומר" in txt and "🟩" in txt and "⬜" in txt, "פקודת חומר: מפה עם מקרא")
    e["tg"].user_text("חומר")
    n = len(e["tg"].sent)
    tick(e)
    check(any(m["text"].startswith("📚 מפת החומר") for m in e["tg"].sent[n:]), "הפקודה חומר מגיבה")
    b.st["topics"]["law"][0].update(rating=2, rated_at="2026-11-03", reviewed=[])
    recs = SUG.recommend(b.st, date(2026, 11, 4), P.Uni([], [], {}, [], []), P.at(date(2026, 11, 4), P.hm("20:00")))
    check(any(x["course"] == "law" and any("נושא חלש" in w for w in x["why"]) for x in recs), "המלצה כוללת נושא חלש")
    old = TOP.ensure({}, "intro")
    check(TOP.pending_review({"name": "x", "learned": ["2026-10-01"], "reviewed": [], "rating": None, "rated_at": None, "unclear": ""}, date(2026, 11, 3)), "נושא שלא נחזר מעל 5 ימים ממתין")
    uni, _ = b.load_uni()
    b.st["topics"]["math"][0].update(learned=["2026-11-02"])
    _, lines = b.week_summary(P.week_start(date(2026, 11, 7)), date(2026, 11, 7), uni)
    check(any("כיסוי חומר" in x for x in lines) and any("מתמטיקה" in x and "נלמדו" in x for x in lines), "סיכום שבועי כולל כיסוי חומר")
    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
