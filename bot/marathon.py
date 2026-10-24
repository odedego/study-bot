"""
מרתון מבחנים (מסמך האפיון + החלטות 16.10):
  - מתחיל 4 שבועות לפני המבחן הראשון (28.12). אפשר להקדים / לדחות מהבוט.
  - כל קורס צריך מספר סשנים (MARATHON_NEED). החלוקה לימים לפי סדר המבחנים: קודם הימים שבין המבחן הקודם
    למבחן של הקורס ("החלון שלו"), ומה שלא נכנס יורד לימים שלפני כן, מהקרוב לרחוק.
  - כל קורס: קודם סיכומים לפי נושאי הסילבוס (~60%), אחר כך מבחני תרגול (שני סשנים למבחן), ובסוף "חזרה אחרונה".
  - שליטה = 90 ומעלה במבחן תרגול: נשארת רק חזרה אחרונה, והזמן עובר לקורסים אחרים.
  - ימי מילואים / מנוחה = ימים אבודים. עודד מדווח, והחלוקה מתעדכנת.
  - ביומן נכתבים רק 3 ימים קדימה (גמישות). שאר התוכנית וירטואלית: "מרתון" בבוט מציג אותה.
  - אחרי מבחן ממשיכים ישר לקורס הבא, אלא אם עודד ביקש יום מנוחה.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime, time, timedelta

import planner as P
from bot import semester as SEM

COURSES = P.COURSES
COMMIT_DAYS = 3
PRACTICE_PER_DAY = 4


def _dd(d: date) -> str:
    return f"{P.HEB_DAY[d.weekday()]} {d.day}.{d.month}"


class MarathonMixin:
    # ------------------------------------------------------------ state
    def mar(self) -> dict:
        m = self.st.setdefault("marathon", {})
        if m.get("sem") and m["sem"] != SEM.MARATHON_START.isoformat():
            m.clear()             # סמסטר חדש (semester.py הוחלף): מרתון חדש מאפס
        m.setdefault("sem", SEM.MARATHON_START.isoformat())
        m.setdefault("start", SEM.MARATHON_START.isoformat())
        m.setdefault("status", "scheduled")
        ex = self.mar_exams()
        if ex:
            m["end"] = ex[-1][1].isoformat()
            if m["status"] in ("scheduled", "active") and self.now.date() > ex[-1][1]:
                m["status"] = "done"
        for k, v in {"blocked": {}, "need": {}, "scores": {}, "committed": [], "archived_done": {}, "archived_total": {}, "seq": {}}.items():
            m.setdefault(k, v)
        return m

    def mar_exams(self, after: date | None = None) -> list[tuple[str, date]]:
        out = []
        for c in SEM.EXAMS:
            dt = SEM.exam_dt(c, "a")
            if dt and (after is None or dt.date() > after):
                out.append((c, dt.date()))
        return sorted(out, key=lambda x: x[1])

    def mar_active(self, d: date | None = None) -> bool:
        m = self.mar()
        d = d or self.now.date()
        ex = self.mar_exams()
        return m["status"] in ("scheduled", "active") and d.isoformat() >= m["start"] and bool(ex) and d <= ex[-1][1]

    def mar_need(self, c: str) -> int:
        return int(self.mar()["need"].get(c, SEM.MARATHON_NEED.get(c, 0)))

    def mar_count(self, c: str, done_only: bool) -> int:
        m = self.mar()
        n = m["archived_done" if done_only else "archived_total"].get(c, 0)
        for s in self.st["sessions"]:
            if s.get("kind") == "marathon" and s.get("course") == c:
                if done_only and s.get("status") == "done":
                    n += 1
                elif not done_only and s.get("status") in ("planned", "done", "missed"):
                    n += 1
        return n

    def mar_user_deleted(self, c: str) -> int:
        """סשני מרתון שעודד מחק ביומן: החלטה שלו. לא משבצים אותם מחדש במקום אחר."""
        return sum(1 for s in self.st["sessions"] if s.get("kind") == "marathon" and s.get("course") == c
                   and s.get("status") == "dropped" and str(s.get("note", "")).startswith(("נמחק ממך", "בוטל לבקשתך")))

    def mar_best(self, c: str):
        sc = self.mar()["scores"].get(c, [])
        return max((x["s"] for x in sc), default=None)

    def mar_mastered(self, c: str) -> bool:
        b = self.mar_best(c)
        return b is not None and b >= SEM.MASTERY

    # ------------------------------------------------------------ capacity & allocation
    def mar_day_slots(self, d: date, uni, personal, taken, exam_days: set) -> list:
        m = self.mar()
        if d.isoformat() in m["blocked"] or d in exam_days or d.weekday() in P.CONFIG["closed_weekdays"]:
            return []
        prof = P.day_profile(d, uni)
        if prof["type"] == "class_day" or not prof["start"]:
            return []           # ערב אחרי יום שיעורים ארוך: נשאר לחזרה הרגילה
        if prof["type"] == "free":
            end = "16:00" if d.weekday() == P.FRI else P.CONFIG["evening_end_limit"]
            cap = SEM.MARATHON_DAY_CAP - (SEM.MARATHON_SEMESTER_RESERVE if d <= SEM.SEMESTER_END else 0)
            prof = dict(prof, end=P.at(d, P.hm(end)), cap=max(0, cap))
        if d == self.now.date():
            prof = dict(prof, start=max(prof["start"], P._round_up(self.now + timedelta(minutes=30))))
        on_day = sum(1 for s in taken if s.start.date() == d)
        prof = dict(prof, cap=min(prof["cap"], P.day_room(self.st, d, on_day),
                                  max(0, SEM.MARATHON_DAY_CAP - on_day)))   # 7 ביום בסך הכול, כולל חזרות
        return P.free_slots(d, prof, P.busy_blocks(d, uni, personal), taken)

    def mar_split(self, c: str) -> int:
        """כמה מהסשנים של הקורס הם סיכומים (השאר מבחני תרגול וחזרה אחרונה)."""
        return max(1, round(0.6 * (max(self.mar_need(c), 1) - 1)))

    def mar_remaining(self) -> dict:
        """(סיכומים, תרגול) שעוד צריך לשבץ לכל קורס, מעבר למה שבוצע או כבר כתוב ביומן."""
        out = {}
        today = self.now.date()
        for c, ed in self.mar_exams(after=today):
            planned = sum(1 for s in self.st["sessions"] if s.get("kind") == "marathon" and s.get("course") == c
                          and s.get("status") == "planned" and datetime.fromisoformat(s["end"]) > self.now)
            rem = self.mar_need(c) - self.mar_count(c, True) - planned - self.mar_user_deleted(c)
            if self.mar_mastered(c):
                rem = min(rem, 1 - planned if planned < 1 else 0)
                out[c] = (0, max(0, rem))
                continue
            rem = max(0, rem)
            summ = min(rem, max(0, self.mar_split(c) - self.mar_count(c, False)))
            out[c] = (summ, rem - summ)
        return out

    def mar_allocate(self) -> tuple[dict, dict]:
        """
        (day → [course, ...], shortage course → n). ימים שכבר נכתבו ליומן לא מחולקים מחדש.
        תרגול: כמה שיותר קרוב למבחן (החלון שבין המבחן הקודם למבחן הזה, מהסוף להתחלה), ומה שלא נכנס יורד אחורה.
        סיכומים: כמה שיותר מוקדם, מתחילת המרתון, עד 3 מאותו קורס ביום כדי לשלב קורסים.
        """
        m = self.mar()
        uni, _ = self.load_uni()
        personal = self.load_personal(P.week_start(self.now.date()))
        exams = self.mar_exams(after=self.now.date())
        if not exams:
            return {}, {}
        exam_day = dict(exams)
        exam_days = {ed for _, ed in self.mar_exams()}
        first = max(self.now.date(), date.fromisoformat(m["start"]))
        taken = [P._session_from_state(s) for s in self.st["sessions"] if s.get("status") in ("planned", "done", "missed")]
        cap = {}
        d = first
        while d < exams[-1][1]:
            if d.isoformat() not in m["committed"]:
                cap[d] = len(self.mar_day_slots(d, uni, personal, taken, exam_days))
            d += timedelta(days=1)
        rem = self.mar_remaining()
        plan: dict[date, list] = {d: [] for d in cap}
        short = Counter()
        free = lambda day: cap[day] - len(plan[day])  # noqa: E731
        # 1) תרגול וחזרה אחרונה: צמוד למבחן
        prev = first - timedelta(days=1)
        for c, ed in exams:
            need = rem.get(c, (0, 0))[1]
            own = sorted((d for d in cap if prev < d < ed), reverse=True)
            before = sorted((d for d in cap if d <= prev), reverse=True)
            # קודם עד 4 ביום (כדי לא לדחוס הכול ליום לפני המבחן), ואם לא מספיק: כל המקום שנשאר
            for limit in (PRACTICE_PER_DAY, None):
                for d in own + before:
                    if need <= 0:
                        break
                    room = free(d) if limit is None else min(free(d), limit - plan[d].count(c))
                    take = min(need, room)
                    if take > 0:
                        plan[d] += [c] * take
                        need -= take
            short[c] += need
            prev = ed
        # 2) סיכומים: מוקדם ככל האפשר, משולבים
        left = {c: rem.get(c, (0, 0))[0] for c, _ in exams}
        for limit in (3, PRACTICE_PER_DAY, None):     # קודם משלבים קורסים, ורק אם חסר מקום מעמיסים יותר מאותו קורס
            for d in sorted(cap):
                per = Counter(plan[d])
                while free(d) > 0:
                    pick = next((c for c, _ in exams if left[c] > 0 and d < exam_day[c] and (limit is None or per[c] < limit)), None)
                    if not pick:
                        break
                    plan[d].append(pick)
                    per[pick] += 1
                    left[pick] -= 1
        for c, n in left.items():
            short[c] += n
        order = {c: i for i, (c, _) in enumerate(exams)}
        return ({d: sorted(v, key=lambda c: order[c]) for d, v in plan.items() if v},
                {c: n for c, n in short.items() if n > 0})

    # ------------------------------------------------------------ labels
    def mar_topics(self, c: str) -> list[str]:
        if c in SEM.TOPICS:
            return SEM.TOPICS[c]
        if c in SEM.WEEKLY_TOPICS:
            out = []
            for _, t in sorted(SEM.WEEKLY_TOPICS[c].items()):
                if t != "חזרה" and t not in out:
                    out.append(t)
            return out
        return []

    def mar_label(self, c: str, i: int) -> str:
        """i = מספר הסשן של הקורס במרתון (מ-1)."""
        n = max(self.mar_need(c), 1)
        name = COURSES[c]["name"]
        if self.mar_mastered(c) or i >= n:
            return f"🏃 {name} · 🏁 חזרה אחרונה וטעויות"
        n_sum = max(1, round(0.6 * (n - 1)))
        if i <= n_sum:
            topics = self.mar_topics(c)
            if topics:
                t = topics[min(len(topics) - 1, (i - 1) * len(topics) // n_sum)]
                return f"🏃 {name} · 📖 סיכום: {t}"
            return f"🏃 {name} · 📖 סיכום חומר ({i}/{n_sum})"
        j = i - n_sum
        return f"🏃 {name} · 🎯 מבחן תרגול {math.ceil(j / 2)} (חלק {2 - j % 2}/2)"

    # ------------------------------------------------------------ calendar (3 days ahead)
    def mar_commit(self) -> list[str]:
        """כותב ליומן את ימי המרתון הקרובים שעוד לא נכתבו. מחזיר שורות להודעה."""
        if not self.mar_active():
            return []
        why = self.plan_guard()
        if why:
            return [f"⚠️ מרתון: לא שיבצתי היום ({why[:80]})"]
        m = self.mar()
        if m["status"] == "scheduled":
            m["status"] = "active"
        plan, _ = self.mar_allocate()
        uni, _ = self.load_uni()
        personal = self.load_personal(P.week_start(self.now.date()))
        exam_days = {ed for _, ed in self.mar_exams()}
        taken = [P._session_from_state(s) for s in self.st["sessions"] if s.get("status") in ("planned", "done", "missed")]
        made = Counter()
        today = self.now.date()
        for k in range(COMMIT_DAYS):
            d = today + timedelta(days=k)
            if d.isoformat() in m["committed"] or d.isoformat() < m["start"]:
                continue
            courses = plan.get(d, [])
            slots = self.mar_day_slots(d, uni, personal, taken, exam_days)
            for c, (a, b) in zip(courses, slots):
                i = self.mar_count(c, False) + 1
                m["seq"][c] = m["seq"].get(c, 0) + 1
                key = self.unique_key(f"marathon:{c}:{d.isoformat()}:s{m['seq'][c]}")
                sess = P.Session(key, "marathon", c, [c], a, b, None, note="מרתון מבחנים. אחרי הסשן סמן ✅ בסיכום היומי.",
                                 label=self.mar_label(c, i))
                js = sess.to_json()
                eid = self.create_event(js)
                self.st["sessions"].append({k2: js[k2] for k2 in ("key", "kind", "course", "courses", "start", "end", "lesson_day", "note", "label")}
                                           | {"status": "planned", "eventId": eid})
                taken.append(sess)
                made[d] += 1
            m["committed"].append(d.isoformat())
        m["committed"] = sorted(set(m["committed"]))[-60:]
        return [f"➕ מרתון: נוספו ליומן {n} סשנים ב-{_dd(d)}" for d, n in sorted(made.items())]

    def mar_drop_days(self, days: list[date], reason: str, course: str | None = None) -> list[dict]:
        """רק מבקשה של עודד (ימים חסומים, או "לפנות" אחרי שליטה): מוחק בלי לשאול, והקורא מדווח מה ירד."""
        out = []
        for s in self.st["sessions"]:
            if s.get("kind") == "marathon" and s.get("status") == "planned" and datetime.fromisoformat(s["start"]) > self.now \
                    and date.fromisoformat(s["start"][:10]) in days and (course is None or s.get("course") == course):
                s["status"] = "dropped"
                s["note"] = reason
                if s.get("eventId"):
                    try:
                        self.cal.delete(self.reviews_cal, s["eventId"])
                    except Exception as e:  # noqa: BLE001
                        self.log.append(f"marathon drop: {e}")
                out.append(s)
        return out

    # ------------------------------------------------------------ actions (מהודעות חופשיות)
    def mar_block(self, start: str, end: str | None, reason: str) -> str:
        a = date.fromisoformat(start)
        b = date.fromisoformat(end) if end else a
        if b < a:
            a, b = b, a
        if (b - a).days > 60:
            raise ValueError("טווח ארוך מדי")
        m = self.mar()
        days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
        for d in days:
            m["blocked"][d.isoformat()] = reason or "חסום"
        dropped = self.mar_drop_days(days, f"{reason or 'חסום'} (דיווחת)")
        span = _dd(a) if a == b else f"{_dd(a)} עד {_dd(b)}"
        msg = f"🎖️ רשמתי: {span} {reason or 'חסום'}. בימים האלה אין סשני מרתון."
        if dropped:
            msg += f"\n🗑 עודכן: הורדתי מהיומן {len(dropped)} סשנים:\n" + "\n".join(self.drop_lines(dropped)[:8]) \
                + (f"\n(ועוד {len(dropped) - 8})" if len(dropped) > 8 else "")
        _, short = self.mar_allocate()
        if short and date.fromisoformat(m["start"]) <= b + timedelta(days=60):
            msg += "\n⚠️ " + self.mar_short_text(short)
        return msg

    def mar_unblock(self, start: str, end: str | None) -> str:
        a = date.fromisoformat(start)
        b = date.fromisoformat(end) if end else a
        m = self.mar()
        n = 0
        for i in range((b - a).days + 1):
            n += m["blocked"].pop((a + timedelta(days=i)).isoformat(), None) is not None
            m["committed"] = [x for x in m["committed"] if x != (a + timedelta(days=i)).isoformat()]
        return f"👍 שחררתי {n} ימים. אשבץ בהם מרתון כשיגיע הזמן."

    def mar_score(self, c: str, score, d: str | None) -> str:
        if c not in COURSES:
            raise ValueError("קורס לא מוכר")
        s = int(score)
        if not 0 <= s <= 100:
            raise ValueError("ציון בין 0 ל-100")
        self.mar()["scores"].setdefault(c, []).append({"d": d or self.now.date().isoformat(), "s": s})
        name = COURSES[c]["name"]
        if s >= SEM.MASTERY:
            later = self._mar_future(c)
            if later:
                self.deferred(lambda: self.tg.send(
                    f"ביומן כבר יש {len(later)} סשני מרתון של {name} בימים הקרובים. לפנות אותם לקורסים אחרים?",
                    [[("🔄 כן, לפנות", f"mar|free|{c}"), ("להשאיר", "noop")]]))
            return (f"🎉 {s} במבחן תרגול ב{name}: שליטה. מעכשיו נשארת רק חזרה אחרונה לפני המבחן, "
                    f"ושאר הזמן עובר לקורסים אחרים.")
        return f"📈 {name}: {s} במבחן תרגול (שליטה מ-{SEM.MASTERY}). ממשיכים לתרגל."

    def _mar_future(self, c: str) -> list[dict]:
        today = self.now.date().isoformat()
        return [s for s in self.st["sessions"] if s.get("kind") == "marathon" and s.get("course") == c
                and s.get("status") == "planned" and s["start"][:10] > today]

    def mar_free(self, c: str) -> str:
        """אחרי שליטה: מפנה את סשני הקורס מהימים הקרובים (חוץ מהיום) ומשבץ בהם מחדש."""
        why = self.plan_guard()
        if why:
            return f"⚠️ לא פיניתי: אני לא מצליח לקרוא את המערכת עכשיו ({why[:60]}). נסה שוב עוד מעט."
        later = self._mar_future(c)
        days = sorted({date.fromisoformat(s["start"][:10]) for s in later})
        gone = self.mar_drop_days(days, "פונה אחרי שליטה", c) if days else []
        m = self.mar()
        m["committed"] = [x for x in m["committed"] if date.fromisoformat(x) not in days]
        made = self.mar_commit()
        if not gone:
            return "🔄 לא היו סשנים לפנות."
        return (f"🔄 פיניתי {len(gone)} סשנים ושיבצתי מחדש.\n🗑 עודכן: ירדו מהיומן:\n" + "\n".join(self.drop_lines(gone)[:8])
                + ("\n" + "\n".join(x for x in made if not x.startswith("⚠️")) if made else ""))

    def mar_set_need(self, c: str, n) -> str:
        if c not in COURSES:
            raise ValueError("קורס לא מוכר")
        self.mar()["need"][c] = max(0, min(80, int(n)))
        return f"🏃 {COURSES[c]['name']}: {self.mar_need(c)} סשנים במרתון."

    def mar_set_start(self, d: str) -> str:
        day = date.fromisoformat(d)
        ex = self.mar_exams()
        if ex and day >= ex[0][1]:
            raise ValueError("זה אחרי המבחן הראשון")
        m = self.mar()
        m["start"] = max(day, self.now.date()).isoformat()
        if m["status"] in ("off", "done"):
            m["status"] = "scheduled"
        return f"🏃 המרתון מתחיל ב-{_dd(date.fromisoformat(m['start']))}."

    # ------------------------------------------------------------ text
    def mar_short_text(self, short: dict) -> str:
        return "חסרים סשנים עד המבחנים: " + ", ".join(f"{COURSES[c]['name']} {n}" for c, n in short.items()) + \
            ". אפשר להוריד דרישה (\"במרתון תן למשפט 8\") או לשחרר ימים."

    def mar_course_line(self, c: str, ed: date) -> str:
        done, need = self.mar_count(c, True), self.mar_need(c)
        best = self.mar_best(c)
        days = (ed - self.now.date()).days
        tail = ""
        if best is not None:
            tail = f" · מבחן תרגול {best}" + (" ✅" if best >= SEM.MASTERY else "")
        return f"• {COURSES[c]['name']} ({ed.day}.{ed.month}, בעוד {days} ימים): {done}/{need}{tail}"

    def marathon_text(self) -> str:
        m = self.mar()
        start = date.fromisoformat(m["start"])
        today = self.now.date()
        head = ("🏃 מרתון מבחנים · פעיל" if self.mar_active() else
                f"🏃 מרתון מבחנים · מתחיל ב-{_dd(start)} (בעוד {(start - today).days} ימים)" if start > today else "🏃 מרתון מבחנים")
        lines = [head]
        ex = [(c, ed) for c, ed in self.mar_exams(after=today - timedelta(days=1))]
        if not ex:
            return head + "\nכל המבחנים מאחוריך."
        lines += [self.mar_course_line(c, ed) for c, ed in ex]
        plan, short = self.mar_allocate()
        if m["blocked"]:
            bl = sorted(d for d in m["blocked"] if d >= today.isoformat())
            if bl:
                lines += ["", "🎖️ ימים חסומים: " + ", ".join(f"{_dd(date.fromisoformat(d))} ({m['blocked'][d]})" for d in bl[:10])]
        if short:
            lines += ["", "⚠️ " + self.mar_short_text(short)]
        upcoming = sorted(plan.items())[:10]
        if upcoming:
            lines += ["", "התוכנית הקרובה (נכתבת ליומן 3 ימים מראש):"]
            for d, cs in upcoming:
                cnt = Counter(cs)
                lines.append(f"• {_dd(d)}: " + ", ".join(f"{COURSES[c]['name']} ×{n}" for c, n in cnt.items()))
        lines += ["", "אפשר לכתוב: \"מילואים 3.1–10.1\", \"יום מנוחה ב-27.1\", \"מבחן תרגול במבוא 85\", \"במרתון תן למשפט 8\"."]
        return "\n".join(lines)

    def marathon_morning(self) -> list[str]:
        if not self.mar_active():
            return []
        today = self.now.date()
        ex = [(c, ed) for c, ed in self.mar_exams(after=today - timedelta(days=1)) if ed >= today]
        if not ex:
            return []
        c0, e0 = ex[0]
        todays = Counter(s["course"] for s in self.st["sessions"] if s.get("kind") == "marathon"
                         and s["start"][:10] == today.isoformat() and s.get("status") == "planned")
        lines = [f"🏃 מרתון · {COURSES[c0]['name']} " + ("היום! בהצלחה 🍀" if e0 == today else f"בעוד {(e0 - today).days} ימים")]
        if todays:
            lines.append("היום: " + ", ".join(f"{COURSES[c]['name']} ×{n}" for c, n in todays.items()))
        _, short = self.mar_allocate()
        if short:
            lines.append("⚠️ " + self.mar_short_text(short))
        return lines

    def marathon_ctx(self) -> dict:
        m = self.mar()
        return {"start": m["start"], "active": self.mar_active(), "need": {c: self.mar_need(c) for c in SEM.MARATHON_NEED},
                "done": {c: self.mar_count(c, True) for c in SEM.MARATHON_NEED}, "best_practice": {c: self.mar_best(c) for c in SEM.MARATHON_NEED},
                "blocked": m["blocked"]}
