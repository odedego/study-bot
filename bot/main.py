"""
python -m bot.main tick    הרצה רגילה (כל 5 דקות מ-GitHub Actions)
python -m bot.main check   בדיקת חיבורים: טלגרם, יומנים, פיד, Upstash, Claude. שולח דוח לטלגרם.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

from bot import adapters as A
from bot import health
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


ACTIVE_HOURS = (7, 23)   # שעון ישראל. בלילה הבוט לא עושה כלום (מה שנכתב בלילה מטופל ב-07:00)


def tick():
    h = datetime.now(TZ).hour
    if not ACTIVE_HOURS[0] <= h < ACTIVE_HOURS[1] and env("FORCE_TICK") != "1":
        print(f"לילה ({h}:xx שעון ישראל): לא רץ")
        return
    bot, *_ = build()
    bot.now = bot.now_fn()
    bot.load_state()   # אם זה נכשל: לא ממשיכים ולא שומרים כלום
    try:
        bot.handle_updates()
        if bot.tg.chat_id:
            bot.run_jobs()
            bot.after_deploy(env("GITHUB_SHA")[:7])
    finally:
        bot.save_state()
        for line in bot.log:
            print(line[:300])


def check():
    bot, tg, cal, feed, store, llm, cfg = build()
    res = health.run(tg, cal, feed, store, llm, cfg, datetime.now(TZ))
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
