"""
ריקאפ כשיחה (שלב E של v2). פעיל בכל מצב חוץ מ-v1.

זרימה אחרי שעודד ענה "הייתי" ודירג הבנה:
  דירוג 4 או 5:  שאלה אחת: "מה עברתם?" (עם הצעה מהסילבוס אם יש)
  דירוג 3 ומטה: "מה לא הבנת?" ואחריה "מה הכי הפריע?"
  "דלג" זמין תמיד ומדלג על כל שאלות הקורס. שאלה אחת בכל הודעה, הבאה רק אחרי שענה או דילג.
  כשכל הקורסים נסגרו: "מתי יהיה לך נוח ללמוד היום?" עם המלצה וחלונות פנויים. עודד בוחר, הבוט קובע ביומן החזרות.
הבוט לא קובע זמן לבד. אין הצעה בלי שאלה. מה שלא נענה פשוט פג (ב-21:30 המצב נסגר בשקט).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import planner as P
from bot import semester as SEM
from bot import suggest as SUG

COURSES = P.COURSES

UNCLEAR_OPTS = {"u1": "מושג או הגדרה", "u2": "תרגיל או חישוב", "u3": "הכול בערבוביה"}
CAUSE_OPTS = {"k1": "הקצב מהיר מדי", "k2": "ההסבר לא היה ברור", "k3": "חסר לי רקע מקודם", "k4": "לא הייתי מרוכז"}
NO_TIME_LABEL = "🚫 לא היום"


def _dd(d: date) -> str:
    return f"{P.HEB_DAY[d.weekday()]} {d.day}.{d.month}"


class ConvMixin:
    # ------------------------------------------------------------ מצב
    @property
    def conv(self) -> bool:
        return self.st["bot"].get("mode", "freeze") != "v1"

    def rq(self, ds: str | None = None) -> dict | None:
        r = self.st["bot"].get("rq")
        if not r or (ds and r.get("date") != ds):
            return None
        return r

    def rq_open(self, ds: str, courses: list[str]):
        cur = self.rq(ds)
        if cur:                       # ריקאפ חוזר באותו יום: ממשיכים את אותה שיחה
            cur["courses"] = list(courses)
            return
        self.st["bot"]["rq"] = {"date": ds, "courses": list(courses), "queue": [], "cur": None, "asked": [],
                               "final": False, "closed": False}

    def rq_for_llm(self):
        r = self.rq()
        cur = r["cur"] if r and not r["closed"] else None
        if not cur or cur.get("at", "") < P.iso(self.now - timedelta(hours=10)):
            return None
        return {"course": cur["c"], "date": r["date"], "kind": cur["k"], "asked": cur["text"].split("\n")[0]}

    def rq_close(self, ds: str | None = None):
        r = self.rq(ds)
        if r:
            r["closed"] = True
            r["queue"] = []
            r["cur"] = None

    # ------------------------------------------------------------ תור שאלות
    def rq_on_rating(self, ds: str, c: str, rating: int, quick: bool = False):
        r = self.rq(ds)
        if not r or r["closed"]:
            return
        if quick:
            return self.deferred(lambda: self.rq_check_final(ds))
        if c not in r["courses"]:
            return
        kinds = ["topics"] if rating >= 4 else ["unclear", "cause"]
        for k in kinds:
            tag = f"{c}|{k}"
            if tag in r["asked"]:
                continue
            r["asked"].append(tag)
            r["queue"].append({"c": c, "k": k})
        self.deferred(lambda: self.rq_ask_next(ds))

    def rq_suggestion(self, c: str, ds: str) -> str:
        return SEM.topics_for_week(P.week_start(date.fromisoformat(ds))).get(c, "")

    def rq_ask_next(self, ds: str):
        r = self.rq(ds)
        if not r or r["closed"] or r["cur"]:
            return
        if not r["queue"]:
            return self.rq_check_final(ds)
        q = r["queue"][0]
        c, k = q["c"], q["k"]
        name = COURSES[c]["name"]
        sug = self.rq_suggestion(c, ds)
        pre = f"rqa|{ds}|{c}|"
        skip = ("⏭️ דלג", pre + "skip")
        if k == "topics":
            if sug:
                text = f"{name}: מה עברתם היום?\nלפי הסילבוס: {sug}"
                rows = [[("✔️ כן, זה", pre + "ok"), ("✏️ משהו אחר", pre + "w")], [skip]]
            else:
                text = f"{name}: מה עברתם היום? כתוב לי בקצרה."
                rows = [[skip]]
        elif k == "unclear":
            text = f"{name}: מה לא הבנת?"
            first = [("🧩 " + UNCLEAR_OPTS["u1"], pre + "u1"), ("🧮 " + UNCLEAR_OPTS["u2"], pre + "u2")]
            rows = [first, [("🌫️ " + UNCLEAR_OPTS["u3"], pre + "u3"), ("✏️ אכתוב", pre + "w")]]
            if sug:
                rows.insert(0, [("📌 " + sug[:30], pre + "u0")])
            rows.append([skip])
        else:
            text = f"{name}: מה הכי הפריע?"
            rows = [[("🏃 " + CAUSE_OPTS["k1"], pre + "k1"), ("🌀 " + CAUSE_OPTS["k2"], pre + "k2")],
                    [("📉 " + CAUSE_OPTS["k3"], pre + "k3"), ("😴 " + CAUSE_OPTS["k4"], pre + "k4")],
                    [("✏️ אכתוב", pre + "w"), skip]]
        mid = self.tg.send(text, rows)
        r["queue"].pop(0)
        r["cur"] = {"c": c, "k": k, "mid": mid, "text": text, "sug": sug, "at": P.iso(self.now)}

    def _rq_finish_cur(self, shown: str):
        r = self.rq()
        cur = r["cur"] if r else None
        if not cur:
            return
        try:
            self.tg.edit(cur["mid"], cur["text"] + "\n" + shown, None)
        except Exception as e:  # noqa: BLE001
            self.log.append(f"rq edit: {type(e).__name__}")
        r["cur"] = None

    def on_rq_callback(self, parts: list[str]) -> str:
        """rqa|תאריך|קורס|קוד. מחזיר טקסט קצר לתשובת הכפתור."""
        _, ds, c, code = parts
        r = self.rq(ds)
        cur = r["cur"] if r else None
        if not r or r["closed"] or not cur or cur["c"] != c:
            return "כבר טופל"
        if code == "skip":
            r["queue"] = [q for q in r["queue"] if q["c"] != c]
            self._rq_finish_cur("⏭️ דולג")
            self.deferred(lambda: self.rq_ask_next(ds))
            return "דולג"
        if code == "w":
            cur["await_text"] = True
            return "כתוב לי בהודעה"
        k = cur["k"]
        if k == "topics" and code == "ok":
            self.add_recap(ds, c, topics=cur.get("sug", ""))
            shown = f"✔️ {cur.get('sug', '')}"
        elif k == "unclear" and code in UNCLEAR_OPTS:
            self.add_recap(ds, c, unclear=UNCLEAR_OPTS[code])
            shown = f"✔️ {UNCLEAR_OPTS[code]}"
        elif k == "unclear" and code == "u0":
            self.add_recap(ds, c, unclear=f"הנושא: {cur.get('sug', '')}")
            shown = f"✔️ {cur.get('sug', '')}"
        elif k == "cause" and code in CAUSE_OPTS:
            self.add_recap(ds, c, cause=CAUSE_OPTS[code])
            shown = f"✔️ {CAUSE_OPTS[code]}"
        else:
            return "לא הבנתי"
        self._rq_finish_cur(shown)
        self.deferred(lambda: self.rq_ask_next(ds))
        return "נרשם"

    def rq_skip_course(self, ds: str, c: str):
        r = self.rq(ds)
        if not r or r["closed"]:
            return
        r["queue"] = [q for q in r["queue"] if q["c"] != c]
        if r["cur"] and r["cur"]["c"] == c:
            self._rq_finish_cur("⏭️ דולג")
        self.deferred(lambda: self.rq_ask_next(ds))

    def rq_text_progress(self, ds: str, c: str, topics: str = "", unclear: str = "", cause: str = ""):
        """תשובה חופשית (דרך Claude) לשאלה הפתוחה: אם היא מכסה את מה שנשאל, ממשיכים לשאלה הבאה."""
        r = self.rq(ds)
        cur = r["cur"] if r else None
        if not r or r["closed"] or not cur or cur["c"] != c:
            return
        got = topics or unclear or cause
        if not got:
            return
        r["queue"] = [q for q in r["queue"] if not (q["c"] == c and q["k"] == "cause" and cause)]
        self._rq_finish_cur("✔️ " + got[:60])
        self.deferred(lambda: self.rq_ask_next(ds))

    # ------------------------------------------------------------ סיום והשאלה על הזמן
    def rq_resolved(self, ds: str, r: dict) -> bool:
        if r["cur"] or r["queue"]:
            return False
        for c in r["courses"]:
            att = self.st["bot"]["attend"].get(f"{c}|{ds}")
            if att is None:
                return False
            if att == "present" and not any(x["date"] == ds and x["course"] == c and x.get("rating") for x in self.st["recaps"]):
                return False
        return True

    def rq_check_final(self, ds: str):
        r = self.rq(ds)
        if not r or r["closed"] or r["final"] or not self.rq_resolved(ds, r):
            return
        self.rq_final(ds)
        r["final"] = True

    def study_windows(self, day: date) -> list:
        try:
            uni, _ = self.load_uni()
            personal = self.load_personal(P.week_start(day))
            if getattr(self, "_feed_error", None) or getattr(self, "_personal_error", None):
                return []
            return SUG.free_windows(day, uni, personal, self.st, self.now)
        except Exception as e:  # noqa: BLE001
            self.log.append(f"windows: {type(e).__name__}")
            return []

    def rq_final(self, ds: str):
        today = date.fromisoformat(ds)
        r = self.rq()
        uni, _ = self.load_uni()
        recs = SUG.recommend(self.st, today, uni, self.now)
        course = recs[0]["course"] if recs else None
        if not course:
            course = next((c for c in r["courses"] if self.st["bot"]["attend"].get(f"{c}|{ds}") == "present"), None)
        wins = self.study_windows(today)
        lines = ["מתי יהיה לך נוח ללמוד היום?"]
        if recs:
            lines.append("מומלץ לחזק: " + "; ".join(f"{COURSES[x['course']]['name']} ({', '.join(x['why'][:2])})" for x in recs))
        rows = []
        if course and wins:
            name = COURSES[course]["name"]
            for a, b in wins[:3]:
                m = min(SUG.minutes_for(course, self.st), int((b - a).total_seconds() // 60))
                rows.append([(f"{P.fmt_t(a)} · {m} דק׳ · {name}", f"sa|{ds}|{a:%H%M}|{course}|{m}")])
        elif not wins:
            lines.append("לא רואה חלון פנוי להיום. אפשר לכתוב לי שעה אם בכל זאת.")
        rows.append([(NO_TIME_LABEL, f"sa|{ds}|no"), ("⏭️ דלג", f"sa|{ds}|no")])
        self.tg.send("\n".join(lines), rows)

    # ------------------------------------------------------------ קביעת סשן לפי בחירתו
    def _drop_offer(self, mid):
        """אחרי בחירה: מורידים את כפתורי ההצעה. בסיכום היומי נשארים כפתורי הסימון."""
        meta = self.st["bot"]["msgs"].get(str(mid), {})
        if meta.get("type") == "daily":
            meta["offer"] = []
            self.refresh_daily(mid)
        else:
            self.tg.edit_buttons(mid, [])

    def on_sa_callback(self, parts: list[str], mid) -> str:
        ds = parts[1]
        if parts[2] == "no":
            self.rq_close(ds)
            self._drop_offer(mid)
            return "בסדר, יום טוב"
        _, _, hhmm, c, m = parts
        d = date.fromisoformat(ds)
        start = P.at(d, datetime.strptime(hhmm, "%H%M").time())
        msg = self.book_study(c, start, int(m), "button")
        if msg.startswith("✅"):
            self.rq_close(ds)
            self._drop_offer(mid)
        self.tg.send(msg)
        return "נקבע" if msg.startswith("✅") else "לא נקבע"

    def book_study(self, c: str, start: datetime, minutes: int | None, src: str, late_ok: bool = False) -> str:
        """עודד החליט מתי: הבוט רושם ביומן החזרות ומדווח. לא משבץ ולא מזיז בלי בקשה."""
        if c not in COURSES:
            raise ValueError("קורס לא ידוע")
        try:
            minutes = int(minutes) if minutes else SUG.minutes_for(c, self.st)
        except (TypeError, ValueError):
            return "לא הבנתי כמה דקות. כתוב לי מספר, למשל 30."
        minutes = max(10, min(minutes, 240))
        end = start + timedelta(minutes=minutes)
        if start.hour < 6 or end.date() != start.date():
            if not late_ok or end.date() != start.date():
                return "השעה לא סבירה ללימוד (לפני 06:00 או אחרי חצות). כתוב לי שעה אחרת."
        name = COURSES[c]["name"]
        if start <= self.now:
            return f"השעה {P.fmt_t(start)} כבר עברה. כתוב לי שעה מאוחרת יותר."
        if end > P.at(start.date(), P.hm(P.CONFIG["hard_end"])) and not late_ok:
            return f"זה יסתיים אחרי {P.CONFIG['hard_end']}. כתוב לי שעה מוקדמת יותר, או שתבקש במפורש ללמוד מאוחר."
        why = self.plan_guard()
        if why:
            return f"לא יכול לקבוע עכשיו, אני לא מצליח לקרוא את היומן ({str(why)[:60]}). נסה שוב עוד כמה דקות."
        uni, _ = self.load_uni()
        personal = self.load_personal(P.week_start(start.date()))
        for x in P.busy_blocks(start.date(), uni, personal):
            if x[0] < end and start < x[1]:
                return f"זה מתנגש עם {'שיעור' if x[2] == 'lesson' else 'אירוע ביומן'} ({P.fmt_t(x[0])} עד {P.fmt_t(x[1])}). איזו שעה מתאימה?"
        for s in self.st["sessions"]:
            if s.get("status") == "planned" and datetime.fromisoformat(s["start"]) < end and start < datetime.fromisoformat(s["end"]):
                if s.get("src") in ("button", "text") and s.get("course") == c and datetime.fromisoformat(s["start"]) == start:
                    return f"✅ כבר נקבע: {name}, {s['start'][11:16]}"
                return f"כבר יש סשן ב-{s['start'][11:16]}. כתוב לי אם להזיז אותו."
        key = self.unique_key(f"study:{c}:{start:%Y%m%dT%H%M}")
        sess = P.Session(key, "extra", c, [c], start, end, None, note="נקבע לפי בחירתך", label=f"📚 {name}")
        js = sess.to_json()
        eid = self.create_event(js)
        self.st["sessions"].append({k: js[k] for k in ("key", "kind", "course", "courses", "start", "end", "lesson_day", "note", "label")}
                                   | {"status": "planned", "eventId": eid, "min": minutes, "src": src})
        return f"✅ קבעתי: {name}, {_dd(start.date())} {P.fmt_t(start)} עד {P.fmt_t(end)}"

    def log_done_session(self, s: dict):
        """סשן שסומן כבוצע נרשם ביומן הלימוד (פעם אחת לכל סשן), כדי שהפער יסגר."""
        if not self.conv or not s.get("course") or s.get("status") != "done":
            return
        if any(r.get("session") == s["key"] for r in self.st.get("log", [])):
            return
        mins = s.get("min") or int((datetime.fromisoformat(s["end"]) - datetime.fromisoformat(s["start"])).total_seconds() // 60)
        SUG.log_study(self.st, s["start"][:10], s["course"], mins, None, "session", P.iso(self.now), s["key"])

    def studied_action(self, a: dict) -> str:
        c = a.get("course")
        if c not in COURSES:
            raise ValueError("קורס לא ידוע")
        d = a.get("date") or self.now.date().isoformat()
        date.fromisoformat(d)
        try:
            m = a.get("minutes")
            m = int(m) if m not in (None, "", 0) else None
        except (TypeError, ValueError):
            return "לא הבנתי כמה דקות למדת. כתוב לי מספר."
        SUG.log_study(self.st, d, c, m, a.get("topic"), "text", P.iso(self.now))
        return f"✅ רשמתי: {COURSES[c]['name']}" + (f", {m} דק׳" if m else "")

    def study_at_action(self, a: dict) -> str:
        c = a.get("course")
        raw = str(a.get("start") or "")
        if "T" not in raw and " " not in raw.strip():
            return "באיזו שעה? כתוב לי יום ושעה ואקבע."
        try:
            start = datetime.fromisoformat(raw.strip())
        except ValueError:
            return "לא הבנתי את השעה. כתוב לי יום ושעה, למשל מחר 17:00."
        if start.tzinfo is None:
            start = start.replace(tzinfo=P.TZ)
        start = start.astimezone(P.TZ)
        return self.book_study(c, start, a.get("minutes"), "text", late_ok=bool(a.get("late_ok")))

    # ------------------------------------------------------------ הודעות קבועות (שלב F)
    def week_minutes(self, ws: date) -> dict:
        """דקות לימוד בפועל לפי קורס בשבוע שמתחיל ב-ws (מיומן הלימוד)."""
        lo, hi = ws.isoformat(), (ws + timedelta(days=7)).isoformat()
        out: dict[str, int] = {}
        for r in self.st.get("log", []):
            if lo <= r.get("date", "") < hi and r.get("course") in COURSES:
                out[r["course"]] = out.get(r["course"], 0) + int(r.get("min") or 0)
        return out

    @staticmethod
    def hm_text(minutes: int) -> str:
        h, m = divmod(int(minutes), 60)
        return (f"{h} שעות" if h > 1 else "שעה" if h == 1 else "") + (" ו" if h and m else "") + (f"{m} דק׳" if m else "") or "0 דק׳"

    def pick_course(self, today: date, uni) -> str | None:
        recs = SUG.recommend(self.st, today, uni, self.now)
        if recs:
            return recs[0]["course"]
        up = [c for c, _dt, _n in SEM.upcoming_exams(today) if c in COURSES]
        return up[0] if up else None

    def evening_block(self, today: date, uni, personal) -> tuple[list[str], list]:
        """בסיכום 21:30: מחר יום בלי לימודים (או חצי יום פנוי, כמו חמישי עד 13:45): חלונות והשאלה מתי נוח."""
        o = SUG.evening_offer(today, uni, personal, self.st, self.now)
        if not o:
            return [], []
        tm = date.fromisoformat(o["date"])
        c = self.pick_course(today, uni)
        recs = SUG.recommend(self.st, today, uni, self.now)
        head = "מחר אפשר ללמוד עד 13:45 (השיעורים מתחילים אז)." if o["kind"] == "half" else "מחר אין לימודים."
        lines = ["", f"{head} מתי נוח לך ללמוד?"]
        if recs:
            lines.append("מומלץ לחזק: " + "; ".join(f"{COURSES[x['course']]['name']} ({', '.join(x['why'][:2])})" for x in recs))
        rows = []
        if c:
            for a, b in o["windows"]:
                a, b = datetime.fromisoformat(a), datetime.fromisoformat(b)
                m = min(SUG.minutes_for(c, self.st), int((b - a).total_seconds() // 60))
                rows.append([(f"{P.fmt_t(a)} · {m} דק׳ · {COURSES[c]['name']}", f"sa|{tm.isoformat()}|{a:%H%M}|{c}|{m}")])
        rows.append([("⏭️ דלג", f"sa|{tm.isoformat()}|no")])
        return lines, rows

    def morning_reco_line(self, today: date, uni) -> str:
        if today.weekday() in P.CONFIG["closed_weekdays"]:
            return ""
        recs = SUG.recommend(self.st, today, uni, self.now, limit=1)
        if not recs:
            return ""
        x = recs[0]
        return f"💪 כדאי לחזק היום: {COURSES[x['course']]['name']} ({', '.join(x['why'][:2])})"

    def conv_weak_text(self, today: date) -> str:
        """הודעת חיזוק בשני וחמישי: מה לחזק ולמה. בלי הצעת שיבוץ, שואלת מתי נוח."""
        uni, _ = self.load_uni()
        lines = P.weak_lines(self.st, today)
        gaps = SUG.open_gaps(self.st, today, uni, self.now)
        if not lines and not gaps:
            return ""
        out = ["💪 מה צריך חיזוק (שבועיים אחרונים)"] + lines
        if gaps:
            names = []
            for g in gaps[:6]:
                d = date.fromisoformat(g["day"])
                names.append(f"{COURSES[g['course']]['name']} {d.day}.{d.month}")
            out += ["", "פערים פתוחים (שיעור בלי לימוד אחריו): " + ", ".join(names)]
        out += ["", "מתי נוח לך לחזק? כתוב לי יום ושעה ואקבע."]
        return "\n".join(out)
