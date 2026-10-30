"""
מפת חומר (יעד 1): כל קורס מתפרק לנושאי הסילבוס, ולכל נושא יש מצב:
  נלמד בשיעור (תאריכים) · נחזר (תאריכים, ו-est=True כשזה הוערך ולא נאמר) · הבנה אחרונה (1-5) · מה לא היה ברור.
המפה מתמלאת מהריקאפ ("מה עברתם") ומדיווחי לימוד. שיוך תשובה חופשית לנושא נעשה ב-Claude (topic_ids),
ובגיבוי התאמת מילים. כל שיוך מוצג לעודד ואפשר לתקן אותו (פעולה topic_set).
שדות מצב: st["topics"][course] = [{"name", "learned":[iso], "reviewed":[{"d","est"}], "rating", "rated_at", "unclear"}]
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import planner as P
from bot import semester as SEM

COURSES = P.COURSES
STOP = {"של", "עם", "על", "אל", "את", "הוא", "היא", "גם", "כל", "לא", "אבל", "או", "זה", "מה", "בין", "אחרי", "לפני", "עוד",
        "המשך", "שיעור", "הקודם", "נושא", "פרק", "עברנו", "למדנו", "היום", "קצת"}
UNREVIEWED_AFTER_DAYS = 5     # נושא שנלמד ולא נחזר מעבר לכך נחשב "ממתין לחזרה"
WEAK_MAX = 3


def course_topics(c: str) -> list[str]:
    """שמות הנושאים של הקורס מהסילבוס, בסדר: כלכלה לפי שבועות, ניהול לפי מפגשים, השאר לפי רשימת הנושאים."""
    if c in SEM.WEEKLY_TOPICS:
        m = SEM.WEEKLY_TOPICS[c]
        return [m[k] for k in sorted(m)]
    if c in SEM.DATED_TOPICS:
        m = SEM.DATED_TOPICS[c]
        return [m[k] for k in sorted(m)]
    return list(SEM.TOPICS.get(c, []))


def _new(name: str) -> dict:
    return {"name": name, "learned": [], "reviewed": [], "rating": None, "rated_at": None, "unclear": ""}


def ensure(st: dict, c: str) -> list[dict]:
    """רשימת הנושאים של הקורס במצב. נושא חדש בסילבוס נוסף בסוף, נושא קיים נשמר לפי שם."""
    tp = st.setdefault("topics", {}).setdefault(c, [])
    have = {t["name"] for t in tp}
    cur = course_topics(c)
    for n in course_topics(c):
        if n not in have:
            tp.append(_new(n))
    for t in tp:
        t["stale"] = t["name"] not in cur       # נושא שהוסר/שונה בסילבוס: נשמר, אך לא נספר ולא מוצג
    return tp


def live(st: dict, c: str) -> list[tuple[int, dict]]:
    return [(i, t) for i, t in enumerate(ensure(st, c)) if not t.get("stale")]


def index_of(st: dict, c: str, name: str):
    for i, t in live(st, c):
        if t["name"] == name:
            return i
    return None


def _stem(w: str) -> str:
    return w[1:] if len(w) >= 5 and w[0] in "הוכלמשב" else w


def _words(s: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[\u0590-\u05FFa-zA-Z0-9]+", s or "") if len(w) >= 3 and w not in STOP]


def _hit(tok: str, words: list[str]) -> bool:
    if len(tok) < 4:
        return tok in words
    return any(len(w) >= 4 and (w.startswith(tok[:4]) or tok.startswith(w[:4])) for w in words)


def match(st: dict, c: str, text: str) -> list[int]:
    """גיבוי ל-Claude. שם זהה בדיוק מנצח. אחרת נושא אחד שמוביל בבירור במילים תואמות. תיקו = לא משייכים."""
    text = (text or "").strip()
    for i, t in live(st, c):
        if t["name"] == text:
            return [i]
    words = _words(text)
    if not words:
        return []
    scored = []
    for i, t in live(st, c):
        toks = _words(t["name"])
        h = sum(1 for k in toks if _hit(k, words))
        if h:
            scored.append((h, h / max(len(toks), 1), i))
    if not scored:
        return []
    top = max((h, r) for h, r, _ in scored)
    best = [i for h, r, i in scored if (h, r) == top]
    return best if len(best) == 1 else []


def valid_ids(st: dict, c: str, ids) -> list[int]:
    n = len(ensure(st, c))
    out = []
    for x in ids or []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if 0 <= i < n and i not in out:
            out.append(i)
    return out


def mark_learned(st: dict, c: str, d: str, idxs: list[int], rating=None, unclear: str = ""):
    tp = ensure(st, c)
    for i in idxs:
        t = tp[i]
        if d not in t["learned"]:
            t["learned"].append(d)
            t["learned"].sort()
        newest = not t["rated_at"] or d >= t["rated_at"]      # דירוג ישן לא דורס חדש יותר
        if rating is not None and newest:
            t["rating"], t["rated_at"] = rating, d
        if unclear and newest:
            t["unclear"] = unclear[:120]


def mark_reviewed(st: dict, c: str, d: str, idxs: list[int], est: bool = False):
    tp = ensure(st, c)
    for i in idxs:
        t = tp[i]
        if not any(r["d"] == d and r["est"] == est for r in t["reviewed"]):
            t["reviewed"].append({"d": d, "est": est})


def unreview_est(st: dict, c: str, d: str, idxs: list[int]):
    tp = ensure(st, c)
    for i in idxs:
        if 0 <= i < len(tp):
            tp[i]["reviewed"] = [r for r in tp[i]["reviewed"] if not (r["est"] and r["d"] == d)]


def next_unlearned(st: dict, c: str, n: int = 3) -> list[int]:
    """הנושאים הבאים בתור שעוד לא נלמדו (השיעורים מתקדמים לפי סדר הסילבוס)."""
    return [i for i, t in live(st, c) if not t["learned"]][:n]


def clear_topic(st: dict, c: str, idxs: list[int], d: str | None = None):
    """בלי תאריך: מאפס את הנושא. עם תאריך: מסיר רק את הלימוד והחזרה של אותו יום (ואת הדירוג אם ניתן בו)."""
    tp = ensure(st, c)
    for i in idxs:
        t = tp[i]
        if d is None:
            t.update(learned=[], reviewed=[], rating=None, rated_at=None, unclear="")
            continue
        t["learned"] = [x for x in t["learned"] if x != d]
        t["reviewed"] = [r for r in t["reviewed"] if r["d"] != d]
        if t["rated_at"] == d:
            t["rating"], t["rated_at"], t["unclear"] = None, None, ""
    for r in st.get("recaps", []):
        if r.get("course") == c and (d is None or r.get("date") == d):
            r["tids"] = [x for x in r.get("tids", []) if x not in idxs]


def reviewed_real(t: dict) -> list[str]:
    return [r["d"] for r in t["reviewed"] if not r["est"]]


def is_weak(t: dict) -> bool:
    """הבנה 3 ומטה שלא נסגרה בחזרה אמיתית אחרי הדירוג."""
    if t["rating"] is None or t["rating"] > WEAK_MAX:
        return False
    return not any(d >= (t["rated_at"] or "") for d in reviewed_real(t))


def pending_review(t: dict, today: date) -> bool:
    """נלמד לפני יותר מכמה ימים ועדיין לא נחזר (גם לא בהערכה)."""
    if not t["learned"] or t["reviewed"]:
        return False
    return date.fromisoformat(t["learned"][0]) <= today - timedelta(days=UNREVIEWED_AFTER_DAYS)


def stats(st: dict, c: str, today: date) -> dict:
    tp = [t for _, t in live(st, c)]
    return {"total": len(tp), "learned": sum(1 for t in tp if t["learned"]),
            "reviewed": sum(1 for t in tp if t["reviewed"]),
            "reviewed_est": sum(1 for t in tp if t["reviewed"] and not reviewed_real(t)),
            "weak": [t["name"] for t in tp if is_weak(t)],
            "pending": [t["name"] for t in tp if pending_review(t, today)]}


def infer_review(st: dict, c: str, d: str) -> list[int]:
    """סשן או דיווח לימוד בלי נושא: מוערך שנחזר הנושא הוותיק ביותר שנלמד ועוד לא נחזר (אחד). מסומן est."""
    tp = ensure(st, c)
    cand = [(t["learned"][0], i) for i, t in live(st, c) if t["learned"] and not t["reviewed"] and t["learned"][0] <= d]
    if not cand:
        return []
    i = min(cand)[1]
    mark_reviewed(st, c, d, [i], est=True)
    return [i]


def label(st: dict, c: str, idxs: list[int]) -> str:
    tp = ensure(st, c)
    return ", ".join(tp[i]["name"].split(":")[0][:40] for i in idxs[:3])


def coverage_line(st: dict, c: str, today: date) -> str:
    s = stats(st, c, today)
    bits = [f"{s['learned']}/{s['total']} נלמדו", f"{s['reviewed']} נחזרו" + (f" ({s['reviewed_est']} בהערכה)" if s["reviewed_est"] else "")]
    if s["weak"]:
        bits.append(f"{len(s['weak'])} חלשים")
    return f"{COURSES[c]['name']}: " + " · ".join(bits)


def material_text(st: dict, today: date, only: str | None = None) -> str:
    lines = ["📚 מפת החומר"]
    for c in P.COURSE_ORDER:
        if only and c != only:
            continue
        tp = [t for _, t in live(st, c)]
        if not tp:
            continue
        lines += ["", coverage_line(st, c, today)]
        for t in tp:
            mark = "⬜"
            if t["learned"]:
                mark = "🟩" if t["reviewed"] else "🟨"
            if is_weak(t):
                mark = "🟥"
            lines.append(f"{mark} {t['name'][:55]}" + (f" · הבנה {t['rating']}/5" if t["rating"] else "")
                         + (f" · לא ברור: {t['unclear'][:40]}" if is_weak(t) and t["unclear"] else ""))
    lines += ["", "⬜ לא נלמד · 🟨 נלמד · 🟩 נחזר · 🟥 חלש. כתוב לי אם שיוך לא נכון (\"לא למדנו את X\")."]
    return "\n".join(lines)
