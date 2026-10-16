"""
הבוט עצמו. רץ כל כמה דקות (tick): קורא הודעות וכפתורים מטלגרם, ומריץ את המשימות
שהגיע זמנן (בוקר, ריקאפ, סקירה יומית, סקירה שבועית). כל חישובי השיבוץ ב-planner.py.

כללי ברזל (ממסמך האפיון):
  - כותבים רק ליומן "חזרות לימודים". היומן האישי ויומן האוניברסיטה לקריאה בלבד.
  - כשל בסנכרון אינו "אין שינוי": פיד חשוד = לא משבצים, רק מתריעים.
  - מחיקה של עודד ביומן היא החלטה: מודיעים ולא יוצרים מחדש, אלא אם ביקש.
  - אין לימוד אחרי 23:00 אלא בבקשה מפורשת.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import traceback
from datetime import date, datetime, time, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import planner as P  # noqa: E402

TZ = P.TZ
HEB_DAY = P.HEB_DAY
COURSES = P.COURSES
MARKS = ("✅", "❌", "↪️")

JOBS = {
    # שם: (ימים בשבוע לפי weekday של פייתון, שעה, חלון ריצה בשעות)
    "morning": ([P.SUN, P.MON, P.TUE, P.WED, P.THU, P.FRI], "07:44", 6),
    "daily": ([P.SUN, P.MON, P.TUE, P.WED, P.THU], "21:30", 3),
    "weekly": ([P.SAT], "21:00", 24),
}


def sid(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:8]


def dd(d: date) -> str:
    return f"{HEB_DAY[d.weekday()]} {d.day}.{d.month}"


def event_id(key: str, suffix: str = "") -> str:
    """id קבוע לאירוע לפי מפתח הסשן (base32hex, כמו ש-Google דורש). יצירה כפולה = 409 = אין כפילות."""
    return "sb" + base64.b32hexencode(hashlib.sha1((key + suffix).encode()).digest()).decode().lower().rstrip("=")


def strip_marks(title: str) -> str:
    t = (title or "").strip()
    changed = True
    while changed:
        changed = False
        for m in MARKS:
            if t.startswith(m):
                t = t[len(m):].strip()
                changed = True
    return t


def rfc(iso_str: str) -> str:
    return datetime.fromisoformat(iso_str).astimezone(TZ).isoformat()


class Bot:
    def __init__(self, tg, cal, feed, store, llm, cfg: dict, now_fn=None):
        self.tg, self.cal, self.feed, self.store, self.llm = tg, cal, feed, store, llm
        self.reviews_cal = cfg["REVIEWS_CAL"]
        self.personal_cal = cfg.get("PERSONAL_CAL", "")
        self.now_fn = now_fn or (lambda: datetime.now(TZ))
        self.log: list[str] = []

    # ------------------------------------------------------------ state
    def load_state(self):
        st = self.store.load()
        if not st:
            seed = os.path.join(os.path.dirname(__file__), "seed_state.json")
            with open(seed, encoding="utf-8") as f:
                st = json.load(f)
            self.log.append("state: seeded")
        base = P.new_state()
        base.update(st)
        base.setdefault("bot", {})
        b = base["bot"]
        for k, v in {"offset": 0, "jobs": {}, "ids": {}, "msgs": {}, "pending": None, "fails": {},
                     "notes": [], "durations": {}, "warned": {}}.items():
            b.setdefault(k, v)
        self.st = base

    def save_state(self):
        P.compact_state(self.st, self.now)
        b = self.st["bot"]
        cutoff = (self.now.date() - timedelta(days=21)).isoformat()
        b["jobs"] = {k: v for k, v in b["jobs"].items() if k.split(":", 1)[-1] >= cutoff}
        b["msgs"] = dict(list(b["msgs"].items())[-60:])
        live = {s["key"] for s in self.st["sessions"]}
        b["ids"] = {k: v for k, v in b["ids"].items() if v in live}
        b["notes"] = b["notes"][-50:]
        self.st["updated"] = P.iso(self.now)
        self.st["planner_version"] = P.VERSION
        self.st.pop("checksum", None)
        self.st["checksum"] = P.state_checksum(self.st)
        self.store.save(self.st)

    def short(self, key: str) -> str:
        s = sid(key)
        self.st["bot"]["ids"][s] = key
        return s

    def by_short(self, s: str) -> dict | None:
        key = self.st["bot"]["ids"].get(s)
        return next((x for x in self.st["sessions"] if x["key"] == key), None) if key else None

    # ------------------------------------------------------------ data
    def _events_from(self, data) -> list:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"events": data}, f, ensure_ascii=False)
        try:
            return P.load_events(path)
        finally:
            os.unlink(path)

    def load_uni(self):
        if getattr(self, "_uni", None) is not None:
            return self._uni, self._raw
        try:
            text = self.feed.fetch()
            fd, path = tempfile.mkstemp(suffix=".ics")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            evs = P.load_events(path)
            os.unlink(path)
            self._feed_error = None
        except Exception as e:  # noqa: BLE001
            evs, self._feed_error = [], f"לא הצלחתי להוריד את פיד האוניברסיטה ({str(e)[:80]})"
        self._uni, self._raw = P.classify(evs), len(evs)
        if evs and not self._uni.lessons and not self._feed_error:
            self._feed_error = "הפיד הגיע, אבל לא זיהיתי בו אף שיעור (אולי בעיית קידוד)"
        return self._uni, self._raw

    def load_personal(self, start: date | None = None) -> list:
        """חלון קבוע ורחב (מתחילת השבוע הנוכחי, 3 שבועות קדימה), נטען פעם אחת בכל הרצה."""
        if getattr(self, "_personal", None) is not None:
            return self._personal
        self._personal, self._personal_error = [], None
        if self.personal_cal:
            s0 = P.week_start(self.now.date())
            try:
                data = self.cal.list(self.personal_cal, P.at(s0, time(0, 0)).isoformat(),
                                     P.at(s0 + timedelta(days=21), time(0, 0)).isoformat())
                self._personal = self._events_from(data)
            except Exception as e:  # noqa: BLE001
                self._personal_error = f"לא הצלחתי לקרוא את היומן האישי ({str(e)[:60]})"
        return self._personal

    def reviews(self, start: date, days: int) -> list[dict]:
        return self.cal.list(self.reviews_cal, P.at(start, time(0, 0)).isoformat(),
                             P.at(start + timedelta(days=days), time(0, 0)).isoformat())

    def guard_empty(self, events: list, start: date, end: date):
        """יומן שחזר ריק בזמן שיש לנו סשנים רשומים בו = קריאה חשודה. עוצרים לפני שמסיקים 'נמחק' או יוצרים כפילויות."""
        if events:
            return
        known = [s for s in self.st["sessions"] if s.get("eventId") and s.get("status") in ("planned", "done", "missed")
                 and start.isoformat() <= s["start"][:10] < end.isoformat()]
        if len(known) >= 2:
            raise RuntimeError("יומן החזרות חזר ריק למרות שיש בו סשנים רשומים. עצרתי כדי לא למחוק או לשכפל")

    def warn_once(self, key: str, text: str, every_hours: int = 24):
        w = self.st["bot"]["warned"]
        last = w.get(key)
        if last and self.now - datetime.fromisoformat(last) < timedelta(hours=every_hours):
            return
        w[key] = P.iso(self.now)
        self.tg.send(text)

    # ------------------------------------------------------------ calendar writes
    def create_event(self, s: dict, suffix: str = "") -> str:
        body = {"id": event_id(s["key"], suffix), "summary": s["title"], "description": s.get("description") or f"מזהה: {s['key']}",
                "start": {"dateTime": rfc(s["start"]), "timeZone": "Asia/Jerusalem"},
                "end": {"dateTime": rfc(s["end"]), "timeZone": "Asia/Jerusalem"},
                "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 10}]}}
        return self.cal.create(self.reviews_cal, body)["id"]

    def set_event_id(self, key: str, eid: str):
        for x in self.st["sessions"]:
            if x["key"] == key:
                x["eventId"] = eid

    def mark(self, s: dict, status: str):
        """בוצע / לא בוצע: גם במצב וגם בכותרת האירוע ביומן."""
        s["status"] = status
        s["marked_at"] = P.iso(self.now)
        if s.get("eventId"):
            title = strip_marks(P._session_from_state(s).title())
            prefix = {"done": "✅ ", "missed": "❌ "}.get(status, "")
            try:
                self.cal.patch(self.reviews_cal, s["eventId"], {"summary": prefix + title})
            except Exception as e:  # noqa: BLE001
                self.log.append(f"mark patch failed: {e}")

    def sync_week(self, ws: date, uni, personal) -> dict:
        """תוכנית השבוע ← יומן החזרות. יוצר רק מה שחסר. לא מוחק ולא מזיז."""
        p = P._plan_for(ws, uni, personal, self.st)
        known = {x["key"] for x in self.st["sessions"]}
        count = sum(1 for x in self.st["sessions"] if x.get("kind") != "weekly" and x.get("status") in ("planned", "done", "missed")
                    and ws.isoformat() <= x["start"][:10] < (ws + timedelta(days=7)).isoformat())
        keep, capped, past = [], [], []
        for x in p["sessions"]:
            if x["key"] in known:
                keep.append(x)
            elif datetime.fromisoformat(x["start"]) <= self.now:
                past.append(x)          # שיעור שהופיע באיחור ושובץ לזמן שעבר: לא נרשם
            elif x["kind"] != "weekly" and count >= self.WEEKLY_CAP:
                capped.append(x)        # תקרה שבועית
            else:
                keep.append(x)
                count += 1 if x["kind"] != "weekly" else 0
        p["sessions"] = keep
        p["capped"] = capped
        existing = self.reviews(ws - timedelta(days=1), 9)
        self.guard_empty(existing, ws, ws + timedelta(days=7))
        d = P.diff(p, existing, self.now)
        ids = {}
        for e in existing:
            m = re.search(r"מזהה: (\S+)", e.get("description", "") or "")
            if m:
                ids[m.group(1)] = e["id"]
        created = []
        for s in d["create"]:
            if datetime.fromisoformat(s["end"]) > datetime.fromisoformat(s["start"]).replace(hour=23, minute=0):
                continue  # כלל 23:00
            ids[s["key"]] = self.create_event(s)
            created.append(s)
        for u in d["update"]:
            if u["old_start"] == u["start"]:  # רק כותרת השתנתה (למשל השלמה)
                ex = next((e for e in existing if e["id"] == u["eventId"]), {})
                if not strip_marks(ex.get("summary", "")) == u["title"]:
                    self.cal.patch(self.reviews_cal, u["eventId"], {"summary": u["title"]})
        P.register_plan(self.st, p, ids)
        self.st["archived_keys"] = sorted(set(self.st.get("archived_keys", [])) | {x["key"] for x in p["unplaced"]}
                                          | {x["key"] for x in capped + past})
        p["created"] = created
        return p

    WEEKLY_CAP = 11

    def cap_reschedule(self, r: dict, today: date) -> dict:
        """השלמות רק בתוך השבוע הנוכחי, ובלי לעבור את התקרה השבועית (11). מה שלא נכנס: עודד מחליט."""
        ws = P.week_start(today)
        we = ws + timedelta(days=7)
        count = sum(1 for s in self.st["sessions"] if s.get("kind") != "weekly" and s.get("status") in ("planned", "done", "missed")
                    and ws.isoformat() <= s["start"][:10] < we.isoformat())
        keep, extra = [], []
        for x in r["rescheduled"]:
            d = datetime.fromisoformat(x["start"]).date()
            if d >= we or count >= self.WEEKLY_CAP:
                extra.append({"key": x["from_key"], "title": strip_marks(x["from_title"])})
            else:
                keep.append(x)
                count += 1
        return {**r, "rescheduled": keep, "not_placed": r["not_placed"] + extra}

    def restore(self, s: dict) -> str:
        d = datetime.fromisoformat(s["start"]).date()
        for e in self.reviews(d, 1):
            if f"מזהה: {s['key']}" in (e.get("description") or ""):
                s.update(status="planned", eventId=e["id"])
                s.pop("note", None)
                return e["id"]
        n = self.st["bot"].setdefault("restores", {}).get(s["key"], 0) + 1
        self.st["bot"]["restores"][s["key"]] = n
        eid = self.create_event(P._session_from_state(s).to_json(), suffix=f"r{n}")
        s.update(status="planned", eventId=eid)
        s.pop("note", None)
        return eid

    # ------------------------------------------------------------ feed + adopt (shared)
    def feed_and_adopt(self, today: date):
        uni, raw = self.load_uni()
        personal = self.load_personal(P.week_start(today))
        if getattr(self, "_feed_error", None):
            fc = {"ok": False, "block": True, "reasons": [self._feed_error], "stale": False, "first_run": False}
        else:
            fc = P.feed_check(uni, raw, self.st, today, self.now, True)
        if getattr(self, "_personal_error", None):
            fc["block"] = True
            fc["reasons"] = fc["reasons"] + [self._personal_error]
        ov = P.lesson_overrides(uni, personal)
        fc["skipped"] = [dict(v, key=k) for k, v in ov["skipped"].items() if v["date"] >= today.isoformat()]
        fc["duplicates"] = [dict(v, key=k) for k, v in ov["duplicates"].items() if v["date"] >= today.isoformat()]
        rev = self.reviews(today - timedelta(days=7), 15)
        self.guard_empty(rev, today - timedelta(days=7), today + timedelta(days=8))
        seen = {e["id"] for e in rev}
        lo, hi = (today - timedelta(days=7)).isoformat(), (today + timedelta(days=8)).isoformat()
        for x in self.st["sessions"]:
            if x.get("eventId") and x["eventId"] not in seen and x.get("status") == "planned" and lo <= x["start"][:10] < hi:
                e = self.cal.get(self.reviews_cal, x["eventId"])   # אולי הוזז רחוק, לא נמחק
                if e:
                    rev.append(e)
        changes = P.adopt_calendar(self.st, rev, P.at(today - timedelta(days=7), time(0, 0)),
                                   P.at(today + timedelta(days=8), time(0, 0)))
        return uni, personal, fc, changes

    def feed_lines(self, fc) -> list[str]:
        out = []
        if fc.get("stale"):
            out += [f"⚠️ {r}" for r in fc["reasons"]]
        if fc.get("first_run"):
            return out
        if fc.get("block"):
            return out
        for k in ("cancelled", "changed", "moved", "added", "vanished"):
            out += [f"• {x['text']}" for x in fc.get(k, [])]
        out += [f"• {x['text']} (בבדיקה)" for x in fc.get("vanishing", [])]
        return out

    # ------------------------------------------------------------ jobs
    def due_jobs(self) -> list[tuple[str, str]]:
        today, wd = self.now.date(), self.now.weekday()
        out = []
        for name, (days, hhmm, win) in JOBS.items():
            if wd in days:
                at = P.at(today, P.hm(hhmm))
                out.append((name, at, win))
        # ריקאפ ותזכורת: לפי השיעור האחרון ביום (רק אם יש שיעורים היום)
        if wd != P.SAT:
            jobs_today = self.st["bot"]["jobs"]
            need = not jobs_today.get(f"recap:{today.isoformat()}") or not jobs_today.get(f"recapr:{today.isoformat()}")
            if need:
                cache = self.st["bot"].setdefault("recap_at", {})
                if today.isoformat() not in cache:
                    uni, _ = self.load_uni()
                    if not getattr(self, "_feed_error", None):
                        ls = [l for l in uni.lessons if l.day == today]
                        cache.clear()
                        cache[today.isoformat()] = P.iso(max(l.end for l in ls) + timedelta(minutes=P.CONFIG["recap_after_min"])) if ls else ""
                r_iso = cache.get(today.isoformat())
                if r_iso is None:
                    pass                # הפיד לא זמין: ננסה בהרצה הבאה
                elif not r_iso:
                    jobs_today[f"recap:{today.isoformat()}"] = jobs_today[f"recapr:{today.isoformat()}"] = "none"
                else:
                    r = datetime.fromisoformat(r_iso)
                    out.append(("recap", r, 4))
                    sent = self.st["bot"].setdefault("recap_sent", {}).get(today.isoformat())
                    if sent:
                        out.append(("recapr", max(r, datetime.fromisoformat(sent)) + timedelta(minutes=P.CONFIG["recap_reminder_after_min"]), 2))
        res = []
        for name, at, win in out:
            key = f"{name}:{today.isoformat()}"
            if self.st["bot"]["jobs"].get(key):
                continue
            if self.now < at:
                continue
            if name == "recapr" and not self.st["bot"]["jobs"].get(f"recap:{today.isoformat()}"):
                continue
            if self.now > at + timedelta(hours=win):
                self.st["bot"]["jobs"][key] = "skipped"
                continue
            res.append((name, key))
        order = {"morning": 0, "recap": 1, "recapr": 2, "daily": 3, "weekly": 4}
        return sorted(res, key=lambda x: order[x[0]])

    def run_jobs(self):
        for name, key in self.due_jobs():
            snapshot = copy.deepcopy(self.st)
            try:
                getattr(self, f"job_{name}")(self.now.date())
                self.st["bot"]["jobs"][key] = "done"
                self.st["runs"].append({"run": name, "at": P.iso(self.now), "ok": True, "notes": ""})
            except Exception as e:  # noqa: BLE001
                # חוזרים למצב שלפני המשימה, כדי שההודעות לא יאבדו בניסיון הבא.
                # היצירות ביומן בטוחות לחזרה: id קבוע = אין כפילויות.
                self.st = snapshot
                self._uni = self._personal = None
                fails = self.st["bot"]["fails"]
                fails[key] = fails.get(key, 0) + 1
                self.log.append(f"{name}: {type(e).__name__}: {str(e)[:200]}")
                self.st["runs"].append({"run": name, "at": P.iso(self.now), "ok": False, "notes": str(e)[:200]})
                if fails[key] >= 3:
                    self.st["bot"]["jobs"][key] = "failed"
                    self.tg.send(f"⚠️ {name} נכשל 3 פעמים ודילגתי עליו היום: {str(e)[:200]}")
            self.save_state()

    def lessons_lines(self, uni, d: date) -> list[str]:
        return [f"• {P.fmt_t(l.start)}–{P.fmt_t(l.end)} {COURSES[l.course]['name']}" + (" (תרגול)" if l.kind == "תרגול" else "")
                + (f" · {l.location}" if l.location else "") for l in sorted(uni.lessons, key=lambda l: l.start) if l.day == d]

    def sessions_on(self, d: date) -> list[dict]:
        return sorted([s for s in self.st["sessions"] if s.get("start", "")[:10] == d.isoformat()
                       and s.get("status") in ("planned", "done", "missed")], key=lambda s: s["start"])

    def sess_line(self, s: dict) -> str:
        mark = {"done": "✅ ", "missed": "❌ "}.get(s.get("status"), "")
        return f"• {mark}{s['start'][11:16]} {strip_marks(P._session_from_state(s).title())}"

    def job_morning(self, today: date):
        uni, personal, fc, changes = self.feed_and_adopt(today)
        lines = [f"☀️ בוקר טוב · {dd(today)}"]
        buttons, warn = [], []
        moved, not_placed, created = [], [], []
        if fc["block"]:
            warn.append("⚠️ לא שיבצתי היום: " + "; ".join(fc["reasons"]))
        else:
            r = self.cap_reschedule(P.reschedule(today, uni, personal, self.st, self.now), today)
            P.apply_reschedule(self.st, r)
            for x in r["rescheduled"]:
                self.set_event_id(x["key"], self.create_event(x))
                if x.get("from_eventId"):
                    try:
                        self.cal.patch(self.reviews_cal, x["from_eventId"], {"summary": "↪️ " + strip_marks(x["from_title"])})
                    except Exception:  # noqa: BLE001
                        pass
                moved.append(f"• {strip_marks(x['from_title'])} → {dd(datetime.fromisoformat(x['start']).date())} {x['start'][11:16]}")
            not_placed = [f"• {x['title']}" for x in r["not_placed"]]
            p = self.sync_week(P.week_start(today), uni, personal)
            created = p["created"]
            not_placed += [f"• {x['title']} (השבוע כבר מלא, 11 סשנים)" for x in p["capped"]]
        ex = P.exc_notify(today, uni, personal, self.st, True)

        ls, ss = self.lessons_lines(uni, today), self.sessions_on(today)
        if ls or ss:
            lines.append("")
            lines += ls + [self.sess_line(s) for s in ss]
        else:
            lines.append("היום אין שיעורים ואין סשנים.")
        fl = self.feed_lines(fc)
        if fl:
            lines += ["", "🔄 שינויים במערכת:"] + fl
        deleted = [c for c in changes if c.startswith("נמחק ביומן")]
        if deleted:
            lines += ["", "🗑️ נמחק ממך ביומן (לא אצור מחדש):"]
            for s in self.st["sessions"]:
                if s.get("status") == "dropped" and s.get("note") == "נמחק ממך ביומן.":
                    s["note"] = "נמחק ממך ביומן (הודעתי)."
                    lines.append(self.sess_line(dict(s, status="planned")))
                    buttons.append([(f"↩️ להחזיר {strip_marks(P._session_from_state(s).title())[:28]}", f"restore|{self.short(s['key'])}")])
        if moved:
            lines += ["", "↪️ שובץ מחדש (לא סומן כבוצע):"] + moved
        if created:
            lines += ["", f"➕ נוספו ליומן {len(created)} סשנים חדשים."]
        decide = [x for x in ex["new"] + ex["remind"] if x["type"] not in ("class_on_free_day",)]
        if not_placed or decide:
            lines += ["", "❓ צריך החלטה:"] + [f"• לא מצאתי מקום ל{x[1:].strip()}" for x in not_placed]
            for x in decide:
                lines.append(f"• {x['text']}")
                buttons.append([(f"👍 קיבלתי · {x['text'][:30]}", f"ack|{x['id']}")])
        if fc.get("duplicates") and not any(q.get("id") == "dup" for q in self.st["pending_questions"]):
            self.st["pending_questions"].append({"id": "dup", "text": "עותקים ידניים של שיעורים ביומן האישי", "asked": P.iso(self.now)})
            lines += ["", "💡 יש ביומן האישי עותקים ידניים של שיעורים. כדאי למחוק אותם, כדי שכל שיעור יופיע פעם אחת."]
        lines += [""] + warn if warn else []
        self.tg.send("\n".join(lines).strip(), buttons or None)

    def recap_buttons(self, d: date, courses: list[str]):
        rated = {r["course"]: r.get("rating") for r in self.st["recaps"] if r.get("date") == d.isoformat()}
        rows = []
        for c in courses:
            row = [(COURSES[c]["name"][:10], "noop")]
            for n in range(1, 6):
                row.append((f"•{n}•" if rated.get(c) == n else str(n), f"rate|{c}|{d.isoformat()}|{n}"))
            rows.append(row)
        rows.append([("✅ הכול ברור (5 לכולם)", f"rateall|{d.isoformat()}")])
        return rows

    def job_recap(self, today: date):
        uni, _ = self.load_uni()
        if getattr(self, "_feed_error", None):
            raise RuntimeError(self._feed_error)
        courses = sorted({l.course for l in uni.lessons if l.day == today}, key=P.COURSE_ORDER.index)
        if not courses:
            return
        self.st["bot"].setdefault("recap_sent", {}).clear()
        self.st["bot"]["recap_sent"][today.isoformat()] = P.iso(self.now)
        names = " · ".join(COURSES[c]["name"] for c in courses)
        text = (f"🚆 ריקאפ · {dd(today)}\n{names}\n\nכמה הבנת היום? 1 = לא הבנתי, 5 = שולט.\n\n"
                "ואז כתוב לי בחופשיות: מה נלמד, מה לא היה ברור, מטלות חדשות (מה, עד מתי, כמה זמן), "
                "או שינוי בזמני הלימוד.")
        mid = self.tg.send(text, self.recap_buttons(today, courses))
        self.st["bot"]["msgs"][str(mid)] = {"type": "recap", "date": today.isoformat(), "courses": courses}
        self.st["bot"]["pending"] = {"type": "recap", "date": today.isoformat(), "courses": courses,
                                    "until": P.iso(self.now + timedelta(hours=8))}

    def job_recapr(self, today: date):
        if any(r.get("date") == today.isoformat() for r in self.st["recaps"]):
            return
        self.tg.send("🚆 תזכורת קטנה לריקאפ: גם \"הכול ברור\" זו תשובה. כפתור אחד מספיק.")

    def daily_buttons(self, d: date):
        rows = []
        idx = {s["key"]: s for s in self.st["sessions"]}
        keys = self.st["bot"]["msgs"].get(f"daily:{d.isoformat()}", {}).get("keys", [])
        for k in keys:
            s = idx.get(k)
            if not s:
                continue
            name = "חזרה שבועית" if s.get("kind") == "weekly" else COURSES[s["course"]]["name"]
            t = f"{name} {s['start'][11:16]}"
            st = s.get("status")
            short = self.short(k)
            rows.append([(("✅ " if st == "done" else "") + f"בוצע · {t}", f"s|done|{short}"),
                         (("❌ " if st == "missed" else "") + "לא", f"s|miss|{short}")])
        if keys:
            rows.append([("✅ הכול בוצע", f"alldone|{d.isoformat()}")])
            cur = self.st["bot"]["durations"].get(d.isoformat())
            rows.append([((("•" + lab + "•") if cur == v else lab), f"dur|{d.isoformat()}|{v}")
                         for lab, v in (("⏱ כמתוכנן", "same"), ("פחות", "less"), ("יותר", "more"))])
        return rows

    def job_daily(self, today: date):
        uni, _ = self.load_uni()
        personal = self.load_personal(P.week_start(today))
        rd = P.review_data(today, uni, personal, self.st)
        keys = []
        for s in rd["unanswered_past_sessions"] + rd["today_sessions"]:
            if s["key"] not in keys and s.get("status", "planned") in ("planned", "done", "missed"):
                keys.append(s["key"])
        lines = [f"🌙 סיכום יום · {dd(today)}"]
        if keys:
            lines.append("סמן מה בוצע (מה שלא יסומן עד הבוקר ישובץ מחדש):")
        else:
            lines.append("לא היו סשנים היום.")
        prog = rd["week_progress"]
        if prog:
            lines += ["", "📈 השבוע: " + " · ".join(f"{n} {v['done']}/{v['planned']}" for n, v in prog.items())]
        t = rd["tomorrow"]
        tl = [f"• {l['start']}–{l['end']} {l['course']}" for l in t["lessons"]] + [f"• {s['start']} {s['title']}" for s in t["sessions"]]
        if getattr(self, "_feed_error", None):
            lines += ["", "⚠️ לא הצלחתי לקרוא את המערכת עכשיו, אז השיעורים של מחר אולי חסרים."]
        lines += ["", f"מחר · {t['day']} {date.fromisoformat(t['date']).day}.{date.fromisoformat(t['date']).month}:"] + (tl or ["• יום פנוי"])
        if rd["assignments_next_7_days"]:
            lines += ["", "📝 מטלות קרובות:"] + [f"• {COURSES.get(a['course'], {}).get('name', a['course'])}: {a['title']} עד {a['due']}" for a in rd["assignments_next_7_days"]]
        openq = [q for q in rd["pending_questions"] if not str(q.get("id", "")).startswith(("dup", "cap-"))][-3:]
        if openq:
            lines += ["", "❔ פתוח:"] + [f"• {q['text']}" for q in openq[:3]]
        lines += ["", "אפשר לכתוב לי קיבולת למחר (\"מחר פנוי כל היום\" / \"מחר רק סשן אחד\")."]
        self.st["bot"]["msgs"][f"daily:{today.isoformat()}"] = {"keys": keys}
        mid = self.tg.send("\n".join(lines), self.daily_buttons(today) or None)
        self.st["bot"]["msgs"][str(mid)] = {"type": "daily", "date": today.isoformat()}

    def job_weekly(self, today: date):
        uni, personal, fc, changes = self.feed_and_adopt(today)
        ws = P.week_start(today)
        rep = P.week_report(ws, self.st, self.now)
        nxt = today + timedelta(days=1)
        lines = [f"📊 סיכום שבוע · {ws.day}.{ws.month}–{today.day}.{today.month}"]
        if rep["total_planned"]:
            pct = round(100 * rep["total_done"] / rep["total_planned"])
            lines.append(f"בוצעו {rep['total_done']} מתוך {rep['total_planned']} ({pct}%)")
            for n, v in rep["by_course"].items():
                ok = v["done"] >= v["planned"] and v["planned"] > 0
                lines.append(f"{'✅' if ok else '⚠️'} {n} {v['done']}/{v['planned']}")
        else:
            lines.append("עוד אין נתונים לשבוע הזה.")
        if rep["suggest_lighter_load"]:
            lines += ["", "שבועיים ברצף מתחת ל-70%. רוצה שאוריד קצת עומס בשבוע הבא?"]
        if rep["weak_topics"]:
            lines += ["", "🔍 חלש השבוע:"] + [f"• {w['course']}: {w.get('unclear') or w.get('topics') or ''} ({w.get('rating') or '?'}/5)" for w in rep["weak_topics"][:5]]
        fl = self.feed_lines(fc)
        if fl:
            lines += ["", "🔄 שינויים במערכת:"] + fl
        buttons = []
        if fc["block"]:
            lines += ["", "⚠️ לא שיבצתי את השבוע הבא: " + "; ".join(fc["reasons"])]
        else:
            p = self.sync_week(nxt, uni, personal)
            lines += ["", "🗓 השבוע הבא:"] + P.plan_text(p).split("\n")[1:]
            if p["capped"]:
                lines += ["השבוע מלא (11). לא שיבצתי: " + ", ".join(x["title"] for x in p["capped"])]
        ex = P.exc_notify(nxt, uni, personal, self.st, True)
        for x in ex["new"]:
            if x["type"] == "class_on_free_day":
                continue
            lines.append(f"• {x['text']}")
            buttons.append([(f"👍 קיבלתי · {x['text'][:30]}", f"ack|{x['id']}")])
        if rep["weeks_since_start"] == 2:
            lines += ["", "שאלה לכל קורס: החומר מצטבר (כל שיעור נבנה על הקודם) או שמספיק רענון כל 3 שבועות?"]
            for c in P.COURSE_ORDER:
                buttons.append([(COURSES[c]["name"], "noop"), ("מצטבר", f"kind|{c}|cumulative"), ("רענון", f"kind|{c}|refresh")])
        lines += ["", "לשנות משהו בשבוע הבא? פשוט כתוב לי."]
        self.tg.send("\n".join(lines), buttons or None)

    # ------------------------------------------------------------ telegram updates
    def handle_updates(self):
        b = self.st["bot"]
        ups = self.tg.get_updates(b["offset"])
        for i, u in enumerate(ups):
            b["offset"] = u["update_id"] + 1
            if i and i % 5 == 0:
                self.save_state()
            try:
                if "callback_query" in u:
                    self.on_callback(u["callback_query"])
                elif "message" in u and u["message"].get("text"):
                    self.on_text(u["message"])
            except Exception as e:  # noqa: BLE001
                self.log.append(f"update: {type(e).__name__}: {str(e)[:200]}")
                try:
                    self.tg.send(f"⚠️ משהו נכשל בטיפול בהודעה: {str(e)[:150]}")
                except Exception:  # noqa: BLE001
                    pass
        if ups:
            self.save_state()

    def authorized(self, chat_id) -> bool:
        return str(chat_id) == str(self.tg.chat_id)

    def on_callback(self, q: dict):
        msg = q.get("message", {})
        if not self.authorized(msg.get("chat", {}).get("id")):
            return
        data, mid = q.get("data", ""), msg.get("message_id")
        parts = data.split("|")
        kind, reply = parts[0], ""
        if kind == "noop":
            self.tg.answer(q["id"])
            return
        if kind == "rate":
            _, c, d, n = parts
            self.add_recap(d, c, rating=int(n))
            reply = f"נרשם: {COURSES[c]['name']} {n}/5"
            meta = self.st["bot"]["msgs"].get(str(mid), {})
            self.tg.edit_buttons(mid, self.recap_buttons(date.fromisoformat(d), meta.get("courses", [c])))
        elif kind == "rateall":
            d = parts[1]
            meta = self.st["bot"]["msgs"].get(str(mid), {})
            for c in meta.get("courses", []):
                if not any(r["date"] == d and r["course"] == c for r in self.st["recaps"]):
                    self.add_recap(d, c, rating=5)
            reply = "נרשם. יום טוב!"
            self.tg.edit_buttons(mid, self.recap_buttons(date.fromisoformat(d), meta.get("courses", [])))
        elif kind == "s":
            _, act, short = parts
            s = self.by_short(short)
            if not s:
                reply = "לא מצאתי את הסשן"
            else:
                self.mark(s, "done" if act == "done" else "missed")
                reply = "✅ נרשם כבוצע" if act == "done" else "❌ נרשם. ישובץ מחדש בבוקר"
                self.refresh_daily(mid)
        elif kind == "alldone":
            d = parts[1]
            for k in self.st["bot"]["msgs"].get(f"daily:{d}", {}).get("keys", []):
                s = next((x for x in self.st["sessions"] if x["key"] == k), None)
                if s and s.get("status") in ("planned", "missed"):
                    self.mark(s, "done")
            reply = "✅ הכול נרשם כבוצע"
            self.refresh_daily(mid)
        elif kind == "dur":
            _, d, v = parts
            self.st["bot"]["durations"][d] = v
            reply = "נרשם"
            self.refresh_daily(mid)
        elif kind == "ack":
            self.st["acked"] = sorted(set(self.st["acked"]) | {parts[1]})
            reply = "👍 נרשם"
        elif kind == "kind":
            _, c, v = parts
            self.st["course_kind"][c] = v
            reply = f"{COURSES[c]['name']}: {'מצטבר' if v == 'cumulative' else 'רענון'}"
        elif kind == "restore":
            s = self.by_short(parts[1])
            if s and s.get("status") == "dropped":
                self.restore(s)
                reply = "↩️ הוחזר ליומן"
            else:
                reply = "כבר ביומן"
        self.tg.answer(q["id"], reply)

    def refresh_daily(self, mid):
        meta = self.st["bot"]["msgs"].get(str(mid), {})
        if meta.get("type") == "daily":
            self.tg.edit_buttons(mid, self.daily_buttons(date.fromisoformat(meta["date"])))

    def add_recap(self, d: str, course: str, rating=None, topics="", unclear=""):
        cur = next((r for r in self.st["recaps"] if r["date"] == d and r["course"] == course), None)
        if not cur:
            cur = {"date": d, "course": course, "rating": None, "topics": "", "unclear": "", "at": P.iso(self.now)}
            self.st["recaps"].append(cur)
        if rating is not None:
            cur["rating"] = rating
        if topics:
            cur["topics"] = (cur["topics"] + "; " if cur["topics"] else "") + topics
        if unclear:
            cur["unclear"] = (cur["unclear"] + "; " if cur["unclear"] else "") + unclear

    # ------------------------------------------------------------ free text
    def on_text(self, m: dict):
        chat = m["chat"]["id"]
        text = m["text"].strip()
        if not self.tg.chat_id:
            self.tg.send(f"ה-chat id שלך: {chat}\nהדבק אותו ב-GitHub כסוד TELEGRAM_CHAT_ID.", chat_id=str(chat))
            return
        if not self.authorized(chat):
            return
        cmd = text.split()[0].lower()
        today = self.now.date()
        if cmd in ("/start", "/help", "עזרה"):
            self.tg.send(HELP)
            return
        if cmd in ("/today", "היום"):
            self.tg.send(self.day_text(today))
            return
        if cmd in ("/tomorrow", "מחר"):
            self.tg.send(self.day_text(today + timedelta(days=1)))
            return
        if cmd in ("/week", "שבוע"):
            uni, _ = self.load_uni()
            p = P.plan_week(P.week_start(today), uni, self.load_personal(P.week_start(today)), self.st)
            self.tg.send(P.plan_text(p))
            return
        self.free_text(text)

    def day_text(self, d: date) -> str:
        uni, _ = self.load_uni()
        if getattr(self, "_feed_error", None):
            return f"⚠️ {self._feed_error}"
        lines = [f"📅 {dd(d)}"] + self.lessons_lines(uni, d) + [self.sess_line(s) for s in self.sessions_on(d)]
        return "\n".join(lines) if len(lines) > 1 else f"📅 {dd(d)}: אין שיעורים ואין סשנים."

    def context_for_llm(self) -> str:
        today = self.now.date()
        up = [s for s in self.st["sessions"] if s.get("status") in ("planned", "done", "missed", "dropped")
              and (today - timedelta(days=3)).isoformat() <= s["start"][:10] <= (today + timedelta(days=10)).isoformat()]
        sess = [{"id": self.short(s["key"]), "title": strip_marks(P._session_from_state(s).title()),
                 "start": s["start"][:16], "status": s.get("status")} for s in sorted(up, key=lambda s: s["start"])]
        pend = self.st["bot"].get("pending")
        if pend and pend.get("until", "") < P.iso(self.now):
            pend = None
        return json.dumps({"now": P.iso(self.now), "today": f"{HEB_DAY[today.weekday()]} {today.isoformat()}",
                           "courses": {k: v["name"] for k, v in COURSES.items()},
                           "sessions": sess, "recap_context": pend,
                           "open_assignments": [a for a in self.st["assignments"] if a.get("status") != "done"]},
                          ensure_ascii=False)

    def free_text(self, text: str):
        if not self.llm:
            self.st["bot"]["notes"].append({"at": P.iso(self.now), "text": text})
            self.tg.send("רשמתי. (בלי מפתח Claude אני מבין רק כפתורים ופקודות: /today /tomorrow /week)")
            return
        try:
            raw = self.llm.complete(LLM_SYSTEM, f"הקשר:\n{self.context_for_llm()}\n\nההודעה של עודד:\n{text}")
        except Exception:  # noqa: BLE001
            self.st["bot"]["notes"].append({"at": P.iso(self.now), "text": text})
            self.tg.send("שמרתי את ההודעה, אבל לא הצלחתי לפענח אותה עכשיו. נסה שוב עוד מעט או השתמש בכפתורים.")
            return
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            plan = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            plan = {}
        done = []
        for a in plan.get("actions", [])[:12]:
            try:
                r = self.apply_action(a)
                if r:
                    done.append(r)
            except Exception as e:  # noqa: BLE001
                done.append(f"⚠️ לא ביצעתי {a.get('action')}: {str(e)[:80]}")
        reply = (plan.get("reply") or "").strip()
        body = "\n".join(([reply] if reply else []) + done) or "קיבלתי."
        self.tg.send(body)

    def apply_action(self, a: dict) -> str:
        act = a.get("action")
        if act in ("done", "missed"):
            s = self.by_short(a.get("id", ""))
            if not s:
                raise ValueError("סשן לא ידוע")
            self.mark(s, "done" if act == "done" else "missed")
            return f"{'✅' if act == 'done' else '❌'} {strip_marks(P._session_from_state(s).title())}"
        if act == "recap":
            c = a.get("course")
            if c not in COURSES:
                raise ValueError("קורס לא ידוע")
            d = a.get("date") or self.now.date().isoformat()
            rating = a.get("rating")
            rating = int(rating) if rating not in (None, "") and 1 <= int(rating) <= 5 else None
            self.add_recap(d, c, rating, a.get("topics", ""), a.get("unclear", ""))
            return f"📝 ריקאפ {COURSES[c]['name']} נשמר"
        if act == "assignment":
            c = a.get("course")
            if c not in COURSES:
                raise ValueError("קורס לא ידוע")
            due = a.get("due")
            date.fromisoformat(due) if due else None
            for y in self.st["assignments"]:
                if y["course"] == c and y["title"] == a.get("title", "") and y.get("due") == due:
                    return f"📝 המטלה כבר רשומה: {y['title']}"
            x = {"id": f"{c}-{sid(c + a.get('title', '') + str(due))[:5]}", "course": c, "title": a.get("title", ""),
                 "due": due, "est_min": a.get("est_min"), "status": "open", "added": P.iso(self.now)}
            self.st["assignments"].append(x)
            return f"📝 מטלה: {COURSES[c]['name']} · {x['title']} עד {due or '?'}" + ("" if x["est_min"] else " (כמה זמן זה ייקח?)")
        if act == "assignment_done":
            for x in self.st["assignments"]:
                if x["id"] == a.get("id"):
                    x["status"] = "done"
                    return f"✅ מטלה {x['title']}"
            raise ValueError("מטלה לא ידועה")
        if act == "move":
            s = self.by_short(a.get("id", ""))
            if not s or s.get("status") != "planned":
                raise ValueError("אפשר להזיז רק סשן מתוכנן")
            st0 = datetime.fromisoformat(a["start"])
            st0 = st0.replace(tzinfo=TZ) if st0.tzinfo is None else st0.astimezone(TZ)
            en = st0 + timedelta(minutes=P.CONFIG["session_min"])
            if en.time() > time(23, 0) or en.date() != st0.date():
                if not a.get("late_ok"):
                    raise ValueError("זה מסתיים אחרי 23:00. אם אתה רוצה בכל זאת, תגיד במפורש")
            uni, _ = self.load_uni()
            clash = [b for b in P.busy_blocks(st0.date(), uni, self.load_personal(st0.date()))
                     if st0 < b[1] and b[0] < en and b[2] == "lesson"]
            if clash:
                raise ValueError("יש שיעור בזמן הזה")
            if s.get("eventId"):
                self.cal.patch(self.reviews_cal, s["eventId"], {"start": {"dateTime": st0.isoformat(), "timeZone": "Asia/Jerusalem"},
                                                               "end": {"dateTime": en.isoformat(), "timeZone": "Asia/Jerusalem"}})
            s["start"], s["end"] = P.iso(st0), P.iso(en)
            return f"↔️ {strip_marks(P._session_from_state(s).title())} → {dd(st0.date())} {P.fmt_t(st0)}"
        if act == "restore":
            s = self.by_short(a.get("id", ""))
            if not s or s.get("status") != "dropped":
                raise ValueError("אין מה להחזיר")
            self.restore(s)
            return f"↩️ הוחזר: {strip_marks(P._session_from_state(s).title())}"
        if act == "capacity":
            self.st["pending_questions"].append({"id": f"cap-{a.get('date', '')}", "text": f"קיבולת {a.get('date', '')}: {a.get('text', '')}", "asked": P.iso(self.now)})
            return "📌 רשמתי. בינתיים אני לא מסדר את השבוע מחדש לבד לפי זה: תגיד לי מה להזיז, ואזיז."
        if act == "note":
            self.st["bot"]["notes"].append({"at": P.iso(self.now), "text": a.get("text", "")})
            return ""
        return ""


HELP = """אני בוט הלימודים שלך 📚
• בבוקר (7:44): מה יש היום, שינויים במערכת, מה צריך החלטה.
• 20 דק׳ אחרי השיעור האחרון: ריקאפ. ציון בכפתור, ואת השאר כותבים חופשי.
• 21:30: מה בוצע היום, ומה מחר.
• מוצ״ש 21:00: סיכום שבוע ותוכנית לשבוע הבא.

