"""
ריקאפ כשיחה (שלב E): נוכחות, שאלות לפי דירוג, דילוג, טקסט חופשי, "מתי נוח לך ללמוד היום?", קביעה ביומן.
מצב freeze (החי). יום שני 2.11: משפט, מתמטיקה, סטטיסטיקה. הרצה: python3 -m tests.conv_test
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.features_test import REV, bot_of, make_env, run_until, st, t, tick  # noqa: E402

FAILS: list[str] = []


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def env(mode="freeze"):
    e = make_env("2026-11-02T14:00")
    b = bot_of(e)
    b.load_state()
    b.st["bot"]["mode"] = mode
    b.save_state()
    run_until(e, t("2026-11-02T15:45"), step=5)
    return e


def msgs(e, prefix):
    return [m for m in e["tg"].sent if m["text"].startswith(prefix)]


def press(e, mid, data):
    e["tg"].press(mid, data)
    tick(e)


def btn(e, m, pred):
    return e["tg"].find_button(m["id"], pred)


def recap_msg(e):
    r = msgs(e, "🚆 ריקאפ")
    return r[-1] if r else None


def main():
    e = env()
    tg = e["tg"]
    rc = recap_msg(e)
    check(rc is not None, "ריקאפ נשלח אחרי השיעור האחרון")
    ds = "2026-11-02"
    print("== נוכחות ודירוג ==")
    # נוכחות: משפט ומתמטיקה הייתי, סטטיסטיקה לא
    for c, v in (("law", "present"), ("math", "present"), ("stats", "absent")):
        press(e, rc["id"], f"att|{c}|{ds}|{v}")
    check(not msgs(e, "מתי יהיה לך נוח"), "עדיין לא שואלים על זמן: חסר דירוג")
    n0 = len(tg.sent)
    press(e, rc["id"], f"rate|math|{ds}|5")
    q = [m for m in tg.sent[n0:] if "מה עברתם" in m["text"]]
    check(len(q) == 1, "דירוג גבוה: שאלה אחת, מה עברתם")
    check(len([m for m in tg.sent[n0:] if m["buttons"] and any(d.startswith("rqa|") for r in m["buttons"] for _, d in r)]) == 1,
          "שאלה אחת בלבד בהודעה אחת בכל פעם")
    check(q and btn(e, q[0], lambda tx, d: d.endswith("|skip")) is not None, "דלג זמין")
    n1 = len(tg.sent)
    press(e, rc["id"], f"rate|law|{ds}|2")
    check(not any("מה לא הבנת" in m["text"] for m in tg.sent[n1:]), "שאלה שנייה ממתינה עד שהראשונה נענתה")
    press(e, q[0]["id"], f"rqa|{ds}|math|skip")
    tq = [m for m in tg.sent[n1:] if "מה עברתם" in m["text"] and "משפט" in m["text"]]
    check(len(tq) == 1, "אחרי דילוג: בדירוג נמוך בקורס בלי נושא שבועי קודם שואלים מה עברתם")
    press(e, tq[0]["id"], btn(e, tq[0], lambda tx, d: d.endswith("|t0")))
    lq = [m for m in tg.sent[n1:] if "מה לא הבנת" in m["text"]]
    check(len(lq) == 1 and "משפט" in lq[0]["text"], "ואז שאלת ההבהרה לדירוג נמוך")
    check(not any(r.get("topics") for r in st(e)["recaps"] if r["course"] == "math"), "דילוג לא שומר כלום")
    print("== דירוג נמוך: שתי שאלות, בלי הצעת חיזוק אוטומטית ==")
    n2 = len(tg.sent)
    press(e, lq[0]["id"], f"rqa|{ds}|law|u2")
    cq = [m for m in tg.sent[n2:] if "מה הכי הפריע" in m["text"]]
    check(len(cq) == 1, "אחרי מה לא הבנת: מה הכי הפריע")
    n3 = len(tg.sent)
    press(e, cq[0]["id"], f"rqa|{ds}|law|k1")
    rec = [r for r in st(e)["recaps"] if r["course"] == "law"][0]
    check(rec["unclear"] == "תרגיל או חישוב" and rec.get("cause") == "הקצב מהיר מדי", "התשובות נשמרו בריקאפ")
    check(not msgs(e, "💡") and not st(e)["bot"].get("xp"), "אין הצעת שיבוץ אוטומטית לחיזוק")
    fin = [m for m in tg.sent[n3:] if m["text"].startswith("מתי יהיה לך נוח ללמוד היום")]
    check(len(fin) == 1, "כשהכול נסגר: מתי יהיה לך נוח ללמוד היום, פעם אחת")
    check(fin and "מומלץ לחזק" in fin[0]["text"] and "משפט" in fin[0]["text"], "המלצה על הקורס החלש")
    check(fin and fin[0]["text"].count("?") == 1, "שאלה אחת בהודעה")
    check(not st(e)["sessions"] or all(s.get("src") != "button" for s in st(e)["sessions"]), "לא נקבע דבר בלי בחירה")
    print("== בחירה בחלון: נקבע ביומן ==")
    check(btn(e, fin[0], lambda tx, d: "דלג" in tx and d.endswith("|no")) is not None, "בשאלת הזמן יש כפתור דלג")
    sa = btn(e, fin[0], lambda tx, d: d.startswith("sa|") and not d.endswith("|no"))
    check(sa is not None, "יש כפתור חלון פנוי")
    if sa:
        before = len(st(e)["sessions"])
        press(e, fin[0]["id"], sa)
        s = st(e)["sessions"]
        check(len(s) == before + 1 and s[-1].get("src") == "button" and s[-1].get("min"), "נרשם סשן עם אורך ומקור")
        check(any(x["text"].startswith("✅ קבעתי") for x in tg.sent), "דווח על הקביעה")
        cal = e["cal"].cals.get(REV, {})
        evs = list(cal.values()) if isinstance(cal, dict) else list(cal)
        check(any("מתמטיקה" in (x.get("summary") or "") or "משפט" in (x.get("summary") or "") for x in evs), "אירוע נוצר ביומן החזרות")
        press(e, fin[0]["id"], sa)
        check(len(st(e)["sessions"]) == before + 1, "לחיצה חוזרת לא יוצרת עוד סשן")
    print("== טקסט חופשי ==")
    e2 = env()
    tg2 = e2["tg"]
    rc2 = recap_msg(e2)
    for c in ("law", "math", "stats"):
        press(e2, rc2["id"], f"att|{c}|{ds}|present")
    press(e2, rc2["id"], f"rate|math|{ds}|4")
    q2 = [m for m in tg2.sent if "מה עברתם" in m["text"]][-1]
    e2["llm"].script.append({"reply": "רשמתי", "actions": [{"action": "recap", "course": "math", "date": ds, "topics": "נגזרות"}]})
    tg2.user_text("עברנו נגזרות")
    n = len(tg2.sent)
    tick(e2)
    check([r for r in st(e2)["recaps"] if r["course"] == "math"][0]["topics"] == "נגזרות", "תשובה חופשית נשמרה כנושא")
    check(st(e2)["bot"]["rq"]["cur"] is None, "השאלה נסגרה אחרי התשובה")
    b2 = bot_of(e2)
    b2.load_state()
    check(json.loads(b2.context_for_llm())["recap_question"] is None, "אחרי תשובה אין recap_question בהקשר")
    b2.st["bot"]["rq"]["cur"] = {"c": "stats", "k": "unclear", "mid": 1, "text": "סטטיסטיקה: מה לא הבנת?", "sug": "", "at": b2.now.isoformat()}
    rq = json.loads(b2.context_for_llm())["recap_question"]
    check(rq and rq["course"] == "stats" and rq["kind"] == "unclear" and rq["date"] == ds, "שאלה פתוחה מגיעה ל-Claude כהקשר")
    b2.st["bot"]["rq"]["cur"] = None
    print("== קביעה והקלטה בטקסט ==")
    e2["llm"].script.append({"reply": "קבעתי", "actions": [{"action": "study_at", "course": "stats", "start": "2026-11-02T20:00", "minutes": 30}]})
    tg2.user_text("אני לומד סטטיסטיקה ב-20:00 חצי שעה")
    tick(e2)
    s = [x for x in st(e2)["sessions"] if x.get("src") == "text"]
    check(s and s[-1]["min"] == 30 and s[-1]["start"][11:16] == "20:00", "study_at: סשן של 30 דקות ב-20:00")
    e2["llm"].script.append({"reply": "רשמתי", "actions": [{"action": "study_at", "course": "stats", "start": "2026-11-02T20:10", "minutes": 30}]})
    tg2.user_text("ועוד אחד ב-20:10")
    tick(e2)
    check(len([x for x in st(e2)["sessions"] if x.get("src") == "text"]) == 1, "חפיפה עם סשן קיים נדחית")
    e2["llm"].script.append({"reply": "רשמתי", "actions": [{"action": "study_at", "course": "stats", "start": "2026-11-02T22:30", "minutes": 60}]})
    tg2.user_text("22:30 שעה")
    tick(e2)
    check(len([x for x in st(e2)["sessions"] if x.get("src") == "text"]) == 1, "אחרי 23:00 נדחה")
    e2["llm"].script.append({"reply": "רשמתי", "actions": [{"action": "studied", "course": "intro", "date": ds, "minutes": 40, "topic": "מלאי"}]})
    tg2.user_text("למדתי היום מבוא 40 דקות")
    tick(e2)
    check(any(r["course"] == "intro" and r["min"] == 40 and not r["est"] for r in st(e2)["log"]), "studied נרשם ביומן הלימוד")
    print("== סימון בוצע נכנס ליומן לימוד, ו-21:30 סוגר שאלות ==")
    b = bot_of(e2)
    b.load_state()
    sess = [x for x in b.st["sessions"] if x.get("src") == "text"][0]
    b.mark(sess, "done")
    b.mark(sess, "done")
    check(len([r for r in b.st["log"] if r.get("session") == sess["key"]]) == 1, "סימון בוצע נרשם פעם אחת")
    e3 = env()
    rc3 = recap_msg(e3)
    press(e3, rc3["id"], f"att|math|{ds}|present")
    press(e3, rc3["id"], f"rate|math|{ds}|3")
    run_until(e3, t("2026-11-02T21:35"), step=5)
    check(st(e3)["bot"]["rq"]["closed"], "ב-21:30 שאלות שלא נענו נסגרות")
    check(not msgs(e3, "מתי יהיה לך נוח"), "ושאלת הזמן לא נשלחת בדיעבד")
    print("== rateall: בלי שאלות, ישר לשאלת הזמן ==")
    e4 = env()
    rc4 = recap_msg(e4)
    press(e4, rc4["id"], f"attall|{ds}")
    n = len(e4["tg"].sent)
    press(e4, rc4["id"], f"rateall|{ds}")
    check(not any("מה עברתם" in m["text"] for m in e4["tg"].sent[n:]), "הכול ברור: בלי שאלות מה עברתם")
    check(any(m["text"].startswith("מתי יהיה לך נוח") for m in e4["tg"].sent[n:]), "ישר לשאלת הזמן")
    print("== v1 לא משתנה ==")
    e5 = env("v1")
    rc5 = recap_msg(e5)
    press(e5, rc5["id"], f"att|math|{ds}|present")
    n = len(e5["tg"].sent)
    press(e5, rc5["id"], f"rate|math|{ds}|5")
    check(not any(m["text"].startswith(("מתמטיקה: מה", "מתי יהיה")) for m in e5["tg"].sent[n:]), "v1: אין שיחה")
    print("== יום מילואים והיעדרות: השאלה נשלחת ==")
    e7 = env()
    rc7 = recap_msg(e7)
    for c, v in (("law", "reserve"), ("math", "absent"), ("stats", "reserve")):
        press(e7, rc7["id"], f"att|{c}|{ds}|{v}")
    fin7 = [m for m in e7["tg"].sent if m["text"].startswith("מתי יהיה לך נוח")]
    check(len(fin7) == 1 and btn(e7, fin7[0], lambda tx, d: "דלג" in tx) is not None, "גם ביום מילואים והיעדרות: שאלה עם דלג")
    press(e7, fin7[0]["id"], btn(e7, fin7[0], lambda tx, d: "דלג" in tx))
    check(st(e7)["bot"]["rq"]["closed"] and all(s.get("src") != "button" for s in st(e7)["sessions"]), "דלג סוגר בלי לקבוע")
    print("== תיקונים מסקירת Opus ==")
    e6 = env()
    tg6, ds = e6["tg"], "2026-11-02"
    rc6 = recap_msg(e6)
    press(e6, rc6["id"], f"att|law|{ds}|present")
    press(e6, rc6["id"], f"att|math|{ds}|present")
    press(e6, rc6["id"], f"att|stats|{ds}|absent")
    press(e6, rc6["id"], f"rate|law|{ds}|2")
    e6["llm"].script.append({"reply": "דילגתי", "actions": [{"action": "recap_skip", "course": "law", "date": ds}]})
    tg6.user_text("דלג")
    tick(e6)
    check(st(e6)["bot"]["rq"]["cur"] is None and not st(e6)["bot"]["rq"]["queue"], "דילוג בטקסט סוגר את שאלות הקורס")
    e6["llm"].script.append({"reply": "רשמתי", "actions": [{"action": "recap", "course": "math", "date": ds, "rating": 4}]})
    tg6.user_text("מתמטיקה 4")
    tick(e6)
    check(any("מה עברתם" in m["text"] for m in tg6.sent), "דירוג בטקסט מפעיל את שאלת מה עברתם")
    n = len(tg6.sent)
    e6["llm"].script.append({"reply": "רשמתי", "actions": [{"action": "recap", "course": "math", "date": ds, "unclear": "x"}]})
    tg6.user_text("עברנו נגזרות")
    tick(e6)
    check(any(m["text"].startswith("מתי יהיה לך נוח") for m in tg6.sent[n:]), "תשובה בשדה אחר עדיין מקדמת לשאלת הזמן")
    nn = len([m for m in tg6.sent if m["text"].startswith("מתי יהיה לך נוח")])
    b = bot_of(e6)
    b.load_state()
    b.now = b.now.replace(hour=17, minute=0)
    b.job_daily(b.now.date())
    check(not b.st["bot"]["rq"]["closed"], "סיכום באמצע היום לא סוגר את השיחה")
    b.rq_open(ds, ["law", "math", "stats"])
    b.rq_check_final(ds)
    check(len([m for m in tg6.sent if m["text"].startswith("מתי יהיה לך נוח")]) == nn, "ריקאפ חוזר באותו יום לא שואל שוב")
    b.rq_close("2026-10-01")
    check(not b.st["bot"]["rq"]["closed"], "סגירה של יום אחר לא נוגעת בשיחה של היום")
    for start in ("2026-11-03", "2026-11-04T00:30", "2026-11-03T05:00"):
        r = b.study_at_action({"course": "stats", "start": start, "minutes": 30})
        check(not r.startswith("✅"), f"זמן לא סביר נדחה: {start}")
    check(not b.study_at_action({"course": "stats", "start": "2026-11-03T17:00", "minutes": "חצי שעה"}).startswith("✅"), "דקות לא מספריות: הודעה בעברית")
    k1 = b.study_at_action({"course": "stats", "start": "2026-11-03T17:00", "minutes": 30})
    k2 = b.study_at_action({"course": "stats", "start": "2026-11-03T17:00", "minutes": 30})
    check(k1.startswith("✅ קבעתי") and k2.startswith("✅ כבר נקבע") and len([x for x in b.st["sessions"] if x.get("src") == "text"]) == 1, "קביעה כפולה זהה: שקט")
    sess = [x for x in b.st["sessions"] if x.get("src") == "text"][0]
    b.mark(sess, "done")
    b.mark(sess, "missed")
    check(not [r for r in b.st["log"] if r.get("session") == sess["key"]], "בוצע ואז לא בוצע מסיר את רישום הלימוד")
    xp0 = len(b.st["bot"].get("xp", {}))
    e6["llm"].script.append({"reply": "נרשם", "actions": [{"action": "attendance", "course": "stats", "date": ds, "status": "absent"}]})
    tg6.user_text("לא הייתי בסטטיסטיקה")
    tick(e6)
    check(len(st(e6)["bot"].get("xp", {})) == xp0, "היעדרות בטקסט לא פותחת הצעת השלמה אוטומטית")
    print("\n" + ("הכול עבר" if not FAILS else f"{len(FAILS)} כשלים"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
