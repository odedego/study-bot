"""
מרתון v2 (שלב H): שעות במקום סשנים, וההצעות הן של עודד לאשר. בלי שיבוץ אוטומטי ובלי כתיבה ליומן.
  - נדרש לכל קורס (דקות): בסיס (MARATHON_NEED × 50) כפול הנותר במפת החומר: נושא שלא נלמד 1.0, נלמד ולא נחזר 0.6,
    נחזר בהערכה 0.45, נחזר פעם אחת 0.35, פעמיים ומעלה 0.1, נושא חלש +0.3. שליטה (מבחן תרגול 90+) = 15% מהבסיס. הערכה, ועודד מתקן.
  - פנוי: חלונות פנויים ביום (suggest.free_windows), בלי ימים חסומים, בלי ימי מבחן.
  - הקצאה (EDF): כל יום מחולק קודם לקורס שהמבחן שלו הכי קרוב. מה שלא נכנס עד המבחן = חוסר, ומוצג בגלוי.
  - הצעת היום: כמה זמן לכל קורס, וחלונות עם כפתורי אישור (sa|...). עודד קובע.
  - מד מוכנות לכל קורס, ודף חזרה (PDF): נושאים לפי מצב, מה לא היה ברור, וציוני מבחני תרגול.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import planner as P
from bot import semester as SEM
from bot import suggest as SUG
from bot import topics as TOP

COURSES = P.COURSES
SESSION_MIN = 50
MASTERED_LEFT = 0.15
SOFT_CAP_MIN = 180          # מכסה רכה ליום לקורס (לערבוב קורסים). אם יש מקום פנוי, היא מורמת
MAX_WINDOWS = 6
DAY_CAP_MIN = SEM.MARATHON_DAY_CAP * SESSION_MIN      # 350 דק' ליום (ברירת המחדל מ-v1; עודד אמר שמוקדם לקבוע)
MAX_COURSES_PER_DAY = 3
MIN_CHUNK_MIN = 25
FACTORS = {"unlearned": 1.0, "learned": 0.6, "est": 0.45, "r1": 0.35, "r2": 0.1}


def hours(minutes: float) -> str:
    m = int(round(minutes / 5.0) * 5)
    h, r = divmod(m, 60)
    if h and r:
        return f"{h}:{r:02d} ש׳"
    return f"{h} ש׳" if h else f"{r} דק׳"


def topic_factor(t: dict) -> float:
    if not t["learned"]:
        f = FACTORS["unlearned"]
    else:
        real = len({d for d in TOP.reviewed_real(t)})
        if real >= 2:
            f = FACTORS["r2"]
        elif real == 1:
            f = FACTORS["r1"]
        elif t["reviewed"]:
            f = FACTORS["est"]
        else:
            f = FACTORS["learned"]
    if TOP.is_weak(t):
        f = min(1.0, f + 0.3)
    return f


class Marathon2Mixin:
    # ------------------------------------------------------------ נדרש
    def m2_baseline(self, c: str) -> float:
        m = self.mar()
        if c in m.get("need_min", {}):
            return float(m["need_min"][c])
        return self.mar_need(c) * SESSION_MIN

    def m2_remaining(self, c: str) -> float:
        base = self.m2_baseline(c)
        if self.mar_mastered(c):
            return base * MASTERED_LEFT
        tp = [t for _, t in TOP.live(self.st, c)]
        if not tp:
            return base
        return base * sum(topic_factor(t) for t in tp) / len(tp)

    def m2_left(self, c: str) -> float:
        """הנותר בפועל: לפי מפת החומר, פחות סשנים שנקבעו (מלא) ופחות לימוד שנרשם מתחילת המרתון (60%: חלקו כבר במפה)."""
        left = self.m2_remaining(c)
        start = self.mar()["start"]
        planned = sum((datetime.fromisoformat(x["end"]) - datetime.fromisoformat(x["start"])).total_seconds() / 60
                      for x in self.st.get("sessions", []) if x.get("course") == c and x.get("status") == "planned"
                      and datetime.fromisoformat(x["end"]) > self.now)
        logged = sum(int(r.get("min") or 0) for r in self.st.get("log", []) if r.get("course") == c and r.get("date", "") >= start)
        return max(0.0, left - planned - 0.6 * logged)

    def m2_readiness(self, c: str) -> int:
        base = self.m2_baseline(c) or 1
        return max(0, min(100, int(round(100 * (1 - self.m2_left(c) / base)))))

    # ------------------------------------------------------------ פנוי והקצאה
    def m2_day_free(self, d: date, uni, personal) -> list[tuple[datetime, datetime]]:
        """כל הזמן הפנוי ביום (מפוצל לחלונות של עד שעתיים), עד תקרה יומית."""
        if d.isoformat() in self.mar()["blocked"]:
            return []
        if d in {ed for _, ed in self.mar_exams()}:
            return []
        return SUG.free_chunks(d, uni, personal, self.st, self.now, cap_min=DAY_CAP_MIN, max_n=MAX_WINDOWS)

    def m2_plan(self, first: date | None = None) -> dict:
        """{"days": {date: {course: min}}, "windows": {date: [(a,b)]}, "left": {course: min}, "free": {course: min},
        "exams": [(course, date)], "personal_until": date, "personal_error": str|None}.
        חוסר: EDF (כל יום קודם לקורס שהמבחן שלו הכי קרוב). הצעה: לפי דחיפות (ערבוב קורסים), ואם היא לא מכסה כמו EDF, מציעים את ה-EDF.
        החוסר המדווח הוא תמיד של ההצעה שמוצגת."""
        today = self.now.date()
        exams = self.mar_exams(after=today)
        start = date.fromisoformat(self.mar()["start"])
        first = first or max(today, start)
        uni, _ = self.load_uni()
        personal = self.load_personal(P.week_start(first))
        need0 = {c: self.m2_left(c) for c, _ in exams}
        end = exams[-1][1] if exams else first
        wins, avail = {}, {}
        d = first
        while d < end:
            wins[d] = self.m2_day_free(d, uni, personal)
            avail[d] = sum((b - a).total_seconds() / 60 for a, b in wins[d])
            d += timedelta(days=1)
        # 1) EDF
        need = dict(need0)
        free_for = {c: 0.0 for c, _ in exams}
        edf_days = {}
        for d in sorted(avail):
            left, row = avail[d], {}
            for c, ed in exams:
                if ed > d:
                    free_for[c] += avail[d]
                    take = min(left, need[c])
                    if take > 0:
                        row[c] = take
                        need[c] -= take
                        left -= take
            if row:
                edf_days[d] = row
        edf_left = {c: n for c, n in need.items() if n > 5}
        # 2) לפי דחיפות
        need = dict(need0)
        suffix = dict(free_for)
        pace_days = {}
        for d in sorted(avail):
            elig = [c for c, ed in exams if ed > d and need[c] > 0]
            elig = sorted(elig, key=lambda c: -need[c] / max(suffix[c], 1.0))[:MAX_COURSES_PER_DAY]
            row, left = {}, avail[d]
            for _ in range(3):
                if left <= 1 or not elig:
                    break
                w = {c: need[c] / max(suffix[c], 1.0) for c in elig}
                tot = sum(w.values()) or 1.0
                spent = 0.0
                for c in elig:
                    take = min(left * w[c] / tot, need[c], max(0, SOFT_CAP_MIN - row.get(c, 0)))
                    if take > 0:
                        row[c] = row.get(c, 0) + take
                        need[c] -= take
                        spent += take
                left -= spent
                elig = [c for c in elig if need[c] > 0 and row.get(c, 0) < SOFT_CAP_MIN]
            for c, ed in exams:
                if ed > d:
                    suffix[c] -= avail[d]
            small = [c for c, v in row.items() if v < MIN_CHUNK_MIN]
            if small and len(small) < len(row):
                big = max(row, key=row.get)
                for c in small:                       # חתיכה קטנה מדי: מועברת לקורס הגדול, והנדרש מתעדכן
                    v = row.pop(c)
                    need[c] += v
                    add = min(v, need[big])
                    row[big] += add
                    need[big] -= add
            for c in [c for c, v in row.items() if v < 10]:
                need[c] += row.pop(c)
            if row:
                pace_days[d] = dict(sorted(row.items(), key=lambda kv: next(ed for c, ed in exams if c == kv[0])))
        pace_left = {c: n for c, n in need.items() if n > 5}
        if sum(pace_left.values()) <= sum(edf_left.values()) + 5:
            days, deficit = pace_days, pace_left
        else:
            days, deficit = {d: dict(sorted(r.items(), key=lambda kv: next(ed for c, ed in exams if c == kv[0]))) for d, r in edf_days.items()}, edf_left
        return {"days": days, "windows": wins, "left": deficit, "free": free_for, "exams": exams,
                "personal_until": P.week_start(today) + timedelta(days=21), "personal_error": getattr(self, "_personal_error", None)}

    # ------------------------------------------------------------ הצגה
    def m2_active_view(self, today: date | None = None) -> bool:
        today = today or self.now.date()
        start = date.fromisoformat(self.mar()["start"])
        return bool(self.mar_exams(after=today)) and today >= start - timedelta(days=14)

    def m2_course_line(self, c: str, ed: date, plan: dict) -> str:
        rem, base = self.m2_left(c), self.m2_baseline(c)
        days = (ed - self.now.date()).days
        best = self.mar_best(c)
        bits = [f"נותר ~{hours(rem)} מתוך ~{hours(base)}", f"מוכנות {self.m2_readiness(c)}%"]
        if best is not None:
            bits.append(f"מבחן תרגול {best}" + (" ✅" if best >= SEM.MASTERY else ""))
        left = plan["left"].get(c, 0)
        tail = f" · ⚠️ חסר {hours(left)}" if left else ""
        return f"• {COURSES[c]['name']} ({ed.day}.{ed.month}, בעוד {days} ימים): " + " · ".join(bits) + tail

    def m2_text(self) -> str:
        today = self.now.date()
        m = self.mar()
        start = date.fromisoformat(m["start"])
        head = ("🏃 מרתון מבחנים · פעיל" if self.mar_active() else
                f"🏃 מרתון מבחנים · מתחיל ב-{P.HEB_DAY[start.weekday()]} {start.day}.{start.month} (בעוד {(start - today).days} ימים)" if start > today else "🏃 מרתון מבחנים")
        exams = self.mar_exams(after=today - timedelta(days=1))
        if not self.mar_exams(after=today - timedelta(days=1)):
            return "🏃 מרתון מבחנים\nכל המבחנים מאחוריך."
        plan = self.m2_plan()
        lines = [head, "(הערכת שעות לפי מפת החומר. אני מציע, אתה קובע. תיקון: \"במרתון תן למשפט 8 שעות\")"]
        lines += [self.m2_course_line(c, ed, plan) for c, ed in exams]
        bl = sorted(d for d in m["blocked"] if d >= today.isoformat())
        if bl:
            lines += ["", "🎖️ ימים חסומים: " + ", ".join(f"{date.fromisoformat(d).day}.{date.fromisoformat(d).month} ({m['blocked'][d]})" for d in bl[:10])]
        if plan.get("personal_error"):
            lines += ["", "⚠️ לא הצלחתי לקרוא את היומן האישי, אז ההערכה בלי האירועים האישיים שלך."]
        if plan["left"]:
            lines += ["", "⚠️ " + self.m2_deficit_text(plan)]
        elif not plan.get("personal_error"):
            last = plan["exams"][-1][1] if plan["exams"] else None
            note = (f" (היומן האישי ידוע לי עד {plan['personal_until'].day}.{plan['personal_until'].month}, אחריו אני מניח שאתה פנוי)"
                    if last and last > plan["personal_until"] else "")
            lines += ["", "✅ לפי הזמן הפנוי ביומן, הכול נכנס עד המבחנים." + note]
        up = sorted(plan["days"].items())[:6]
        if up:
            lines += ["", "מה מוצע בימים הקרובים:"]
            for d, row in up:
                lines.append(f"• {P.HEB_DAY[d.weekday()]} {d.day}.{d.month}: " + ", ".join(f"{COURSES[c]['name']} {hours(mn)}" for c, mn in row.items()))
        lines += ["", "אפשר לכתוב: \"מילואים 3.1–10.1\", \"יום מנוחה ב-27.1\", \"מבחן תרגול במבוא 85\", \"דף חזרה\"."]
        return "\n".join(lines)

    def m2_deficit_text(self, plan: dict) -> str:
        return ("חסר זמן עד המבחנים: " + ", ".join(f"{COURSES[c]['name']} {hours(n)}" for c, n in plan["left"].items())
                + ". אפשר לשחרר ימים, להוריד דרישה, או להסכים שזה יישאר פער.")

    def m2_today(self, d: date | None = None) -> tuple[list[str], list]:
        """הצעת היום: שורות + כפתורי חלון (sa|...). עודד מאשר בלחיצה."""
        d = d or self.now.date()
        if not self.mar_active(d) or not self.mar_exams(after=d - timedelta(days=1)):
            return [], []
        plan = self.m2_plan(first=d)
        if plan.get("personal_error"):
            return ["🏃 מרתון: לא הצלחתי לקרוא את היומן האישי, אז אני לא מציע חלונות עכשיו."], []
        row = plan["days"].get(d)
        if not row:
            return ["🏃 מרתון: אין לימוד מוצע להיום (או שאין חלון פנוי)." if not plan["windows"].get(d) else "🏃 מרתון: הכול מכוסה ליום הזה."], []
        lines = ["🏃 מרתון · מוצע להיום: " + ", ".join(f"{COURSES[c]['name']} {hours(mn)}" for c, mn in row.items())]
        if plan["left"]:
            lines.append("⚠️ " + self.m2_deficit_text(plan))
        rows = []
        queue = [(c, mn) for c, mn in row.items()]
        for a, b in plan["windows"].get(d, []):
            if not queue:
                break
            c, mn = queue[0]
            m = int(min(mn, (b - a).total_seconds() / 60, 120))
            if m < 25:
                continue
            rows.append([(f"{P.fmt_t(a)} · {m} דק׳ · {COURSES[c]['name']}", f"sa|{d.isoformat()}|{a:%H%M}|{c}|{m}")])
            left = mn - m
            queue[0] = (c, left)
            if left < 25:
                queue.pop(0)
        if rows:
            rows.append([("⏭️ דלג", f"sa|{d.isoformat()}|no")])
        return lines, rows

    def m2_ctx(self) -> dict:
        exams = self.mar_exams(after=self.now.date())
        plan = self.m2_plan() if exams else {"left": {}}
        return {"hours_remaining": {c: round(self.m2_left(c) / 60, 1) for c, _ in exams},
                "readiness_pct": {c: self.m2_readiness(c) for c, _ in exams},
                "deficit_hours": {c: round(n / 60, 1) for c, n in plan["left"].items()}}

    # ------------------------------------------------------------ תיקון דרישה בשעות
    def mar_set_need_conv(self, c: str, n) -> str:
        """עדכון "סשנים" במצב שיחה: מומר לשעות (סשן = 50 דק')."""
        return self.mar_set_hours(c, int(n) * SESSION_MIN / 60)

    def mar_set_hours(self, c: str, h) -> str:
        if c not in COURSES:
            raise ValueError("קורס לא מוכר")
        mins = max(0, min(80 * 60, int(float(h) * 60)))
        self.mar().setdefault("need_min", {})[c] = mins
        return f"🏃 {COURSES[c]['name']}: ~{hours(mins)} במרתון (הערכה כוללת, והנותר לפי מפת החומר)."

    # ------------------------------------------------------------ דף חזרה (PDF)
    def review_sheet_data(self, only: str | None = None) -> dict:
        today = self.now.date()
        exams = self.mar_exams(after=today - timedelta(days=1))
        courses = []
        for c, ed in exams:
            if only and c != only:
                continue
            tp = TOP.live(self.st, c)
            unclear = []
            for r in sorted(self.st.get("recaps", []), key=lambda r: r.get("date", "")):
                if r.get("course") != c or not (r.get("unclear") or "").strip():
                    continue
                names = [TOP.ensure(self.st, c)[i]["name"].split(":")[0][:40] for i in r.get("tids", []) if 0 <= i < len(TOP.ensure(self.st, c))]
                d = date.fromisoformat(r["date"])
                unclear.append({"topic": ", ".join(names), "date": f"{d.day}.{d.month}", "text": r["unclear"].strip()[:160]})
            courses.append({"name": COURSES[c]["name"], "exam": f"{ed.day}.{ed.month}", "days": (ed - today).days,
                            "readiness": self.m2_readiness(c), "best": self.mar_best(c),
                            "topics": [{"name": t["name"].split(":")[0][:50], "state": ("weak" if TOP.is_weak(t) else "reviewed" if t["reviewed"] else
                                                                                         "learned" if t["learned"] else "new"), "rating": t["rating"]} for _, t in tp],
                            "unclear": unclear})
        return {"title": "דף חזרה לפני מבחן", "range": f"נכון ל-{today.day}.{today.month}", "courses": courses}

    def send_review_sheet(self, only: str | None = None) -> str:
        data = self.review_sheet_data(only)
        if not data["courses"]:
            return "אין מבחנים קרובים."
        try:
            from bot.report_pdf import build_review_pdf
            self.tg.send_document(f"דף-חזרה-{self.now.day}-{self.now.month}.pdf", build_review_pdf(data), caption="📝 דף חזרה לפני מבחן")
            return ""
        except Exception as e:  # noqa: BLE001
            self.log.append(f"review pdf: {type(e).__name__}: {str(e)[:100]}")
            lines = ["📝 דף חזרה"]
            for c in data["courses"]:
                lines += ["", f"{c['name']} · מבחן {c['exam']} · מוכנות {c['readiness']}%"]
                lines += [f"• {u['date']} {u['topic']}: {u['text']}" for u in c["unclear"][-6:]] or ["• אין נקודות לא ברורות פתוחות"]
            return "\n".join(lines)