פקודות: היום · מחר · שבוע
אפשר לכתוב חופשי, למשל: "עשיתי את מתמטיקה", "תזיז את כלכלה של מחר ל-10:00", "מטלה באקסל עד 5.11, שעתיים"."""

LLM_SYSTEM = """אתה מנוע הפענוח של בוט לימודים לסטודנט בשם עודד. אתה מקבל הקשר (JSON) והודעה חופשית ממנו,
ומחזיר JSON בלבד, בלי טקסט מסביב:
{"actions":[...], "reply":"תשובה קצרה בעברית, משפט או שניים"}

פעולות מותרות (השתמש רק ב-id מתוך sessions בהקשר, לעולם אל תמציא):
{"action":"done","id":"..."}            סשן בוצע
{"action":"missed","id":"..."}          סשן לא בוצע
{"action":"recap","course":"math|intro|econ|stats|excel|law|mgmt","date":"YYYY-MM-DD","rating":1-5 או null,"topics":"מה נלמד","unclear":"מה לא ברור"}
{"action":"assignment","course":"...","title":"...","due":"YYYY-MM-DD או null","est_min":מספר או null}
{"action":"assignment_done","id":"..."}
{"action":"move","id":"...","start":"YYYY-MM-DDTHH:MM","late_ok":false}   late_ok=true רק אם עודד ביקש במפורש ללמוד אחרי 23:00
{"action":"restore","id":"..."}         להחזיר סשן שנמחק (status=dropped)
{"action":"capacity","date":"YYYY-MM-DD","text":"מה אמר על הזמן הפנוי"}
{"action":"note","text":"..."}           כל מידע אחר שכדאי לזכור

כללים:
- אם recap_context פעיל ועודד מתאר שיעורים, צור recap לכל קורס שהזכיר, בתאריך של recap_context.
- תאריכים יחסיים ("מחר", "ביום רביעי") מחשבים מ-now.
- אם משהו לא ברור, אל תנחש פעולה: שאל ב-reply ו-actions ריק.
- אתה לא מרצה ולא מסביר חומר. אם הוא שואל שאלה על לוח הזמנים, ענה מתוך ההקשר בלבד."""
