"""
יומן "שיעורים" נקי: כל שיעור עם שם הקורס, חדר ובניין, בלי שמות מרצים (בקשה של עודד: "אחרי שבוע").
היומן שייך לחשבון השירות ומשותף לעודד לקריאה. הבוט מסנכרן אותו מהפיד: הוספה, הזזה, ביטול.
על פיד חשוד (ריק / ירידה חדה) לא נוגעים בכלום. היומן של האוניברסיטה נשאר, ועודד רק מסתיר אותו.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

import planner as P
from bot import semester as SEM

MAX_WRITES_PER_RUN = 60   # הסנכרון הראשון (~120 שיעורים) מתחלק לכמה הרצות


def _h(body: dict) -> str:
    return hashlib.sha1(repr((body["summary"], body["start"], body["end"], body.get("location", ""))).encode()).hexdigest()[:10]


class LessonsCalMixin:
    def lessons_cal_id(self) -> str:
        return self.st["bot"].get("lessons_cal", {}).get("id", "") if getattr(self, "st", None) else ""

    def lessons_sync_due(self) -> bool:
        lc = self.st["bot"].get("lessons_cal", {})
        if self.now.date() < SEM.LESSONS_CAL_FROM or lc.get("off") or self.now.hour < 7:
            return False
        return lc.get("incomplete") or lc.get("last", "")[:10] != self.now.date().isoformat()

    def _lesson_bodies(self, uni) -> dict:
        today = self.now.date()
        lo, hi = today - timedelta(days=1), SEM.SEMESTER_END + timedelta(days=7)
        out = {}
        for l in uni.lessons:
            if lo <= l.day <= hi:
                name = SEM.FULL_NAMES.get(l.course, P.COURSES[l.course]["name"]) + (" · תרגול" if l.kind == "תרגול" else "")
                out[P.lesson_key(l)] = {"summary": name, "location": l.location,
                                        "start": {"dateTime": l.start.isoformat(), "timeZone": "Asia/Jerusalem"},
                                        "end": {"dateTime": l.end.isoformat(), "timeZone": "Asia/Jerusalem"},
                                        "description": "מתוך מערכת השעות של האוניברסיטה. מתעדכן אוטומטית מבוט הלימודים."}
        for c, e in uni.cancelled:
            if c and lo <= e.start.date() <= hi:
                k = f"{c}:{e.start.date().isoformat()}:בוטל"
                if not any(x.startswith(f"{c}:{e.start.date().isoformat()}:") for x in out):
                    out[k] = {"summary": f"🚫 בוטל · {SEM.FULL_NAMES.get(c, c)}", "location": e.location, "transparency": "transparent",
                              "start": {"dateTime": e.start.isoformat(), "timeZone": "Asia/Jerusalem"},
                              "end": {"dateTime": e.end.isoformat(), "timeZone": "Asia/Jerusalem"},
                              "description": "השיעור בוטל לפי מערכת השעות של האוניברסיטה."}
        return out

    def sync_lessons_cal(self) -> list[str]:
        """מחזיר שורות להודעה (רק בפעם הראשונה, כשהיומן נוצר)."""
        from bot.core import event_id
        lc = self.st["bot"].setdefault("lessons_cal", {})
        uni, raw = self.load_uni()
        if getattr(self, "_feed_error", None):
            return []
        fc = P.feed_check(uni, raw, self.st, self.now.date(), self.now, False)
        if fc.get("block"):
            self.log.append("lessons cal: feed blocked, not syncing")
            return []
        msg = []
        if not lc.get("id"):
            lc["id"] = self.cal.create_calendar("שיעורים")
        email = self.personal_cal if "@" in self.personal_cal and "calendar.google.com" not in self.personal_cal else ""
        if not lc.get("shared"):
            if email:
                self.cal.share(lc["id"], email, "reader")
                lc["shared"] = True
                msg = ["📅 יש לך עכשיו יומן Google חדש בשם \"שיעורים\": כל השיעורים עם שם הקורס, חדר ובניין, בלי שמות מרצים. "
                       "הוא מתעדכן לבד מהמערכת (ביטולים והזזות). קיבלת מגוגל מייל על יומן ששותף איתך: לחץ שם על הוספה, "
                       "ואחר כך הסתר את היומן הישן של האוניברסיטה (\"יומן אישי מתחנת המידע\")."]
            else:
                msg = [f"📅 יצרתי יומן \"שיעורים\" בלי שמות מרצים, אבל לא מצאתי את המייל שלך כדי לשתף. מזהה היומן: {lc['id']}"]
                lc["shared"] = True
        want = self._lesson_bodies(uni)
        have = lc.setdefault("map", {})
        writes = 0
        for k, body in want.items():
            if writes >= MAX_WRITES_PER_RUN:
                break
            h = _h(body)
            cur = have.get(k)
            if cur and cur["h"] == h:
                continue
            if cur:
                self.cal.patch(lc["id"], cur["id"], body)
            else:
                cur = {"id": self.cal.create(lc["id"], dict(body, id=event_id("lc:" + k)))["id"]}
            have[k] = {"id": cur["id"], "h": h, "d": k.split(":")[1]}
            writes += 1
        today = self.now.date().isoformat()
        for k in [k for k, v in have.items() if k not in want and v["d"] >= today]:
            if writes >= MAX_WRITES_PER_RUN:
                break
            self.cal.delete(lc["id"], have.pop(k)["id"])
            writes += 1
        old = (self.now.date() - timedelta(days=7)).isoformat()
        lc["map"] = {k: v for k, v in have.items() if v["d"] >= old}
        lc["incomplete"] = writes >= MAX_WRITES_PER_RUN
        lc["last"] = P.iso(self.now)
        return msg
