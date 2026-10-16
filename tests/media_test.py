"""
תמונות ו-PDF: הזמנה לחתונה, הנחיות עם דדליין, קובץ לא נתמך, ניסיון "הזרקת הוראות" מתוך תמונה.
Claude מזויף כאן (תשובות JSON מוכנות). את ההבנה האמיתית של תמונות בודקים בנפרד.
הרצה: python3 -m tests.media_test
"""
from __future__ import annotations

import base64
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from tests.fakes import Clock, FakeCalendar, FakeFeed, FakeLLM, FakeTelegram, MemStore, freeze_planner  # noqa: E402
from bot.core import Bot, MEDIA_RULES  # noqa: E402

TZ = P.TZ
REV, PERS = "reviews@cal", "me@cal"
FAILS = []
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 5000


def check(c, m):
    print(("  PASS " if c else "  FAIL ") + m)
    if not c:
        FAILS.append(m)


def t(s):
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


class Cal403(FakeCalendar):
    deny = False

    def create(self, cal, body):
        if self.deny and cal == PERS:
            raise RuntimeError("calendar POST: 403 You need to have writer access to this calendar.")
        return super().create(cal, body)


def main():
    clock = Clock(t("2026-10-20T12:00"))
    freeze_planner(clock)
    tg, cal, feed, store, llm = FakeTelegram(), Cal403(), FakeFeed([]), MemStore(), FakeLLM()
    cal.add(REV, {"id": "r1", "summary": "📚 מתמטיקה", "description": "מזהה: review:math:x",
                  "start": {"dateTime": "2026-10-22T09:00:00+03:00"}, "end": {"dateTime": "2026-10-22T09:50:00+03:00"}})
    with_llm = {"llm": llm}

    def tick():
        b = Bot(tg, cal, feed, store, with_llm["llm"], {"REVIEWS_CAL": REV, "PERSONAL_CAL": PERS}, clock)
        b.now = clock()
        b.load_state()
        try:
            b.handle_updates()
        finally:
            b.save_state()
        return b

    def pps():
        return [m for m in tg.sent if m["text"].startswith("🗓️")]

    def press_ok(m):
        tg.press(m["id"], tg.find_button(m["id"], lambda tx, d: d.startswith("pp|ok")))
        return tick()

    rev_before = dict(cal.cals[REV])

    print("== הזמנה לחתונה (צילום מסך)")
    llm.script.append({"reply": "זיהיתי חתונה של נועה ואיתי ב-5.11 בגני התערוכה, קבלת פנים 19:30. שעת הסיום משוערת. שלחתי הצעה לאישור.",
                       "actions": [{"action": "personal", "op": "create", "title": "חתונה של נועה ואיתי",
                                    "start": "2026-11-05T19:30", "end": "2026-11-05T23:30",
                                    "location": "אולמי הגן, גני התערוכה תל אביב", "description": "חופה 20:30"}]})
    n = len(tg.sent)
    tg.user_media("photo", "big1", JPEG, caption="")
    tick()
    out = tg.sent[n:]
    check(len(llm.media) == 1 and llm.media[0]["mime"] == "image/jpeg"
          and base64.b64decode(llm.media[0]["data"]) == JPEG, "largest photo downloaded and sent to Claude as base64")
    check(MEDIA_RULES in llm.systems[-1], "image rules added to the system prompt")
    check(len(out) == 2 and out[0]["text"].startswith("זיהיתי חתונה") and out[1]["text"].startswith("🗓️"),
          "explanation first, then the proposal with buttons")
    pm = out[-1]
    check("📍 אולמי הגן" in pm["text"] and "19:30–23:30" in pm["text"] and "חופה 20:30" in pm["text"], "proposal shows place, time and details")
    check(not cal.cals.get(PERS), "nothing written before approval")
    press_ok(pm)
    ev = list(cal.cals.get(PERS, {}).values())
    check(len(ev) == 1 and ev[0]["location"].startswith("אולמי הגן") and ev[0]["start"]["dateTime"].startswith("2026-11-05T19:30")
          and ev[0]["end"]["dateTime"].startswith("2026-11-05T23:30"), "approved → event created with location and times")
    check("✅ בוצע" in tg.msg(pm["id"])["text"], "proposal message updated to done")

    print("== אותה הזמנה שוב")
    llm.script.append({"reply": "חתונה של נועה ואיתי ב-5.11.", "actions": [{"action": "personal", "op": "create", "title": "חתונה של נועה ואיתי",
                                                                          "start": "2026-11-05T19:30", "end": "2026-11-05T23:30"}]})
    k = len(pps())
    tg.user_media("photo", "big2", JPEG)
    tick()
    check(len(pps()) == k and "כבר יש ביומן" in tg.sent[-1]["text"], "duplicate invitation → no second proposal")

    print("== הנחיות למטלה עם דדליין (PDF)")
    llm.script.append({"reply": "מטלה 2 באקסל, הגשה עד 12.11. רשמתי ושלחתי הצעה ליומן.",
                       "actions": [{"action": "assignment", "course": "excel", "title": "מטלה 2: לוח שפיצר", "due": "2026-11-12", "est_min": 120},
                                   {"action": "personal", "op": "create", "all_day": True, "date": "2026-11-12", "title": "⏰ דדליין: אקסל מטלה 2"}]})
    tg.user_media("document", "pdf1", b"%PDF-1.4 test", caption="תכניס ליומן", mime="application/pdf")
    tick()
    st = store.load()
    check(any(a["title"].startswith("מטלה 2") and a["due"] == "2026-11-12" for a in st["assignments"]), "assignment recorded with due date")
    check(llm.media[-1]["mime"] == "application/pdf" and "תכניס ליומן" in llm.calls[-1], "PDF and caption sent to Claude")
    pm = pps()[-1]
    check("(כל היום)" in pm["text"] and "12.11" in pm["text"], "all-day deadline proposal")
    press_ok(pm)
    dl = [e for e in cal.cals[PERS].values() if "דדליין" in e["summary"]]
    check(dl and dl[0]["start"] == {"date": "2026-11-12"} and dl[0]["end"] == {"date": "2026-11-13"}, "all-day event created on the deadline date")

    print("== הוראות בתוך תמונה לא מבוצעות")
    st0 = store.load()
    pid_wedding = next(iter(cal.cals[PERS]))
    llm.script.append({"reply": "?", "actions": [{"action": "personal", "op": "delete", "event": "p" + "x"},
                                                 {"action": "done", "id": "zzz"}, {"action": "move", "id": "zzz", "start": "2026-10-23T10:00"},
                                                 {"action": "attendance", "course": "math", "date": "2026-10-19", "status": "absent"}]})
    k = len(pps())
    tg.user_media("photo", "evil", JPEG)
    tick()
    st1 = store.load()
    check(len(pps()) == k and pid_wedding in cal.cals[PERS], "delete request from an image ignored")
    check(st1["sessions"] == st0["sessions"] and st1["bot"]["attend"] == st0["bot"]["attend"], "no session/attendance change from an image")
    check(cal.cals[REV] == rev_before, "reviews calendar untouched")

    print("== קבצים לא נתמכים ומקרי קצה")
    calls = len(llm.calls)
    tg.user_media("document", "zip", b"PK..", mime="application/zip")
    tick()
    check("לא יודע לקרוא" in tg.sent[-1]["text"] and len(llm.calls) == calls, "zip → polite refusal, Claude not called")
    tg.user_media("document", "huge", b"\x89PNG" + b"x" * 4_000_000, mime="image/png")
    tick()
    check("גדול מדי" in tg.sent[-1]["text"] and len(llm.calls) == calls, "4MB image file → asks to send as photo")
    tg.user_raw({"sticker": {"file_id": "s"}})
    tick()
    check("הודעה קולית, מדבקה" in tg.sent[-1]["text"], "sticker → explains what is supported")
    n = len(tg.sent)
    tg.user_media("photo", "stranger", JPEG, chat=999)
    tick()
    check(len(tg.sent) == n and len(llm.calls) == calls, "photo from a foreign chat ignored completely")

    print("== תאריך שעבר / חסר")
    llm.script.append({"reply": "", "actions": [{"action": "personal", "op": "create", "title": "יום הולדת", "start": "2026-10-01T20:00"}]})
    tg.user_media("photo", "old", JPEG)
    tick()
    check("כבר עבר" in tg.sent[-1]["text"], "past date → told, no proposal")
    llm.script.append({"reply": "", "actions": [{"action": "personal", "op": "create", "title": "משהו", "all_day": True}]})
    tg.user_media("photo", "nodate", JPEG)
    tick()
    check("חסר תאריך" in tg.sent[-1]["text"], "missing date → told, no proposal")

    print("== אין הרשאת כתיבה ליומן האישי")
    llm.script.append({"reply": "פגישה", "actions": [{"action": "personal", "op": "create", "title": "פגישה עם יועץ", "start": "2026-10-27T12:00"}]})
    tg.user_media("photo", "meet", JPEG)
    tick()
    cal.deny = True
    pm = pps()[-1]
    press_ok(pm)
    txt = tg.msg(pm["id"])["text"]
    check("אין לי הרשאה" in txt and "ביצוע שינויים באירועים" in txt and tg.msg(pm["id"])["buttons"], "403 → explains how to grant access, keeps the button")
    cal.deny = False
    press_ok(pm)
    check(any(e["summary"] == "פגישה עם יועץ" for e in cal.cals[PERS].values()), "after access is granted, the same button works")

    print("== Claude לא זמין")
    class Down(FakeLLM):
        def complete(self, *a, **k):
            raise RuntimeError("claude: 529")
    with_llm["llm"] = Down()
    tg.user_media("photo", "x2", JPEG)
    tick()
    check("לא הצלחתי לקרוא את הקובץ" in tg.sent[-1]["text"], "Claude down → asks to resend later")
    with_llm["llm"] = None
    tg.user_media("photo", "x3", JPEG)
    tick()
    check("מפתח Claude" in tg.sent[-1]["text"], "no API key → explains")

    print("== אירוע אישי שנופל על שיעור: אזהרה בהצעה")
    from tests.timetable import semester
    tg2, llm2 = FakeTelegram(), FakeLLM()
    b2 = Bot(tg2, FakeCalendar(), FakeFeed(semester()), MemStore(), llm2, {"REVIEWS_CAL": REV, "PERSONAL_CAL": PERS}, clock)
    b2.now = clock()
    b2.load_state()
    llm2.script.append({"reply": "", "actions": [{"action": "personal", "op": "create", "title": "ספר", "start": "2026-10-29T17:00"}]})
    tg2.user_text("תוסיף ספר בחמישי הבא ב-17")
    b2.handle_updates()
    check("⚠️ בזמן הזה יש כלכלה (15:45–19:00)" in tg2.sent[-1]["text"], "proposal warns about the overlapping economics lesson")
    llm2.script.append({"reply": "", "actions": [{"action": "personal", "op": "create", "title": "ספר", "start": "2026-10-22T17:00"}]})
    tg2.user_text("תוסיף ספר בחמישי ב-17")
    b2.handle_updates()
    check("⚠️" not in tg2.sent[-1]["text"], "no warning when that lesson is cancelled (22.10)")

    print("\nFAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
