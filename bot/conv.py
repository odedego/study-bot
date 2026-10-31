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
from bot import topics as TOP

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

    def material_for_llm(self) -> dict:
        """מספרי הנושאים לכל קורס, תמיד בהקשר: כך Claude משייך ומתקן גם מחוץ לשאלת ריקאפ."""
        return {c: [[i, t["name"][:32]] for i, t in TOP.live(self.st, c)] for c in P.COURSE_ORDER if TOP.live(self.st, c)}

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
        sug_i = TOP.index_of(self.st, c, self.rq_suggestion(c, ds))
        if rating >= 4:
            kinds = ["topics"]
        elif sug_i is not None:
            self.add_recap(ds, c, topic_ids=[sug_i])          # נושא השבוע לפי הסילבוס: משויך בלי לשאול
            kinds = ["unclear", "cause"]
        elif TOP.next_unlearned(self.st, c, 1):
            kinds = ["topics", "unclear", "cause"]            # קודם מה עברתם, כדי שהחולשה תיצמד לנושא
        else:
            kinds = ["unclear", "cause"]
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
        dd_ = date.fromisoformat(ds)
        when = "היום" if ds == self.now.date().isoformat() else f"ב-{dd_.day}.{dd_.month}"
        pre = f"rqa|{ds}|{c}|"
        skip = ("⏭️ דלג", pre + "skip")
        cands = TOP.next_unlearned(self.st, c, 3)
        tp = TOP.ensure(self.st, c)
        trows = [[("📌 " + tp[i]["name"].split(":")[0][:32], pre + f"t{i}")] for i in cands]
        if k == "topics":
            if sug:
                text = f"{name}: מה עברתם {when}?\nלפי הסילבוס: {sug}"
                rows = [[("✔️ כן, זה", pre + "ok"), ("✏️ משהו אחר", pre + "w")], [skip]]
            elif trows:
                text = f"{name}: מה עברתם {when}? (הנושאים הבאים בסילבוס, או כתוב לי)"
                rows = trows + [[("✏️ משהו אחר", pre + "w"), skip]]
            else:
                text = f"{name}: מה עברתם {when}? כתוב לי בקצרה."
                rows = [[skip]]
        elif k == "unclear":
            text = f"{name}: מה לא הבנת?"
            first = [("🧩 " + UNCLEAR_OPTS["u1"], pre + "u1"), ("🧮 " + UNCLEAR_OPTS["u2"], pre + "u2")]
            rows = [first, [("🌫️ " + UNCLEAR_OPTS["u3"], pre + "u3"), ("✏️ אכתוב", pre + "w")]]
            if sug:
                rows.insert(0, [("📌 " + sug[:30], pre + "u0")])
            else:
                rows = trows + rows
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
        tp = TOP.ensure(self.st, c)
        if code.startswith("t") and code[1:].isdigit() and int(code[1:]) < len(tp) and k in ("topics", "unclear"):
            i = int(code[1:])
            if k == "topics":
                self.add_recap(ds, c, topics=tp[i]["name"], topic_ids=[i])
            else:
                self.add_recap(ds, c, unclear=f"הנושא: {tp[i]['name'][:60]}", topic_ids=[i])
            shown = f"✔️ {tp[i]['name'].split(':')[0][:40]}"
        elif k == "topics" and code == "ok":
            self.add_recap(ds, c, topics=cur.get("sug", ""), topic_ids=[x for x in [TOP.index_of(self.st, c, cur.get("sug", ""))] if x is not None])
            shown = f"✔️ {cur.get('sug', '')}"
        elif k == "unclear" and code in UNCLEAR_OPTS:
            self.add_recap(ds, c, unclear=UNCLEAR_OPTS[code])
            shown = f"✔️ {UNCLEAR_OPTS[code]}"
        elif k == "unclear" and code == "u0":
            self.add_recap(ds, c, unclear=f"הנושא: {cur.get('sug', '')}",
                           topic_ids=[x for x in [TOP.index_of(self.st, c, cur.get("sug", ""))] if x is not None])
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
        if r.get("backfill"):          # השלמת ריקאפ משיעור ישן: אין שאלת "מתי נוח ללמוד היום"
            att = {c: self.st["bot"]["attend"].get(f"{c}|{ds}") for c in r["courses"]}
            if any(v in ("absent", "reserve") for v in att.values()):
                if not r.get("noted"):               # לא היה בשיעור: אין ריקאפ. אם יתברר ש"בעצם הייתי", ממשיכים כרגיל
                    r["noted"] = True
                    prev = r.get("prev_pending")
                    if prev is None:
                        self.st["bot"].pop("pending", None)
                    else:
                        self.st["bot"]["pending"] = prev
                    self.tg.send("נרשם: לא היית בשיעור הזה.")
                return
            r["final"] = True
            self.rq_close(ds)
            prev = r.get("prev_pending")
            if prev is None:
                self.st["bot"].pop("pending", None)
            else:
                self.st["bot"]["pending"] = prev
            self.tg.send("✅ הריקאפ נשמר.")
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
        row = SUG.log_study(self.st, s["start"][:10], s["course"], mins, None, "session", P.iso(self.now), s["key"])
        inf = TOP.infer_review(self.st, s["course"], s["start"][:10])
        if inf:
            row["tinf"] = inf

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
        row = SUG.log_study(self.st, d, c, m, a.get("topic"), "text", P.iso(self.now))
        ids = TOP.valid_ids(self.st, c, a.get("topic_ids")) or (TOP.match(self.st, c, a.get("topic") or "") if a.get("topic") else [])
        if ids:
            TOP.mark_reviewed(self.st, c, d, ids, est=False)
        elif not any(r is not row and r.get("session") and r.get("course") == c and r.get("date") == d for r in self.st["log"]):
            inf = TOP.infer_review(self.st, c, d)          # אם כבר יש סשן באותו יום, לא סופרים אותו לימוד כפול
            if inf:
                row["tinf"] = inf
        tail = f" · נחזר: {TOP.label(self.st, c, ids)}" if ids else ""
        return f"✅ רשמתי: {COURSES[c]['name']}" + (f", {m} דק׳" if m else "") + tail

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
        if self.mar_active(tm):                      # בתקופת המרתון ההצעה אחת: של המרתון, בלי הצעות סותרות
            ml, mrows = self.m2_today(tm)
            return ([""] + ml) if ml else [], mrows
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

    # ------------------------------------------------------------ מפת חומר (יעד 1)
    def sync_topics(self, rec: dict, topics_text: str = "", topic_ids=None):
        """ריקאפ ← מפת החומר: נושא שנלמד ביום הריקאפ, עם ההבנה ומה שלא היה ברור. שיוך: מספרים מ-Claude, אחרת התאמת מילים."""
        c, d = rec["course"], rec["date"]
        ids = TOP.valid_ids(self.st, c, topic_ids)
        if not ids and topics_text:
            ids = TOP.match(self.st, c, topics_text)
        if ids:
            rec["tids"] = sorted(set(rec.get("tids", [])) | set(ids))
        if rec.get("tids"):
            TOP.mark_learned(self.st, c, d, rec["tids"], rec.get("rating"), rec.get("unclear", ""))
        return ids

    def topic_set_action(self, a: dict) -> str:
        c = a.get("course")
        if c not in COURSES:
            raise ValueError("קורס לא ידוע")
        ids = TOP.valid_ids(self.st, c, a.get("ids"))
        if not ids:
            return "לא הצלחתי לזהות איזה נושא. כתוב \"חומר\" כדי לראות את הרשימה."
        d = a.get("date") or self.now.date().isoformat()
        op = a.get("op")
        if op == "clear":
            TOP.clear_topic(self.st, c, ids, a.get("date") or None)
        elif op == "reviewed":
            TOP.mark_reviewed(self.st, c, d, ids, est=False)
        elif op == "learned":
            TOP.mark_learned(self.st, c, d, ids)
        else:
            return "לא הבנתי מה לעדכן בנושא."
        verb = {"clear": "הוסר", "reviewed": "נרשם כנחזר", "learned": "נרשם כנלמד"}[op]
        return f"🗺️ {verb}: {COURSES[c]['name']} · {TOP.label(self.st, c, ids)}"

    # ------------------------------------------------------------ מעבר ל-v2 (שלב G)
    MIG_KINDS = ("review", "weekly", "rescheduled", "marathon")

    def migration_candidates(self) -> list[dict]:
        """סשנים עתידיים שהבוט קבע לבד. לא נוגעים במה שעודד קבע (src), בתוספות שהוא אישר (extra, assign) ובסשן שכבר סומן."""
        return [s for s in sorted(self.st["sessions"], key=lambda x: x["start"])
                if s.get("status") == "planned" and datetime.fromisoformat(s["start"]) > self.now
                and s.get("kind") in self.MIG_KINDS and s.get("src") not in ("button", "text")]

    def propose_migration(self):
        mode = self.st["bot"].get("mode", "freeze")
        if mode == "v2":
            self.tg.send("כבר במצב v2. הבוט רק שואל ומציע, ואתה קובע.")
            return
        if mode == "v1":
            self.tg.send("הבוט במצב הישן (v1) שמשבץ לבד. המעבר נעשה דרך הגדרת מצב ולא כאן.")
            return
        cand = self.migration_candidates()
        mine = len([s for s in self.st["sessions"] if s.get("status") == "planned" and s not in cand
                    and datetime.fromisoformat(s["start"]) > self.now])
        by_day: dict[str, int] = {}
        for s in cand:
            by_day[s["start"][:10]] = by_day.get(s["start"][:10], 0) + 1
        days = ", ".join(f"{date.fromisoformat(d).day}.{date.fromisoformat(d).month} ({n})" for d, n in list(by_day.items())[:10])
        lines = ["מעבר למצב החדש: הבוט לא קובע זמני למידה לבד, רק שואל ומציע, ואתה בוחר.",
                 f"למחוק מיומן החזרות {len(cand)} סשנים עתידיים שהבוט קבע לבד?"]
        if days:
            lines.append("ימים: " + days + ("…" if len(by_day) > 10 else ""))
        lines.append(f"לא ייגעו: {mine} סשנים שקבעת או אישרת (חיזוק, תוספת, מטלה), ושיעורים.")
        self.tg.send("\n".join(lines), [[("✅ כן, למחוק ולעבור", "mig|ok"), ("✖️ לא עכשיו", "mig|no")]])

    def on_mig_callback(self, parts: list[str], mid) -> str:
        if parts[1] == "no":
            self.tg.edit_buttons(mid, [])
            return "בסדר, נשארים כמו שזה"
        if self.st["bot"].get("mode", "freeze") == "v2":
            self.tg.edit_buttons(mid, [])
            return "כבר טופל"
        done, failed = [], []
        for s in self.migration_candidates():
            n0 = len(self.log)
            self._drop(s, "הוסר במעבר למצב החדש.")
            if len(self.log) > n0 and str(self.log[-1]).startswith("drop:"):
                failed.append(s)
            else:
                done.append(s)
        self.st["bot"]["mode"] = "v2"
        self.tg.edit_buttons(mid, [])
        msg = f"✅ עברנו. נמחקו {len(done)} סשנים מיומן החזרות."
        if failed:
            msg += f" {len(failed)} לא נמחקו מהיומן (שגיאה), בדוק ידנית."
        self.tg.send(msg + "\nמעכשיו אני שואל ומציע, ואתה קובע. סימנתי אותם \"הוסר\" ולא ייספרו כהחמצה.")
        return "בוצע"

    # ------------------------------------------------------------ ריקאפים שמורים וחסרים
    def recaps_for_llm(self) -> list[dict]:
        """הריקאפים השמורים (60 יום אחרונים, עד 30), כדי ש-Claude יוכל לענות מה נרשם."""
        lo = (self.now.date() - timedelta(days=60)).isoformat()
        rows = [r for r in self.st.get("recaps", []) if r.get("date", "") >= lo and r.get("course") in COURSES]
        rows.sort(key=lambda r: (r["date"], P.COURSE_ORDER.index(r["course"])), reverse=True)
        out = []
        for r in rows[:30]:
            x = {"date": r["date"], "course": r["course"], "att": self.st["bot"]["attend"].get(f"{r['course']}|{r['date']}")}
            if r.get("rating") is not None:
                x["rating"] = r["rating"]
            for k, n in (("topics", 90), ("unclear", 70), ("cause", 40)):
                if (r.get(k) or "").strip():
                    x[k] = r[k].strip()[:n]
            out.append(x)
        return out

    def missing_list(self, uni=None) -> list[dict]:
        uni = uni or self.load_uni()[0]
        return SUG.missing_recaps(self.st, self.now.date(), uni, self.now)

    def missing_for_llm(self, uni):
        if getattr(self, "_feed_error", None):
            return None                      # לא יודעים: לא מצהירים "אין חסרים"
        return self.missing_list(uni)[-25:]

    def recaps_overview(self):
        uni, _ = self.load_uni()
        if getattr(self, "_feed_error", None):
            self.tg.send("📝 ריקאפים\n\n⚠️ לא הצלחתי לקרוא את מערכת השעות עכשיו, אז אני לא יכול לדעת מה חסר. נסה שוב בעוד כמה דקות.")
            return
        miss = self.missing_list(uni)
        lines = ["📝 ריקאפים"]
        if miss:
            lines += ["", f"חסרים {len(miss)} (מהחדש לישן):"]
            for m in sorted(miss, key=lambda x: x["date"], reverse=True)[:14]:
                d = date.fromisoformat(m["date"])
                lines.append(f"• {COURSES[m['course']]['name']} · {_dd(d)}")
            if len(miss) > 14:
                lines.append(f"ועוד {len(miss) - 14} ישנים יותר")
        else:
            lines += ["", "אין ריקאפים חסרים 👌"]
        saved = self.recaps_for_llm()[:5]
        if saved:
            lines += ["", f"נשמרו {len(self.st['recaps'])}. האחרונים:"]
            for r in saved:
                d = date.fromisoformat(r["date"])
                bit = (f" · הבנה {r['rating']}/5" if r.get("rating") else "") + (f" · {r['topics'][:45]}" if r.get("topics") else "")
                lines.append(f"• {COURSES[r['course']]['name']} {d.day}.{d.month}{bit}")
        if miss:
            lines += ["", "לחץ על שיעור להשלמה, או כתוב חופשי מה זכור לך (למשל \"במתמטיקה ב-2.11 למדנו נגזרות, הבנתי 3\")."]
        rows = [[(f"השלם: {COURSES[m['course']]['name'][:10]} {date.fromisoformat(m['date']).day}.{date.fromisoformat(m['date']).month}",
                  f"rcb|{m['course']}|{m['date']}")] for m in sorted(miss, key=lambda x: x["date"], reverse=True)[:8]] if self.conv else []
        self.tg.send("\n".join(lines), rows or None)

    def backfill_recap(self, c: str, ds: str) -> str:
        """כפתור "השלם": כרטיס ריקאפ (נוכחות והבנה, ואז השאלות) לשיעור ישן. לא נוגע בשיחת ריקאפ פעילה של היום."""
        if c not in COURSES or not self.conv:
            return "לא זמין"
        d = date.fromisoformat(ds)
        today = self.now.date().isoformat()
        if ds >= today:
            return "הריקאפ של היום מגיע בערב"
        if self.st["bot"]["attend"].get(f"{c}|{ds}") in ("absent", "reserve") or any(
                r["course"] == c and r["date"] == ds and (r.get("rating") is not None or (r.get("topics") or "").strip()) for r in self.st["recaps"]):
            return "כבר טופל"
        r = self.rq()
        if r and not r["closed"] and not r["final"] and not r.get("backfill") and r["date"] != ds:
            self.tg.send(f"יש ריקאפ פתוח של {_dd(date.fromisoformat(r['date']))}. אפשר לסיים אותו קודם, או לכתוב לי חופשי מה זכור לך מהשיעור הזה, "
                         f"למשל \"ב{COURSES[c]['name']} ב-{d.day}.{d.month} ...\", ואשייך לפי התאריך.")
            return ""
        prev = self.st["bot"].get("pending")
        self.st["bot"]["rq"] = {"date": ds, "courses": [c], "queue": [], "cur": None, "asked": [], "final": False, "closed": False,
                               "backfill": True, "prev_pending": prev}
        mid = self.tg.send(f"🚆 ריקאפ לשיעור שעבר · {_dd(d)}\n{COURSES[c]['name']}\n\nהיית בשיעור?", self.recap_buttons(d, [c]))
        self.st["bot"]["msgs"][str(mid)] = {"type": "recap", "date": ds, "courses": [c]}
        self.st["bot"]["pending"] = {"type": "recap", "date": ds, "courses": [c], "until": P.iso(self.now + timedelta(hours=8))}
        return ""
