"""
סשנים שהבוט מציע מעבר לחזרות הרגילות, וכולם רק באישור (כפתור ✅):
  - חיזוק: ריקאפ בציון 1–2 → סשן נוסף לקורס בימים הקרובים
  - זמן עבודה למטלה: מטלה של שעה ומעלה → עד 2 סשני עבודה לפני ההגשה
  - יום פנוי ("מחר פנוי כל היום") → תרגול נוסף
עד 2 תוספות בשבוע, מעל תקרת ה-11 (החלטה של עודד, 16.10).
וגם: קיבולת ("מחר רק סשן אחד") → הבוט מזיז את העודף, וכיול יעדים אחרי 3 שבועות.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

import planner as P

EXTRA_KINDS = ("extra", "assign")
EXTRA_WEEKLY_MAX = 2
XP_TTL_H = 48
COURSES = P.COURSES


def _dd(d: date) -> str:
    return f"{P.HEB_DAY[d.weekday()]} {d.day}.{d.month}"


class ExtrasMixin:
    # ------------------------------------------------------------ budget & slots
    def extras_in_week(self, ws: date, skip: str | None = None) -> int:
        we = ws + timedelta(days=7)
        n = sum(1 for s in self.st["sessions"] if s.get("kind") in EXTRA_KINDS and s.get("status") in ("planned", "done", "missed")
                and ws.isoformat() <= s["start"][:10] < we.isoformat())
        for xid, p in self.st["bot"].get("xp", {}).items():
            if xid != skip and p.get("state") == "pending":
                n += sum(1 for a, _ in p["slots"] if ws.isoformat() <= a[:10] < we.isoformat())
        return n

    def _taken(self) -> list:
        out = [P._session_from_state(s) for s in self.st["sessions"] if s.get("status") in ("planned", "done", "missed")]
        for p in self.st["bot"].get("xp", {}).values():
            if p.get("state") == "pending":
                for a, b in p["slots"]:
                    out.append(P.Session("xp", "extra", p.get("course"), [p.get("course")],
                                         datetime.fromisoformat(a), datetime.fromisoformat(b)))
        return out

    def find_slots(self, n: int, first: date, last: date, course: str | None = None, days: list | None = None,
                   budget: bool = True, extra_taken: list | None = None, skip_days: list | None = None) -> list[tuple[datetime, datetime]]:
        """סלוטים פנויים לפי כללי השיבוץ: לא בשבת, לא בערבי שני/רביעי, לא על שיעור או אירוע אישי, לא אחרי 23:00,
        עד 2 סשנים מאותו קורס ביום, ועד 2 תוספות בשבוע (budget)."""
        uni, _ = self.load_uni()
        personal = self.load_personal(P.week_start(self.now.date()))
        taken = self._taken() + [P.Session("x", "extra", course, [course], a, b) for a, b in (extra_taken or [])]
        used: dict[date, int] = {}
        out = []
        d = max(first, self.now.date())
        while d <= last and len(out) < n:
            if (days is not None and d not in days) or (skip_days and d in skip_days):
                d += timedelta(days=1)
                continue
            wd = d.weekday()
            ws = P.week_start(d)
            if wd in P.CONFIG["closed_weekdays"] or wd in P.CONFIG["reschedule_no_extra_weekdays"]:
                d += timedelta(days=1)
                continue
            if budget and self.extras_in_week(ws) + used.get(ws, 0) >= EXTRA_WEEKLY_MAX:
                d += timedelta(days=1)
                continue
            prof = P.day_profile(d, uni)
            if prof["type"] in ("closed", "class_day") or not prof["start"]:
                d += timedelta(days=1)
                continue
            on_day = sum(1 for s in taken if s.start.date() == d)
            total_cap = P.CONFIG["reschedule_total_cap_free_day"] if prof["type"] == "free" else prof["cap"] + 1
            same = sum(1 for s in taken if s.start.date() == d and s.course == course) if course else 0
            if on_day >= total_cap or same >= P.CONFIG["max_same_course_per_day"]:
                d += timedelta(days=1)
                continue
            prof = dict(prof, cap=prof["cap"] + EXTRA_WEEKLY_MAX)
            if d == self.now.date():
                prof["start"] = max(prof["start"], P._round_up(self.now + timedelta(minutes=30)))
            slots = P.free_slots(d, prof, P.busy_blocks(d, uni, personal), taken)
            if slots:
                a, b = slots[0]
                out.append((a, b))
                taken.append(P.Session("new", "extra", course, [course], a, b))
                used[ws] = used.get(ws, 0) + 1
                continue   # אותו יום עוד פעם (עד התקרות), אחרת היום הבא
            d += timedelta(days=1)
        return out

    # ------------------------------------------------------------ proposals
    def propose_extra(self, kind: str, course: str, label: str, note: str, slots: list, reason: str, ref: str = "") -> str:
        p = {"kind": kind, "course": course, "label": label, "note": note, "reason": reason, "ref": ref,
             "slots": [(P.iso(a), P.iso(b)) for a, b in slots], "state": "pending", "at": P.iso(self.now)}
        xid = P.exc_id(f"{kind}{course}{ref}{p['slots']}")[:8]
        if xid in self.st["bot"].setdefault("xp", {}):
            return xid
        self.st["bot"]["xp"][xid] = p
        self.deferred(lambda: self._send_xp(xid))
        return xid

    def _send_xp(self, xid: str):
        p = self.st["bot"]["xp"][xid]
        text = self._xp_text(p).replace("\n", "\nהצעה:\n", 1) + f"\n(מעבר ל-11 הסשנים הרגילים. עד {EXTRA_WEEKLY_MAX} תוספות בשבוע.)"
        p["mid"] = self.tg.send(text, [[("✅ שבץ", f"xp|ok|{xid}"), ("✖️ לא צריך", f"xp|no|{xid}")]])

    def _xp_text(self, p: dict) -> str:
        lines = [f"💡 {p['reason']}"]
        for a, b in p["slots"]:
            a, b = datetime.fromisoformat(a), datetime.fromisoformat(b)
            lines.append(f"• {_dd(a.date())} {P.fmt_t(a)}–{P.fmt_t(b)} · {p['label']}")
        return "\n".join(lines)

    def cancel_extra(self, xid: str) -> str:
        p = self.st["bot"].get("xp", {}).get(xid)
        if not p or p["state"] != "pending":
            return "כבר טופל"
        p["state"] = "cancelled"
        if p.get("mid"):
            self.tg.edit(p["mid"], self._xp_text(p) + "\n\n✖️ לא שובץ.")
        return "בסדר"

    def exec_extra(self, xid: str) -> str:
        p = self.st["bot"].get("xp", {}).get(xid)
        if not p or p["state"] != "pending":
            return "כבר טופל"
        if self.now - datetime.fromisoformat(p["at"]) > timedelta(hours=XP_TTL_H):
            p["state"] = "expired"
            self.tg.edit(p["mid"], self._xp_text(p) + "\n\n⌛ פג תוקף. בקש שוב אם עדיין רלוונטי.")
            return "פג תוקף"
        p["state"] = "checking"   # לא נספר כתפוס בזמן שבודקים אותו מחדש
        try:
            return self._exec_extra(xid, p)
        except Exception:
            p["state"] = "pending"
            raise

    def _exec_extra(self, xid: str, p: dict) -> str:
        uni, _ = self.load_uni()
        personal = self.load_personal(P.week_start(self.now.date()))
        taken = self._taken()
        final, bad = [], []
        for a, b in p["slots"]:
            a, b = datetime.fromisoformat(a), datetime.fromisoformat(b)
            blocks = [(x[0], x[1]) for x in P.busy_blocks(a.date(), uni, personal)] + [(s.start, s.end) for s in taken]
            if a > self.now + timedelta(minutes=10) and not any(a < y and x < b for x, y in blocks):
                final.append((a, b))
                taken.append(P.Session("new", "extra", p["course"], [p["course"]], a, b))
            else:
                bad.append(a)
        for a in bad:
            alt = self.find_slots(1, a.date(), a.date() + timedelta(days=3), p["course"], budget=False, extra_taken=final)
            if alt:
                final.append(alt[0])
        if not final:
            p["state"] = "failed"
            self.tg.edit(p["mid"], self._xp_text(p) + "\n\n⚠️ הזמנים האלה כבר לא פנויים ולא מצאתי חלופה. בקש שוב.")
            return "לא נמצא זמן"
        made = []
        for i, (a, b) in enumerate(final):
            key = f"{p['kind']}:{p['course']}:{p.get('ref') or xid}:{i + 1}@{a.date().isoformat()}"
            sess = P.Session(key, p["kind"], p["course"], [p["course"]], a, b, None, note=p["note"], label=p["label"])
            js = sess.to_json()
            eid = self.create_event(js)
            self.st["sessions"].append({k: js[k] for k in ("key", "kind", "course", "courses", "start", "end", "lesson_day", "note", "label")}
                                       | {"status": "planned", "eventId": eid})
            made.append(f"• {_dd(a.date())} {P.fmt_t(a)}")
        p["state"] = "done"
        self.tg.edit(p["mid"], self._xp_text(p) + "\n\n✅ שובץ ביומן:\n" + "\n".join(made))
        return "✅ שובץ"

    # ------------------------------------------------------------ triggers
    def maybe_reinforce(self, course: str, d: str, rating: int, unclear: str = "") -> str:
        """ריקאפ בציון 1–2: מציעים סשן חיזוק אחד בארבעת הימים הקרובים (פעם אחת לכל שיעור)."""
        if rating is None or rating > 2 or course not in COURSES:
            return ""
        done = self.st["bot"].setdefault("reinf", [])
        tag = f"{course}|{d}"
        if tag in done:
            return ""
        done.append(tag)
        self.st["bot"]["reinf"] = done[-60:]
        today = self.now.date()
        slots = self.find_slots(1, today + timedelta(days=1), today + timedelta(days=4), course)
        name = COURSES[course]["name"]
        if not slots:
            return f"💪 לא מצאתי מקום לחיזוק ב{name} בימים הקרובים (או שכבר יש {EXTRA_WEEKLY_MAX} תוספות השבוע). כתוב לי אם לחפש בכל זאת."
        what = f": {unclear[:40]}" if unclear else ""
        self.propose_extra("extra", course, f"💪 חיזוק · {name}{what}",
                           f"חיזוק אחרי ריקאפ {d}: הבנה {rating}/5." + (f" לא ברור: {unclear}" if unclear else ""),
                           slots, f"{name}: הבנה {rating}/5 בשיעור. כדאי סשן חיזוק.", ref=d)
        return ""

    def maybe_assignment_time(self, a: dict) -> str:
        """מטלה של שעה ומעלה עם תאריך הגשה: עד 2 סשני עבודה לפני ההגשה (מוקדם ככל האפשר)."""
        est, due = a.get("est_min"), a.get("due")
        if not est or not due or int(est) < 60 or a.get("work_offered"):
            return ""
        try:
            due_d = date.fromisoformat(due[:10])
        except ValueError:
            return ""
        today = self.now.date()
        last = due_d - timedelta(days=1)
        if last < today:
            return ""
        a["work_offered"] = True
        n = min(EXTRA_WEEKLY_MAX, math.ceil(int(est) / P.CONFIG["session_min"]))
        slots = self.find_slots(n, today + timedelta(days=1) if last > today else today, last, a["course"])
        name = COURSES.get(a["course"], {}).get("name", a["course"])
        if not slots:
            return f"📝 לא מצאתי זמן עבודה למטלה לפני {due_d.day}.{due_d.month} (או שכבר יש {EXTRA_WEEKLY_MAX} תוספות). היא תופיע בסיכומים היומיים."
        self.propose_extra("assign", a["course"], f"📝 מטלה · {name}: {a['title'][:40]}",
                           f"זמן עבודה למטלה \"{a['title']}\" (הגשה {due_d.day}.{due_d.month}, הערכה {est} דק').",
                           slots, f"מטלה ב{name}: \"{a['title'][:50]}\" עד {_dd(due_d)}, כ-{est} דק'.", ref=a["id"])
        return ""

    # ------------------------------------------------------------ capacity ("מחר רק סשן אחד" / "מחר פנוי")
    def apply_capacity(self, d: str, max_sessions, free_day: bool, text: str) -> str:
        day = date.fromisoformat(d)
        if day < self.now.date():
            raise ValueError("התאריך כבר עבר")
        if free_day:
            return self._free_day(day)
        if max_sessions is None:
            self.st["pending_questions"].append({"id": f"cap-{d}", "text": f"קיבולת {d}: {text}", "asked": P.iso(self.now)})
            return "📌 רשמתי. תגיד לי כמה סשנים אתה יכול באותו יום, ואסדר לפי זה."
        max_sessions = max(0, int(max_sessions))
        on_day = sorted([s for s in self.st["sessions"] if s.get("status") == "planned" and s["start"][:10] == d
                         and datetime.fromisoformat(s["start"]) > self.now],
                        key=lambda s: (s.get("kind") in EXTRA_KINDS, s.get("kind") == "marathon",
                                       COURSES.get(s.get("course"), {}).get("tier", 0), s["start"]))
        extra = on_day[max_sessions:]
        if not extra:
            return f"👍 ב-{_dd(day)} יש {len(on_day)} סשנים, זה כבר בתוך מה שאמרת."
        out, moving = [], []
        for s in extra:
            title = P._session_from_state(s).title()
            if s.get("kind") in ("review", "rescheduled"):
                s["status"] = "missed"
                s["note"] = f"פינית את {_dd(day)}."
                moving.append(s["key"])
            elif s.get("kind") in EXTRA_KINDS:
                alt = self.find_slots(1, day + timedelta(days=1), day + timedelta(days=5), s.get("course"), budget=False)
                if alt:
                    a, b = alt[0]
                    s["start"], s["end"] = P.iso(a), P.iso(b)
                    if s.get("eventId"):
                        self.cal.patch(self.reviews_cal, s["eventId"], {"start": {"dateTime": a.isoformat(), "timeZone": "Asia/Jerusalem"},
                                                                       "end": {"dateTime": b.isoformat(), "timeZone": "Asia/Jerusalem"}})
                    out.append(f"• {title} → {_dd(a.date())} {P.fmt_t(a)}")
                else:
                    self._drop(s, f"פינית את {_dd(day)}.")
                    out.append(f"• {title}: לא מצאתי זמן אחר, הורד")
            else:   # מרתון: החלוקה מתעדכנת לבד
                self._drop(s, f"פינית את {_dd(day)}.")
                out.append(f"• {title}: הורד, אשבץ במקום אחר בתוכנית המרתון")
        if moving:
            uni, _ = self.load_uni()
            personal = list(self.load_personal(P.week_start(self.now.date())))
            block = P.Ev(uid="cap", summary="תפוס", description="", location="", start=P.at(day, time(0, 0)),
                         end=P.at(day, time(23, 59)), transparent=False, all_day=False)
            cutoff = P.at(self.now.date(), time(0, 0))
            only = [s for s in self.st["sessions"] if not (s.get("status") == "planned" and datetime.fromisoformat(s["end"]) < cutoff)]
            r = self.cap_reschedule(P.reschedule(self.now.date(), uni, personal + [block], dict(self.st, sessions=only), self.now),
                                    self.now.date())
            r = {**r, "rescheduled": [x for x in r["rescheduled"] if x["from_key"] in moving],
                 "not_placed": [x for x in r["not_placed"] if x["key"] in moving]}
            P.apply_reschedule(self.st, r)
            for x in r["rescheduled"]:
                self.set_event_id(x["key"], self.create_event(x))
                if x.get("from_eventId"):
                    try:
                        from bot.core import strip_marks
                        self.cal.patch(self.reviews_cal, x["from_eventId"], {"summary": "↪️ " + strip_marks(x["from_title"])})
                    except Exception:  # noqa: BLE001
                        pass
                a = datetime.fromisoformat(x["start"])
                out.append(f"• {x['from_title']} → {_dd(a.date())} {P.fmt_t(a)}")
            for x in r["not_placed"]:
                out.append(f"• {x['title']}: לא מצאתי מקום השבוע, עובר לחזרה השבועית")
        return f"✅ השארתי ב-{_dd(day)} {max_sessions} סשנים. הזזתי:\n" + "\n".join(out)

    def _drop(self, s: dict, note: str):
        s["status"] = "dropped"
        s["note"] = note
        if s.get("eventId"):
            try:
                self.cal.delete(self.reviews_cal, s["eventId"])
            except Exception as e:  # noqa: BLE001
                self.log.append(f"drop: {e}")

    def _free_day(self, day: date) -> str:
        ws = P.week_start(day)
        left = EXTRA_WEEKLY_MAX - self.extras_in_week(ws)
        if left <= 0:
            return f"👍 רשמתי. השבוע כבר יש {EXTRA_WEEKLY_MAX} תוספות, אז לא הוספתי. אם תרצה בכל זאת, תגיד איזה קורס."
        weak = {}
        for r in self.st["recaps"]:
            if r.get("rating") and r["date"] >= (day - timedelta(days=14)).isoformat():
                weak[r["course"]] = min(weak.get(r["course"], 6), r["rating"])
        order = sorted(P.COURSE_ORDER, key=lambda c: (weak.get(c, 6), COURSES[c]["tier"], P.COURSE_ORDER.index(c)))
        made = 0
        for c in order:
            if made >= left:
                break
            slots = self.find_slots(1, day, day, c, days=[day])
            if not slots:
                continue
            self.propose_extra("extra", c, f"📚 תרגול נוסף · {COURSES[c]['name']}", "תרגול נוסף ביום שאמרת שהוא פנוי.",
                               slots, f"אמרת ש{_dd(day)} פנוי. הצעה לתרגול נוסף ב{COURSES[c]['name']}" +
                               (f" (הבנה {weak[c]}/5 לאחרונה)." if c in weak else "."), ref=day.isoformat())
            made += 1
        return "👍 רשמתי." if made else f"👍 רשמתי. לא מצאתי ב-{_dd(day)} מקום פנוי לתוספת."

    # ------------------------------------------------------------ calibration (אחרי 3 שבועות)
    def calibration_lines(self, ws: date) -> tuple[list[str], list]:
        start = ws - timedelta(days=14)
        per = {c: {"p": 0, "d": 0, "r": []} for c in P.COURSE_ORDER}
        for wk in (start, start + timedelta(days=7), ws):
            for c, row in self.st.get("archive", {}).get(wk.isoformat(), {}).items():
                if c in per:
                    per[c]["p"] += row.get("planned", 0)
                    per[c]["d"] += row.get("done", 0)
        for s in self.st["sessions"]:
            c = s.get("course")
            if c in per and start.isoformat() <= s["start"][:10] < (ws + timedelta(days=7)).isoformat() and s.get("kind") != "rescheduled":
                per[c]["p"] += 1
                per[c]["d"] += s.get("status") == "done"
        for r in self.st["recaps"]:
            if r.get("course") in per and r.get("rating") and start.isoformat() <= r["date"] < (ws + timedelta(days=7)).isoformat():
                per[r["course"]]["r"].append(r["rating"])
        lines = ["🎛️ כיול אחרי 3 שבועות: בוצע/מתוכנן, ממוצע הבנה, ויעד שבועי. אפשר לשנות בכפתורים."]
        buttons = []
        for c in P.COURSE_ORDER:
            v = per[c]
            avg = round(sum(v["r"]) / len(v["r"]), 1) if v["r"] else None
            tgt = P.target_of(c, self.st)
            hint = ""
            if avg is not None and avg <= 2.5:
                hint = " ← כדאי +1"
            elif v["p"] and v["d"] / v["p"] < 0.6 and (avg or 5) >= 4:
                hint = " ← אפשר −1"
            lines.append(f"• {COURSES[c]['name']}: {v['d']}/{v['p']} · הבנה {avg if avg is not None else '?'} · יעד {tgt}{hint}")
            buttons.append(self.target_row(c))
        return lines, buttons

    def target_row(self, c: str) -> list:
        tgt = P.target_of(c, self.st)
        return [(COURSES[c]["name"][:12], "noop"), ("−1", f"tgt|{c}|-1"), (f"יעד {tgt}", "noop"), ("+1", f"tgt|{c}|1")]

    def set_target(self, c: str, value: int) -> str:
        if c not in COURSES:
            raise ValueError("קורס לא מוכר")
        v = max(0, min(6, int(value)))
        self.st.setdefault("weekly_targets", {})[c] = v
        return f"🎛️ {COURSES[c]['name']}: {v} סשנים בשבוע (מהשבוע הבא)"
