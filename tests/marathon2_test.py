"""
מרתון v2 (שלב H): שעות לפי מפת החומר, הקצאה לפי קרבת מבחן, חוסר גלוי, הצעת יום עם כפתורים, מוכנות ודף חזרה.
הרצה: python3 -m tests.marathon2_test
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot import marathon2 as M2  # noqa: E402
from bot import topics as TOP  # noqa: E402
from tests.conv_test import env, press  # noqa: E402
from tests.features_test import bot_of, st  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def fresh(active=True):
    e = env()
    b = bot_of(e)
    b.load_state()
    if active:
        b.mar_set_start(b.now.date().isoformat())       # מרתון פעיל מהיום (במקום 28.12)
    return e, b


def main():
    print("== נדרש לפי מפת החומר ==")
    s = {}
    t = TOP.ensure(s, "math")[0]
    check(M2.topic_factor(t) == 1.0, "נושא שלא נלמד: 1.0")
    TOP.mark_learned(s, "math", "2026-11-02", [0])
    check(M2.topic_factor(t) == 0.6, "נלמד: 0.6")
    TOP.mark_reviewed(s, "math", "2026-11-03", [0], est=True)
    check(M2.topic_factor(t) == 0.45, "נחזר בהערכה: 0.45")
    TOP.mark_reviewed(s, "math", "2026-11-04", [0], est=False)
    check(M2.topic_factor(t) == 0.35, "חזרה אמיתית: 0.35")
    TOP.mark_reviewed(s, "math", "2026-11-10", [0], est=False)
    check(M2.topic_factor(t) == 0.1, "שתי חזרות: 0.1")
    t.update(rating=2, rated_at="2026-11-11", reviewed=[])
    check(abs(M2.topic_factor(t) - 0.9) < 1e-9, "נושא חלש מעלה ב-0.3")
    check(M2.hours(150) == "2:30 ש׳" and M2.hours(60) == "1 ש׳" and M2.hours(20) == "20 דק׳", "תצוגת שעות")
    e, b = fresh()
    base = b.m2_baseline("math")
    check(base == b.mar_need("math") * 50 and b.m2_remaining("math") == base, "בלי מידע: הבסיס")
    TOP.ensure(b.st, "math")
    for i in range(len(TOP.ensure(b.st, "math"))):
        TOP.mark_learned(b.st, "math", "2026-10-30", [i])
    check(abs(b.m2_remaining("math") - base * 0.6) < 1, "הכול נלמד ולא נחזר: 60% מהבסיס")
    b.mar()["scores"]["math"] = [{"d": "2026-11-01", "s": 92}]
    check(abs(b.m2_remaining("math") - base * 0.15) < 1 and b.m2_readiness("math") == 85, "שליטה: 15% נשאר")
    b.mar()["scores"].clear()
    print("== הקצאה, חוסר, ימים חסומים ==")
    plan = b.m2_plan()
    check(plan["exams"] and plan["days"], "יש תוכנית")
    first_exam = plan["exams"][0][0]
    d0 = min(plan["days"])
    row = plan["days"][d0]
    check(list(row)[0] == first_exam, "המבחן הקרוב ראשון בהקצאה")
    for d, r in plan["days"].items():
        win = sum((y - x).total_seconds() / 60 for x, y in plan["windows"][d])
        check(sum(r.values()) <= win + 1, f"{d}: לא מקצה יותר מהפנוי") if d == d0 else None
    b.mar()["need_min"] = {c: 60 * 400 for c, _ in plan["exams"]}
    big = b.m2_plan()
    check(big["left"] and all(n > 0 for n in big["left"].values()), "דרישה ענקית: חוסר גלוי")
    check("חסר זמן" in b.m2_text(), "והטקסט אומר זאת")
    b.mar()["need_min"] = {c: 60 for c, _ in plan["exams"]}
    check(not b.m2_plan()["left"] and "הכול נכנס" in b.m2_text(), "דרישה קטנה: אין חוסר")
    b.mar()["need_min"] = {}
    blocked = d0.isoformat()
    b.mar_block(blocked, None, "מילואים")
    check(d0 not in b.m2_plan()["days"], "יום חסום: בלי הצעה")
    check("חסר" in b.mar_block((d0 + timedelta(days=1)).isoformat(), (d0 + timedelta(days=60)).isoformat(), "מילואים") or True, "חסימה ארוכה לא קורסת")
    b.mar_unblock(blocked, (d0 + timedelta(days=60)).isoformat())
    print("== הצעת היום וכפתורים ==")
    today = b.now.date()
    lines, rows = b.m2_today(today)
    check(lines and lines[0].startswith("🏃 מרתון"), "שורת הצעה להיום")
    sa = [r for r in rows if r[0][1].startswith("sa|") and not r[0][1].endswith("|no")]
    if sa:
        check(len(sa[0][0][1].split("|")) == 5, "כפתור חלון בפורמט sa")
    else:
        print("  SKIP אין חלון פנוי היום בסביבת הבדיקה")
    print("== פקודות והקשר ==")
    check("מרתון" in b.marathon_text() and "נותר" in b.marathon_text(), "marathon_text בשעות")
    check(b.mar_set_hours("law", 7).count("7") >= 1 and b.mar()["need_min"]["law"] == 420, "תיקון דרישה בשעות")
    b.apply_action({"action": "marathon_need", "course": "stats", "hours": 9})
    check(b.mar()["need_min"]["stats"] == 540, "הפעולה marathon_need עם hours")
    import json
    ctx = json.loads(b.context_for_llm())["marathon"]
    check("hours_remaining" in ctx and "readiness_pct" in ctx, "הקשר ל-Claude כולל שעות ומוכנות")
    print("== דף חזרה ==")
    TOP.mark_learned(b.st, "math", "2026-10-30", [0], rating=2, unclear="כלל השרשרת")
    b.st["recaps"].append({"date": "2026-10-30", "course": "math", "rating": 2, "topics": "x", "unclear": "כלל השרשרת", "tids": [0], "at": "x"})
    data = b.review_sheet_data()
    mc = next(c for c in data["courses"] if c["name"] == "מתמטיקה")
    check(mc["unclear"] and mc["unclear"][0]["text"] == "כלל השרשרת" and mc["topics"][0]["state"] == "weak", "נקודות לא ברורות ונושאים לפי מצב")
    n = len(getattr(e["tg"], "docs", []))
    check(b.send_review_sheet() == "" and len(e["tg"].docs) == n + 1 and e["tg"].docs[-1]["data"][:4] == b"%PDF", "דף חזרה נשלח כ-PDF")
    import bot.report_pdf as mod
    orig = mod.build_review_pdf
    mod.build_review_pdf = lambda d: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        check("📝 דף חזרה" in b.send_review_sheet() and "כלל השרשרת" in b.send_review_sheet(), "כשהיצירה נכשלת: טקסט")
    finally:
        mod.build_review_pdf = orig
    print("== דוח שבועי ==")
    uni, _ = b.load_uni()
    ws = P.week_start(today)
    rep, lines = b.week_summary(ws, today, uni)
    wd = b.weekly_pdf_data(ws, today, uni, rep, lines[0])
    check(wd["marathon"] and all({"readiness", "remaining", "days"} <= set(x) for x in wd["marathon"]), "מוכנות למבחנים בדוח השבועי")
    from bot.report_pdf import build_html
    check("מוכנות למבחנים" in build_html(wd), "מופיעה ב-PDF")
    print("== תיקוני סקירה ==")
    e2, b2 = fresh()
    d = b2.now.date() + timedelta(days=3)
    uni2, _ = b2.load_uni()
    pers2 = b2.load_personal(P.week_start(d))
    from bot import suggest as SUG
    ch = SUG.free_chunks(d, uni2, pers2, b2.st, b2.now, cap_min=350, max_n=6)
    tot = sum((y - x).total_seconds() / 60 for x, y in ch)
    check(tot <= 350 and all((y - x).total_seconds() / 60 <= 120 for x, y in ch), "free_chunks: חתיכות עד שעתיים ותקרה יומית")
    check(all(ch[i][1] < ch[i + 1][0] for i in range(len(ch) - 1)), "free_chunks: חלונות לא חופפים, עם הפסקה")
    m0 = b2.m2_left("math")
    b2.st.setdefault("sessions", []).append({"course": "math", "status": "planned", "start": (b2.now + timedelta(days=1)).isoformat(),
                                             "end": (b2.now + timedelta(days=1, minutes=100)).isoformat()})
    check(abs(b2.m2_left("math") - (m0 - 100)) < 1, "סשן שנקבע מופחת מהנותר")
    b2.st.setdefault("log", []).append({"course": "math", "min": 50, "date": b2.mar()["start"]})
    check(abs(b2.m2_left("math") - (m0 - 100 - 30)) < 1, "לימוד שנרשם מופחת (60%)")
    b2._personal_error = "x"
    check("לא הצלחתי לקרוא את היומן האישי" in b2.m2_text(), "שגיאת יומן אישי מוצגת בטקסט")
    b2._personal_error = None
    b2.apply_action({"action": "marathon_need", "course": "law", "hours": 5})
    check(b2.mar()["need_min"].get("law") == 300, "marathon_need בשעות במצב שיחה")
    r = b2.apply_action({"action": "review_sheet", "course": "לא-קיים"})
    check(r and "לא זיהיתי" in str(r), "דף חזרה עם קורס לא מוכר: הודעה")
    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
