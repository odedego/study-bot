"""דוח שבועי ב-PDF (שלב K): בניית הנתונים מהמצב ושליחה. העיצוב עצמו ב-report_pdf.py."""
from __future__ import annotations

from datetime import date, timedelta

import planner as P
from bot import semester as SEM
from bot import suggest as SUG
from bot import topics as TOP

COURSES = P.COURSES


class ReportMixin:
    def weekly_pdf_data(self, ws: date, today: date, uni, rep: dict, headline: str) -> dict:
        cur, prev = self.week_minutes(ws), self.week_minutes(ws - timedelta(days=7))
        courses = []
        for c in P.COURSE_ORDER:
            s = TOP.stats(self.st, c, today)
            if not s["total"]:
                continue
            weak = []
            for _, t in TOP.live(self.st, c):
                if TOP.is_weak(t):
                    weak.append({"topic": t["name"].split(":")[0][:60], "rating": t["rating"], "unclear": t.get("unclear", "")})
            courses.append({"name": COURSES[c]["name"], "min": cur.get(c, 0), "prev_min": prev.get(c, 0), "learned": s["learned"],
                            "total": s["total"], "reviewed": s["reviewed"], "weak": weak, "pending": [n.split(":")[0][:40] for n in s["pending"]]})
        nxt = today + timedelta(days=1)
        exams = [{"name": COURSES[c]["name"], "date": f"{dt.day}.{dt.month}", "days": n} for c, dt, n in SEM.upcoming_exams(today)]
        gaps = sorted({COURSES[g["course"]]["name"] for g in SUG.open_gaps(self.st, today, uni, self.now)})
        tips = [f"{COURSES[x['course']]['name']}: " + ", ".join(x["why"]) for x in SUG.recommend(self.st, today, uni, self.now, limit=3)]
        need = rep.get("recaps_needed") or 0
        return {"title": "סיכום שבוע", "range": f"{ws.day}.{ws.month} – {today.day}.{today.month}",
                "verdict": headline.split(": ", 1)[-1], "total_min": sum(cur.values()), "prev_total_min": sum(prev.values()),
                "recaps": (need - len(rep.get("missing_recaps", [])), need) if need else None, "courses": courses, "exams": exams,
                "next_week": [x.lstrip("• ") for x in self.syllabus_lines(P.week_start(nxt))], "gaps": gaps, "tips": tips}

    def send_weekly_pdf(self, ws: date, today: date, uni, rep: dict, headline: str) -> bool:
        """True אם ה-PDF נשלח. כל כשל (WeasyPrint חסר, גופן, רשת) מחזיר False וקוראים לתצוגת הטקסט המלאה."""
        try:
            from bot.report_pdf import build_pdf
            pdf = build_pdf(self.weekly_pdf_data(ws, today, uni, rep, headline))
            self.tg.send_document(f"סיכום-שבוע-{today.day}-{today.month}.pdf", pdf, caption=f"📎 הדוח המלא · שבוע {ws.day}.{ws.month}")
            return True
        except Exception as e:  # noqa: BLE001
            self.log.append(f"weekly pdf: {type(e).__name__}: {str(e)[:120]}")
            return False
