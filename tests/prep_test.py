"""
תזכורת לפני שיעור (ערב PDF + בוקר קצר), חזרה מרווחת, ונקודות לא ברורות פתוחות בדוח השבועי.
הרצה: python3 -m tests.prep_test
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot import spaced as SP  # noqa: E402
from bot import suggest as SUG  # noqa: E402
from bot import topics as TOP  # noqa: E402
from tests.conv_test import env  # noqa: E402
from tests.features_test import bot_of  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    print("== חזרה מרווחת ==")
    st = {}
    t0 = TOP.ensure(st, "math")[0]
    TOP.mark_learned(st, "math", "2026-11-02", [0], rating=4)
    check(SP.due(st, date(2026, 11, 3)) == [], "יום אחרי הלימוד: עוד לא")
    d = SP.due(st, date(2026, 11, 4))
    check(len(d) == 1 and d[0]["idx"] == 0 and d[0]["n"] == 0, "אחרי יומיים: מוצע")
    TOP.mark_reviewed(st, "math", "2026-11-04", [0], est=False)
    check(SP.due(st, date(2026, 11, 10)) == [] and len(SP.due(st, date(2026, 11, 11))) == 1, "החזרה הבאה אחרי 7 ימים")
    TOP.mark_reviewed(st, "math", "2026-11-11", [0], est=False)
    TOP.mark_reviewed(st, "math", "2026-12-02", [0], est=False)
    check(SP.due(st, date(2027, 3, 1)) == [], "אחרי שלוש חזרות: נסגר")
    TOP.ensure(st, "law")
    TOP.mark_learned(st, "law", "2026-10-20", [0], rating=2)
    TOP.mark_learned(st, "math", "2026-10-01", [1], rating=5)
    order = SP.due(st, date(2026, 11, 5), limit=5)
    check(order and order[0]["course"] == "law" and order[0]["weak"], "נושא חלש קודם")
    print("== תזכורת ==")
    e = env()
    b = bot_of(e)
    b.load_state()
    uni, _ = b.load_uni()
    tm = next(d for d in (b.now.date() + timedelta(days=i) for i in range(1, 8)) if any(l.day == d for l in uni.lessons))
    cs = sorted({l.course for l in uni.lessons if l.day == tm}, key=P.COURSE_ORDER.index)
    c = cs[0]
    pre = next(i for i in b.prep_entries(tm, uni) if i["course"] == c)
    check(pre["last"] is None and pre["prev_status"] in ("no_recap", None), "בלי ריקאפ: אין שורה קודמת")
    b.add_recap((tm - timedelta(days=7)).isoformat(), c, rating=2, topics="נגזרות", unclear="כלל השרשרת", cause="הקצב מהיר מדי")
    en = b.prep_entries(tm, uni)
    x = next(i for i in en if i["course"] == c)
    check(x["last"]["rating"] == 2 and x["last"]["unclear"] == "כלל השרשרת", "הריקאפ האחרון נשלף")
    check("כלל השרשרת" in x["question"] and "תסביר לי" in x["question"], "שאלה מוכנה לצ'אט")
    check("תשובה" not in x["question"], "הבוט לא עונה על השאלה")
    short = "\n".join(b.prep_lines(en, tm, full=False))
    full = "\n".join(b.prep_lines(en, tm, full=True))
    check("הבנה 2/5" in short and "💬" not in short and "💬 שאלה לצ'אט" in full and "הפריע: הקצב מהיר מדי" in full, "קצר בבוקר, מלא בערב")
    today = tm - timedelta(days=1)
    n0, d0 = len(e["tg"].sent), len(getattr(e["tg"], "docs", []))
    b.send_prep_evening(today, uni)
    docs = getattr(e["tg"], "docs", [])
    check(len(docs) == d0 + 1 and docs[-1]["data"][:4] == b"%PDF", "בערב: PDF נשלח")
    check(any("הבנה 2/5" in m["text"] for m in e["tg"].sent[n0:]), "והודעה קצרה לידו")
    import bot.report_pdf as mod
    orig = mod.build_prep_pdf
    mod.build_prep_pdf = lambda data: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        n1 = len(e["tg"].sent)
        b.send_prep_evening(today, uni)
        check(any("💬 שאלה לצ'אט" in m["text"] for m in e["tg"].sent[n1:]), "כשה-PDF נכשל: הגרסה המלאה בטקסט")
    finally:
        mod.build_prep_pdf = orig
    ml = b.prep_morning_lines(tm, uni)
    check(ml and ml[0].startswith("📚") and all("💬" not in z for z in ml), "בוקר: שורות קצרות")
    n2 = len(e["tg"].sent)
    b.send_prep_evening(tm + timedelta(days=30), uni)
    check(len(e["tg"].sent) == n2, "מחר בלי שיעורים: לא נשלח כלום")
    print("== שליחה פעם אחת, רק בערב ==")
    e5 = env()
    b5 = bot_of(e5)
    b5.load_state()
    uni5, _ = b5.load_uni()
    tm5 = next(d for d in (b5.now.date() + timedelta(days=i) for i in range(1, 8)) if any(l.day == d for l in uni5.lessons))
    c5 = next(l.course for l in uni5.lessons if l.day == tm5)
    b5.add_recap((tm5 - timedelta(days=7)).isoformat(), c5, rating=3, topics="x")
    day5 = tm5 - timedelta(days=1)
    from datetime import datetime
    b5.now = datetime.combine(day5, __import__("datetime").time(15, 0), tzinfo=b5.now.tzinfo)
    d_before = len(e5["tg"].docs) if hasattr(e5["tg"], "docs") else 0
    b5.maybe_prep_evening(day5, uni5)
    check((len(getattr(e5["tg"], "docs", [])) == d_before), "בקשת סיכום באמצע היום: לא נשלח")
    b5.now = datetime.combine(day5, __import__("datetime").time(21, 30), tzinfo=b5.now.tzinfo)
    b5.maybe_prep_evening(day5, uni5)
    b5.maybe_prep_evening(day5, uni5)
    check(len(getattr(e5["tg"], "docs", [])) == d_before + 1, "בערב: פעם אחת בלבד גם בשתי קריאות")
    print("== השיעור הקודם בפועל ==")
    lessons_c = sorted({l.day for l in uni5.lessons if l.course == c5 and l.day < tm5})
    if lessons_c:
        pv = lessons_c[-1]
        b5.st["bot"]["attend"][f"{c5}|{pv.isoformat()}"] = "absent"
        x5 = next(i for i in b5.prep_entries(tm5, uni5) if i["course"] == c5)
        check(x5["last"] is None and x5["prev_status"] == "missed", "היעדרות בשיעור הקודם: לא מציגים ריקאפ ישן")
        check("לא היית" in "\n".join(b5.prep_lines([x5], tm5, full=False)), "והטקסט אומר שלא היית")
        b5.st["bot"]["attend"].pop(f"{c5}|{pv.isoformat()}")
        x6 = next(i for i in b5.prep_entries(tm5, uni5) if i["course"] == c5)
        check(x6["prev_status"] == "no_recap" or x6["last"] is not None, "שיעור קודם בלי ריקאפ: אין ריקאפ")
    print("== חזרה מרווחת: מקרי קצה ==")
    s7 = {}
    TOP.ensure(s7, "math")
    TOP.mark_learned(s7, "math", "2026-11-02", [0], rating=4)
    TOP.mark_reviewed(s7, "math", "2026-11-04", [0], est=True)
    TOP.mark_reviewed(s7, "math", "2026-11-04", [0], est=False)
    check(len(SP.due(s7, date(2026, 11, 11))) == 1, "הערכה ודיווח באותו יום נספרים חזרה אחת")
    TOP.mark_learned(s7, "math", "2026-11-20", [0], rating=2)
    check(len(SP.due(s7, date(2026, 11, 23))) == 1, "נושא שנלמד מחדש חוזר למרווחים")
    print("== המלצה: חזרה מרווחת לבדה ==")
    s8 = {}
    TOP.ensure(s8, "stats")
    TOP.mark_learned(s8, "stats", "2026-10-25", [0], rating=5)
    r8 = SUG.recommend(s8, date(2026, 11, 3), uni5, b5.now, limit=6)
    check(all(x["score"] <= 1 or "חזרה מרווחת" not in " ".join(x["why"]) or len(x["why"]) > 1 for x in r8), "ציון חזרה מרווחת לבדה לא מנופח")
    print("== המלצה ודוח שבועי ==")
    TOP.ensure(b.st, "math")
    TOP.mark_learned(b.st, "math", (b.now.date() - timedelta(days=5)).isoformat(), [0], rating=5)
    recs = SUG.recommend(b.st, b.now.date(), uni, b.now, limit=6)
    check(any(r["course"] == "math" and any(w.startswith("חזרה מרווחת") for w in r["why"]) for r in recs), "חזרה מרווחת בהמלצות")
    ws = P.week_start(b.now.date())
    rep, lines = b.week_summary(ws, b.now.date(), uni)
    data = b.weekly_pdf_data(ws, b.now.date(), uni, rep, lines[0])
    check(any(u["unclear"] == "כלל השרשרת" for u in data["open_unclear"]), "נקודות לא ברורות פתוחות בדוח")
    from bot.report_pdf import build_html
    check("נקודות לא ברורות פתוחות" in build_html(data), "מופיעות ב-PDF")
    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
