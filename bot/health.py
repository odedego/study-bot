"""בדיקת חיבורים. רצה מ-Actions (check), מפקודה בטלגרם ("בדיקה"), ופעם אחת אחרי כל עדכון קוד."""
from __future__ import annotations

from datetime import datetime, timedelta

import planner as P


def run(tg, cal, feed, store, llm, cfg: dict, now: datetime) -> list[str]:
    res = []

    def step(name, fn):
        try:
            res.append(f"✅ {name}: {fn()}")
        except Exception as e:  # noqa: BLE001
            res.append(f"❌ {name}: {str(e)[:160]}")

    if hasattr(tg, "_call"):
        step("טלגרם", lambda: "@" + tg._call("getMe")["username"])
    step("Google (חשבון שירות)", lambda: getattr(cal, "email", "") or "?")
    step("יומן חזרות", lambda: f"{len(cal.list(cfg['REVIEWS_CAL'], now.isoformat(), (now + timedelta(days=14)).isoformat()))} אירועים בשבועיים הקרובים")
    step("יומן אישי", lambda: f"{len(cal.list(cfg['PERSONAL_CAL'], now.isoformat(), (now + timedelta(days=14)).isoformat()))} אירועים" if cfg.get("PERSONAL_CAL") else "לא הוגדר")

    def role():
        if not cfg.get("PERSONAL_CAL"):
            return "לא הוגדר"
        r = cal.access_role(cfg["PERSONAL_CAL"])
        if r in ("writer", "owner"):
            return "כתיבה (רק אחרי ✅ שלך)"
        raise RuntimeError(f"קריאה בלבד ({r}): הוספה מתמונות ושינויים שאישרת ייכשלו. "
                           "צריך לשנות את השיתוף של חשבון study-bot ל\"ביצוע שינויים באירועים\"")
    step("הרשאה ליומן האישי", role)

    def rev_write():
        body = {"summary": "בדיקת חיבור (נמחק מיד)", "start": {"dateTime": (now + timedelta(days=30)).isoformat()},
                "end": {"dateTime": (now + timedelta(days=30, minutes=5)).isoformat()}}
        eid = cal.create(cfg["REVIEWS_CAL"], body)["id"]
        cal.delete(cfg["REVIEWS_CAL"], eid)
        return "יצירה ומחיקה עובדות"
    step("כתיבה ליומן חזרות", rev_write)

    def feed_ok():
        evs = P.classify(P._parse_ics(feed.fetch()))
        if not evs.lessons:
            raise RuntimeError("הפיד הגיע אבל לא זיהיתי בו שיעורים")
        return f"{len(evs.lessons)} שיעורים בסמסטר"
    step("פיד האוניברסיטה", feed_ok)

    def up():
        if not hasattr(store, "KEY"):
            raise RuntimeError("UPSTASH_URL לא הוגדר")
        return "יש מצב שמור" if store.load() else "ריק (ייטען מצב התחלתי)"
    step("Upstash", up)

    def claude_ping():
        if not llm:
            return "לא הוגדר (כפתורים ופקודות בלבד)"
        from bot.adapters import Truncated
        try:
            return llm.complete("ענה במילה אחת", "אמור: תקין", 10).strip() or "?"
        except Truncated:
            return "מגיב"      # הפלט נחתך במגבלה הקטנה של הבדיקה: החיבור עצמו תקין
    step("Claude", claude_ping)

    # פלט מובנה (כלי): אותו מנגנון שפענוח ההודעות משתמש בו
    def claude_tool():
        if not llm:
            return "לא הוגדר"
        import json
        out = json.loads(llm.complete("החזר דרך הכלי: ok=true", "בדיקה", 200,
                                      tool={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}))
        if not isinstance(out, dict):
            raise RuntimeError("הכלי לא החזיר אובייקט")
        return "תקין"
    step("Claude (פלט מובנה)", claude_tool)
    return res
