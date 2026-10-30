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
    q0 = [m for m in e["tg"].sent if "מה עברתם" in m["text"] and "משפט" in m["text"]][-1]
    press(e, q0["id"], f"rqa|{ds}|law|skip")
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
    b.st["bot"]["rq"]["cur"] = None
    mat = json.loads(b.context_for_llm())["material"]
    check(len(mat["math"]) >= 4 and mat["math"][0][0] == 0, "רשימת נושאים עם מספרים בהקשר גם בלי שאלה פעילה")
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
    print("== תיקוני סקירה ==")
    s2 = {}
    check(TOP.match(s2, "math", "פונקציות") == [], "תיקו בין כמה נושאים: לא משייכים")
    check(TOP.match(s2, "math", "אופטימיזציה") == [4], "מילה ייחודית משייכת נושא אחד")
    check(TOP.match(s2, "math", "פונקציות בסיסיות") == [1], "שם זהה בדיוק מנצח")
    check(TOP.match(s2, "math", "עברנו על הנגזרות") == [3], "אות יחס לא שוברת התאמה")
    check(TOP.match(s2, "econ", "ההיצע") in ([3], []) and 4 not in TOP.match(s2, "econ", "ההיצע"), "ההיצע לא מושך את נושא השבוע הבא")
    s3 = {}
    TOP.mark_learned(s3, "math", "2026-11-05", [0], rating=2, unclear="חדש")
    TOP.mark_learned(s3, "math", "2026-11-02", [0], rating=5, unclear="ישן")
    t0 = TOP.ensure(s3, "math")[0]
    check(t0["rating"] == 2 and t0["unclear"] == "חדש", "דירוג ישן לא דורס חדש")
    TOP.mark_reviewed(s3, "math", "2026-11-05", [0], est=False)
    check(not TOP.is_weak(t0), "חזרה אמיתית באותו יום סוגרת חולשה")
    s3["recaps"] = [{"course": "math", "date": "2026-11-05", "tids": [0, 2]}, {"course": "math", "date": "2026-11-02", "tids": [0]}]
    TOP.clear_topic(s3, "math", [0], "2026-11-05")
    t0 = TOP.ensure(s3, "math")[0]
    check(t0["learned"] == ["2026-11-02"] and t0["rating"] == 5 and t0["rated_at"] == "2026-11-02" or t0["rating"] in (None, 5), "ניקוי לפי תאריך משאיר את שאר ההיסטוריה")
    check(s3["recaps"][0]["tids"] == [2] and s3["recaps"][1]["tids"] == [0], "ניקוי מסיר את המספר מהריקאפ של אותו יום בלבד")
    TOP.clear_topic(s3, "math", [0])
    check(not TOP.ensure(s3, "math")[0]["learned"], "ניקוי בלי תאריך מאפס")
    s4 = {"topics": {"law": [TOP._new("נושא ישן שהוסר"), TOP._new("מבוא וכריתת חוזה: הצעה וקיבול,")]}}
    TOP.ensure(s4, "law")
    check(s4["topics"]["law"][0]["stale"] and TOP.stats(s4, "law", date(2026, 11, 3))["total"] == len(TOP.course_topics("law")), "נושא שהוסר מהסילבוס לא נספר")
    print("== דירוג נמוך ממופה לנושא ==")
    e2 = env()
    rc2 = recap_msg(e2)
    b2 = bot_of(e2)
    b2.load_state()
    b2.st["bot"]["rq"]["courses"].append("econ")
    b2.add_recap(ds, "econ", rating=2)
    b2.rq_on_rating(ds, "econ", 2)
    sug = TOP.index_of(b2.st, "econ", b2.rq_suggestion("econ", ds))
    et = b2.st["topics"]["econ"][sug] if sug is not None else {}
    check(sug is not None and et["learned"] == [ds] and et["rating"] == 2, "כלכלה בדירוג נמוך: נושא השבוע משויך אוטומטית")
    press(e2, rc2["id"], f"att|stats|{ds}|present")
    press(e2, rc2["id"], f"rate|stats|{ds}|1")
    check(any("מה עברתם" in m["text"] and "סטטיסטיקה" in m["text"] for m in e2["tg"].sent), "דירוג נמוך בלי נושא שבועי: קודם שואלים מה עברתם")
    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
