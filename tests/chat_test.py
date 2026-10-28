"""
שיחה עם הקשר (16.10, אחרי צילום המסך של עודד): הודעות המשך, איחוד הודעות רצופות, הצעות שממתינות,
והאזנה לטלגרם (long polling) בתוך ההרצה.
הרצה: python3 -m tests.chat_test
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402
from bot import main as M  # noqa: E402
from bot.adapters import Telegram  # noqa: E402
from bot.core import Bot, command_of  # noqa: E402
from tests.fakes import Clock, FakeCalendar, FakeFeed, FakeLLM, FakeTelegram, MemStore, freeze_planner  # noqa: E402
from tests.timetable import semester  # noqa: E402

TZ = P.TZ
REV, PERS = "reviews@cal", "me@cal"
FAILS = []


def check(c, m):
    print(("  PASS " if c else "  FAIL ") + m)
    if not c:
        FAILS.append(m)


def main():
    clock = Clock(datetime(2026, 10, 17, 12, 27, tzinfo=TZ))
    freeze_planner(clock)
    tg, cal, store, llm = FakeTelegram(), FakeCalendar(), MemStore(), FakeLLM()

    def tick():
        b = Bot(tg, cal, FakeFeed(semester()), store, llm, {"REVIEWS_CAL": REV, "PERSONAL_CAL": PERS}, clock)
        b.now = clock()
        b.load_state()
        try:
            b.handle_updates()
        finally:
            b.save_state()
        return b

    print("== הודעת המשך מקבלת את השיחה הקודמת")
    llm.script.append({"actions": [], "reply": "הבנתי: חתונה של מתן ויובל בגאמוס, שלישי 20.10 בערב. באיזו שעה היא מתחילה?"})
    tg.user_text("תקבע לי ביום שלישי בערב חתונה למתן ויובל בגאמוס")
    tick()
    clock.t += timedelta(minutes=2)
    llm.script.append({"reply": "שלחתי שתי הצעות לאישור. שעת הסיום של החתונה משוערת.", "actions": [
        {"action": "personal", "op": "create", "title": "החתונה של מתן ויובל", "start": "2026-10-20T18:00", "end": "2026-10-20T22:00", "location": "גאמוס"},
        {"action": "personal", "op": "create", "title": "קונדיטוריית הרים", "start": "2026-10-20T13:30", "location": "קונדיטוריית הרים"}]})
    tg.user_text("18:00, בנוסף אני צריך להיות ב13:30 בקונדיטוריית הרים.")
    tg.user_text("באותו יום שלישי")
    n_calls = len(llm.calls)
    tick()
    check(len(llm.calls) == n_calls + 1, "two quick messages → one Claude call")
    last = llm.calls[-1]
    check("18:00, בנוסף" in last and "באותו יום שלישי" in last.split("ההודעה של עודד:")[-1], "both lines in the current message")
    hist = last.split("השיחה האחרונה בצ'אט")[-1].split("ההודעה של עודד:")[0] if "השיחה האחרונה" in last else ""
    check("חתונה למתן ויובל בגאמוס" in hist and "באיזו שעה" in hist, "history has the first request and the bot's question")
    check("באותו יום שלישי" not in hist, "current message not duplicated into history")
    pps = [m for m in tg.sent if m["text"].startswith("🗓️")]
    check(len(pps) == 2, f"two proposals sent ({len(pps)})")
    replies = [m for m in tg.sent if not m["text"].startswith("🗓️")]
    check(len(replies) == 2, f"one reply per user turn ({len(replies)})")

    print("== הצעה שממתינה: Claude רואה אותה, ותיקון מחליף אותה")
    clock.t += timedelta(minutes=1)
    b = tick()
    pend = json.loads(b.context_for_llm())["pending_proposals"]
    check(len(pend) == 2 and {p["title"] for p in pend} == {"החתונה של מתן ויובל", "קונדיטוריית הרים"}, "pending proposals in context")
    wid = next(p["id"] for p in pend if p["title"].startswith("החתונה"))
    llm.script.append({"reply": "עדכנתי.", "actions": [
        {"action": "cancel_proposal", "id": wid},
        {"action": "personal", "op": "create", "title": "החתונה של מתן ויובל", "start": "2026-10-20T18:00", "end": "2026-10-20T23:00", "location": "גאמוס"}]})
    tg.user_text("החתונה נגמרת ב-23")
    tick()
    old = tg.msg(next(m["id"] for m in pps if "החתונה" in m["text"]))
    check("הוחלף בהצעה מעודכנת" in old["text"] and not old["buttons"], "old proposal marked replaced, buttons removed")
    check("18:00 עד 23:00" in tg.sent[-1]["text"], "new proposal with the corrected end")
    st = json.loads(store.blob)
    check(len(st["bot"]["chat"]) <= 16, "history capped")

    print("== פקודות לא מתאחדות עם טקסט")
    check(command_of("מחר") == "מחר" and command_of("מחר פנוי כל היום") is None, "command detection")
    ups = [{"update_id": 1, "message": {"chat": {"id": 1}, "text": "היום"}},
           {"update_id": 2, "message": {"chat": {"id": 1}, "text": "עשיתי מתמטיקה"}},
           {"update_id": 3, "message": {"chat": {"id": 1}, "text": "וגם כלכלה"}},
           {"update_id": 4, "callback_query": {"id": "c", "data": "noop", "message": {"chat": {"id": 1}, "message_id": 1}}},
           {"update_id": 5, "message": {"chat": {"id": 2}, "text": "זר"}}]
    merged = Bot._merge_texts(ups)
    check([u["update_id"] for u in merged] == [1, 3, 4, 5] and merged[1]["message"]["text"] == "עשיתי מתמטיקה\nוגם כלכלה",
          "only consecutive non-command texts from the same chat merge")

    print("== הקשבה לטלגרם בתוך ההרצה (long polling)")
    t = [0.0]
    clk = lambda: t[0]  # noqa: E731

    class SlowTG(FakeTelegram):
        def get_updates(self, offset, timeout=0):
            got = super().get_updates(offset, timeout)
            if not got:
                t[0] += timeout       # אף אחד לא כתב: הקריאה מחכה עד הסוף
            else:
                t[0] += 1
            if t[0] >= 12 and not getattr(self, "pressed", False):
                self.pressed = True
                self.user_text("מחר")   # מגיע באמצע ההאזנה
            return got
    tg2 = SlowTG()
    b2 = Bot(tg2, FakeCalendar(), FakeFeed(semester()), MemStore(), FakeLLM(), {"REVIEWS_CAL": REV, "PERSONAL_CAL": PERS}, clock)
    b2.now = clock()
    b2.load_state()
    M.listen(b2, 0.0, until_s=35, clock=clk)
    check(any(m["text"].startswith("📅") for m in tg2.sent), "message that arrived mid-listen answered in the same run")
    check(t[0] <= 35, f"listening stops by the budget ({t[0]:.0f}s)")
    clock.t = datetime(2026, 10, 17, 23, 0, 30, tzinfo=TZ)
    tg3 = SlowTG()
    b3 = Bot(tg3, FakeCalendar(), FakeFeed(semester()), MemStore(), FakeLLM(), {"REVIEWS_CAL": REV, "PERSONAL_CAL": PERS}, clock)
    b3.now = clock()
    b3.load_state()
    t[0] = 0.0
    M.listen(b3, 0.0, clock=clk)
    check(getattr(tg3, "polls", 0) == 0, "no listening after 23:00")

    print("== כפתור קישור ל-Google")
    mk = Telegram._markup([[("📅 להוסיף בעצמי", "https://calendar.google.com/x"), ("✅", "pp|ok|1")]])
    check(mk["inline_keyboard"][0][0] == {"text": "📅 להוסיף בעצמי", "url": "https://calendar.google.com/x"}
          and mk["inline_keyboard"][0][1]["callback_data"] == "pp|ok|1", "url button vs callback button")
    link = Bot.gcal_link({"title": "החתונה של מתן ויובל", "new_start": "2026-10-20T18:00+03:00", "new_end": "2026-10-20T22:00+03:00", "location": "גאמוס"})
    check("dates=20261020T180000%2F20261020T220000" in link and "ctz=Asia%2FJerusalem" in link and "action=TEMPLATE" in link, "template link")

    print("\nFAILS:", FAILS)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
