"""
תזכורת לפני שיעור (מהריקאפים): בערב שלפני (מלא, כ-PDF) ובבוקר של השיעור (קצר).
לכל קורס שיש לו שיעור: מה נלמד בשיעור הקודם, רמת ההבנה, מה לא היה ברור, מה הפריע, שאלה מוכנה לצ'אט ומה מחכה לפי הסילבוס.
"שאלה לצ'אט": ניסוח מוכן שעודד מדביק לצ'אט. הבוט לא עונה עליה (עודד בודק במקורות; אם נשאר לא ברור, שואל את המרצה).
"""
from __future__ import annotations

from datetime import date, time, timedelta

import planner as P
from bot import semester as SEM
from bot import spaced as SP
from bot import topics as TOP

COURSES = P.COURSES


class PrepMixin:
    MAX_AGE_DAYS = 28

    def prev_lesson(self, c: str, before: date, uni) -> tuple[date | None, dict | None, str | None]:
        """השיעור הקודם בפועל של הקורס: (תאריך, הריקאפ שלו אם יש, נוכחות). בלי פיד: הריקאפ האחרון, עד 28 יום."""
        days = sorted({l.day for l in uni.lessons if l.course == c and l.day < before})
        has = lambda r: r.get("rating") is not None or (r.get("topics") or "").strip() or (r.get("unclear") or "").strip()  # noqa: E731
        if days:
            d = days[-1]
            att = self.st["bot"]["attend"].get(f"{c}|{d.isoformat()}")
            r = next((r for r in self.st.get("recaps", []) if r.get("course") == c and r.get("date") == d.isoformat() and has(r)), None)
            return d, r, att
        lo = (before - timedelta(days=self.MAX_AGE_DAYS)).isoformat()
        rows = [r for r in self.st.get("recaps", []) if r.get("course") == c and lo <= r.get("date", "") < before.isoformat() and has(r)]
        if not rows:
            return None, None, None
        r = max(rows, key=lambda r: r["date"])
        return date.fromisoformat(r["date"]), r, self.st["bot"]["attend"].get(f"{c}|{r['date']}")

    @staticmethod
    def chat_question(name: str, topic: str, unclear: str) -> str:
        t = f" (נושא: {topic})" if topic else ""
        return f"בקורס {name}{t} לא הבנתי: {unclear}. תסביר לי בפשטות, עם דוגמה קצרה, ואז תבדוק אותי בשאלה אחת."

    def prep_entries(self, day: date, uni) -> list[dict]:
        out = []
        for c in sorted({l.course for l in uni.lessons if l.day == day and l.course in COURSES}, key=P.COURSE_ORDER.index):
            if self.st["bot"]["attend"].get(f"{c}|{day.isoformat()}") in ("absent", "reserve"):
                continue             # סימן מראש שלא יהיה בשיעור
            name = COURSES[c]["name"]
            e = {"course": c, "name": name, "last": None, "prev_date": None, "prev_status": None,
                 "next": SEM.topics_for_week(P.week_start(day)).get(c, ""),
                 "weak": [SP.short(t["name"]) for _, t in TOP.live(self.st, c) if TOP.is_weak(t)][:2],
                 "due": [SP.short(x["name"]) for x in SP.due(self.st, day, c, limit=2)]}
            d, r, att = self.prev_lesson(c, day, uni)
            if d:
                e["prev_date"] = d.isoformat()
            if d and att in ("absent", "reserve"):
                e["prev_status"] = "missed"
            elif r:
                e["last"] = {"date": r["date"], "rating": r.get("rating"), "topics": (r.get("topics") or "").strip()[:140],
                             "unclear": (r.get("unclear") or "").strip()[:140], "cause": (r.get("cause") or "").strip()[:80]}
                if e["last"]["unclear"] or (e["last"]["rating"] is not None and e["last"]["rating"] <= 3 and e["last"]["topics"]):
                    e["question"] = self.chat_question(name, SP.short(e["last"]["topics"]) if e["last"]["topics"] else "",
                                                       e["last"]["unclear"] or "החומר בשיעור הקודם")
            elif d:
                e["prev_status"] = "no_recap"
            out.append(e)
        return out

    def prep_lines(self, entries: list[dict], day: date, full: bool) -> list[str]:
        n = 90 if full else 50
        lines = []
        for e in entries:
            L = e["last"]
            head = f"• {e['name']}"
            if not L:
                pd = date.fromisoformat(e["prev_date"]) if e.get("prev_date") else None
                when = f" ({pd.day}.{pd.month})" if pd else ""
                lines.append(head + (f": בשיעור הקודם{when} לא היית." if e.get("prev_status") == "missed" else
                                     f": אין ריקאפ מהשיעור הקודם{when}. אפשר לכתוב \"ריקאפים\"." if e.get("prev_status") == "no_recap" else
                                     ": עוד אין ריקאפ."))
                continue
            d = date.fromisoformat(L["date"])
            bits = []
            if L["topics"]:
                bits.append(f"למדת: {L['topics'][:n]}")
            if L["rating"] is not None:
                bits.append(f"הבנה {L['rating']}/5")
            if L["unclear"]:
                bits.append(f"לא ברור: {L['unclear'][:n]}")
            lines.append(f"{head} · שיעור קודם {d.day}.{d.month}" + (": " + " · ".join(bits) if bits else ""))
            if full:
                if L["cause"]:
                    lines.append(f"   הפריע: {L['cause']}")
                if e["weak"]:
                    lines.append("   נושא חלש פתוח: " + ", ".join(e["weak"]))
                if e.get("question"):
                    lines.append(f"   💬 שאלה לצ'אט: «{e['question']}»")
                if e["due"]:
                    lines.append("   🔁 מתאים לחזרה: " + ", ".join(e["due"]))
                if e["next"]:
                    lines.append(f"   📖 לפי הסילבוס השבוע: {e['next']}")
        return lines

    # ------------------------------------------------------------ בוקר (קצר) וערב (מלא ב-PDF)
    def prep_morning_lines(self, today: date, uni) -> list[str]:
        en = self.prep_entries(today, uni)
        if not en:
            return []
        return ["📚 לפני השיעורים, מה היה בשיעור הקודם:"] + self.prep_lines(en, today, full=False)

    def maybe_prep_evening(self, today: date, uni):
        """רק בריצה של הערב (אחרי 20:00, לא בבקשת "סיכום" באמצע היום), ופעם אחת לכל יום לימודים."""
        if not self.conv or self.now.time() < time(20, 0):
            return
        tm = (today + timedelta(days=1)).isoformat()
        sent = self.st["bot"].setdefault("prep_sent", {})
        if tm in sent:
            return
        try:
            self.send_prep_evening(today, uni)
        except Exception as e:  # noqa: BLE001
            self.log.append(f"prep evening: {type(e).__name__}: {str(e)[:100]}")
        for k in [k for k in sent if k < today.isoformat()]:
            del sent[k]

    def send_prep_evening(self, today: date, uni) -> bool:
        tm = today + timedelta(days=1)
        en = self.prep_entries(tm, uni)
        if not en:
            return False
        title = f"מה למדת בשיעור הקודם · {tm.day}.{tm.month}"
        sent = False
        try:
            from bot.report_pdf import build_prep_pdf
            data = {"title": "לפני השיעורים של מחר", "range": f"{P.HEB_DAY[tm.weekday()]} {tm.day}.{tm.month}", "entries": en}
            self.tg.send_document(f"לפני-שיעור-{tm.day}-{tm.month}.pdf", build_prep_pdf(data), caption="📚 " + title)
            sent = True
        except Exception as ex:  # noqa: BLE001
            self.log.append(f"prep pdf: {type(ex).__name__}: {str(ex)[:120]}")
        self.st["bot"].setdefault("prep_sent", {})[tm.isoformat()] = True     # מסומן מיד: כשל בהודעה הקצרה לא יגרום לשליחה חוזרת של הקובץ
        if sent:
            self.tg.send("📚 מחר: " + ", ".join(e["name"] for e in en) + ". בקובץ: מה למדת, רמת ההבנה, מה לא היה ברור ושאלה מוכנה לצ'אט.\n"
                         + "\n".join(self.prep_lines(en, tm, full=False)))
        else:
            self.tg.send("📚 " + title + "\n" + "\n".join(self.prep_lines(en, tm, full=True)))
        return True
