"""
python -m bot.main tick    הרצה רגילה (כל 5 דקות מ-GitHub Actions)
python -m bot.main check   בדיקת חיבורים: טלגרם, יומנים, פיד, Upstash, Claude. שולח דוח לטלגרם.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from bot import adapters as A
from bot.core import Bot, TZ, P


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def build(require_chat=True):
    tg = A.Telegram(env("TELEGRAM_TOKEN"), env("TELEGRAM_CHAT_ID"))
    cal = A.Calendar(env("GOOGLE_SA_JSON"))
    feed = A.Feed(env("FEED_URL"))
    if env("UPSTASH_URL"):
        store = A.Upstash(env("UPSTASH_URL"), env("UPSTASH_TOKEN"))
    elif env("GITHUB_ACTIONS"):
        sys.exit("UPSTASH_URL חסר: בלי מקום שמירה הבוט היה מתחיל מאפס בכל הרצה. עוצר.")
    else:
        store = A.FileStore(".state.json")
    llm = A.Claude(env("ANTHROPIC_API_KEY"), env("CLAUDE_MODEL", "claude-sonnet-5-5")) if env("ANTHROPIC_API_KEY") else None
    cfg = {"REVIEWS_CAL": env("REVIEWS_CAL_ID"), "PERSONAL_CAL": env("PERSONAL_CAL_ID")}
    return Bot(tg, cal, feed, store, llm, cfg), tg, cal, feed, store, llm, cfg


def tick():
    bot, *_ = build()
    bot.now = bot.now_fn()
    bot.load_state()   # אם זה נכשל: לא ממשיכים ולא שומרים כלום
    try:
        bot.handle_updates()
        if bot.tg.chat_id:
            bot.run_jobs()
    finally:
        bot.save_state()
        for line in bot.log:
            print(line[:300])


def check():
    bot, tg, cal, feed, store, llm, cfg = build()
    now = datetime.now(TZ)
    res = []

    def step(name, fn):
        try:
            res.append(f"✅ {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            res.append(f"❌ {name}: {str(e)[:160]}")

    step("Google (חשבון שירות)", lambda: cal.email)
    step("יומן חזרות", lambda: f"{len(cal.list(cfg['REVIEWS_CAL'], now.isoformat(), (now + timedelta(days=14)).isoformat()))} אירועים בשבועיים הקרובים")
    step("יומן אישי", lambda: f"{len(cal.list(cfg['PERSONAL_CAL'], now.isoformat(), (now + timedelta(days=14)).isoformat()))} אירועים" if cfg["PERSONAL_CAL"] else "לא הוגדר")

    def f():
        t = feed.fetch()
        evs = P.classify(P._parse_ics(t))
        if not evs.lessons:
            raise RuntimeError("הפיד הגיע אבל לא זיהיתי בו שיעורים")
        return f"{len(evs.lessons)} שיעורים בסמסטר"
    step("פיד האוניברסיטה", f)
    def up():
        if not isinstance(store, A.Upstash):
            raise RuntimeError("UPSTASH_URL לא הוגדר")
        return "יש מצב שמור" if store.load() else "ריק (ייטען מצב התחלתי)"
    step("Upstash", up)
    step("Claude", lambda: (llm.complete("ענה במילה אחת", "אמור: תקין", 10).strip() or "?") if llm else "לא הוגדר (כפתורים ופקודות בלבד)")
    text = "🔧 בדיקת חיבורים\n" + "\n".join(res)
    print(text)
    if tg.chat_id:
        tg.send(text)
    else:
        print("TELEGRAM_CHAT_ID חסר: שלח לבוט הודעה, והוא יחזיר לך את המספר בהרצה הבאה.")
    if any(r.startswith("❌") for r in res):
        sys.exit(1)


if __name__ == "__main__":
    {"tick": tick, "check": check}[sys.argv[1] if len(sys.argv) > 1 else "tick"]()
