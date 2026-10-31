"""
דוח שבועי ב-PDF (שלב K): נתונים מהמצב, בניית PDF עברי, שליחה בסיכום השבועי, וגיבוי לטקסט כשהיצירה נכשלת.
הרצה: python3 -m tests.report_test
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot import report_pdf as RP  # noqa: E402
from tests.conv_test import env  # noqa: E402
from tests.features_test import bot_of  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def weekly(e, b):
    b.load_state()
    uni, _ = b.load_uni()
    today = date(2026, 11, 7)
    from bot import topics as TOP
    TOP.ensure(b.st, "math")[0].update(learned=["2026-11-02"], rating=2, rated_at="2026-11-02", unclear="כלל השרשרת")
    b.st["log"].append({"date": "2026-11-03", "course": "math", "min": 90, "est": False, "topic": "", "src": "text", "at": "x"})
    return uni, today


def main():
    print("== HTML ==")
    d = {"title": "סיכום שבוע", "range": "1.11 – 7.11", "verdict": "למדת", "total_min": 90, "prev_total_min": 0, "recaps": (2, 3),
         "courses": [{"name": "מתמטיקה", "min": 90, "prev_min": 0, "learned": 1, "total": 8, "reviewed": 0,
                      "weak": [{"topic": "נגזרות", "rating": 2, "unclear": "שרשרת <b>"}], "pending": []}],
         "exams": [{"name": "מתמטיקה", "date": "2.1", "days": 60}], "next_week": ["כלכלה: ביקוש"], "gaps": [], "tips": []}
    h = RP.build_html(d)
    check("מתמטיקה" in h and "שרשרת &lt;b&gt;" in h, "עברית בדוח, והטקסט מוגן מ-HTML")
    check(RP.hm(150) == "2:30 ש׳" and RP.hm(45) == "45 דק׳" and RP.hm(120) == "2 ש׳", "תצוגת שעות")
    bar = RP.coverage_bar(3, 1, 10)
    check(bar.count("<i ") == 3 and "width:10.00%" in bar and "width:70.00%" in bar, "פס כיסוי: נחזר/נלמד/לא נלמד")
    check(RP.coverage_bar(0, 0, 0).startswith('<div class="bar">'), "פס בלי נושאים לא קורס")
    try:
        pdf = RP.build_pdf(d)
        check(pdf[:4] == b"%PDF" and len(pdf) > 5000, "PDF אמיתי נוצר")
    except ImportError:
        print("  SKIP WeasyPrint לא מותקן")
    print("== נתונים מהמצב ==")
    e = env()
    b = bot_of(e)
    uni, today = weekly(e, b)
    rep, lines = b.week_summary(P.week_start(today), today, uni)
    data = b.weekly_pdf_data(P.week_start(today), today, uni, rep, lines[0])
    m = next(c for c in data["courses"] if c["name"] == "מתמטיקה")
    check(m["min"] == 90 and m["learned"] == 1 and m["weak"] and m["weak"][0]["unclear"] == "כלל השרשרת", "דקות, כיסוי ונושא חלש בנתונים")
    check(data["total_min"] == 90 and data["exams"], "סך לימוד ומבחנים")
    print("== שליחה וגיבוי ==")
    n_docs = len(getattr(e["tg"], "docs", []))
    ok = b.send_weekly_pdf(P.week_start(today), today, uni, rep, lines[0])
    docs = getattr(e["tg"], "docs", [])
    check(ok and len(docs) == n_docs + 1 and docs[-1]["data"][:4] == b"%PDF", "ה-PDF נשלח כקובץ")
    import bot.report_pdf as mod
    orig = mod.build_pdf
    mod.build_pdf = lambda data: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        sent_before = len(e["tg"].sent)
        b.job_weekly(today)
        text = "\n".join(m["text"] for m in e["tg"].sent[sent_before:])
        check("הדוח המלא" not in text and "🗺️" in text, "כשה-PDF נכשל: הסיכום המלא בטקסט כמו קודם")
    finally:
        mod.build_pdf = orig
    sent_before, docs_before = len(e["tg"].sent), len(e["tg"].docs)
    b.job_weekly(today)
    text = "\n".join(m["text"] for m in e["tg"].sent[sent_before:])
    check(len(e["tg"].docs) == docs_before + 1 and "מצורף כ-PDF" in text, "בסיכום השבועי: PDF והודעה קצרה")
    check("🗺️ כיסוי חומר" not in text, "הודעה קצרה בלי הפירוט שעבר ל-PDF")
    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
