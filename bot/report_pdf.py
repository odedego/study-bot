"""
דוח שבועי ב-PDF (שלב K). פונקציה טהורה: מילון נתונים ← בייטים של PDF. בלי רשת, בלי מצב.
עיצוב: עברית RTL, גופן Heebo (בתיקיית fonts, רישיון OFL), כרטיסי סיכום, פסי כיסוי חומר, גרף לימוד לפי קורס, ספירה לאחור למבחנים.
אם WeasyPrint לא מותקן או נכשל, הקורא חוזר להודעת הטקסט (build_pdf מעלה חריגה).
מבנה הנתונים (data):
  title, range, verdict, total_min, prev_total_min, recaps:(have,need)|None
  courses: [{name, min, prev_min, learned, total, reviewed, weak:[{topic, rating, unclear}], pending:[topic]}]
  exams: [{name, date, days}]    next_week: [str]    gaps: [str]    tips: [str]
"""
from __future__ import annotations

import html
import os

FONT = os.path.join(os.path.dirname(__file__), "fonts", "Heebo.ttf")
PALETTE = ["#2563EB", "#0D9488", "#D97706", "#7C3AED", "#DB2777", "#4B5563", "#65A30D"]
GREEN, AMBER, GREY, RED = "#16A34A", "#F59E0B", "#E5E7EB", "#DC2626"


def _e(x) -> str:
    return html.escape(str(x))


def hm(minutes: int) -> str:
    h, m = divmod(int(minutes or 0), 60)
    if h and m:
        return f"{h}:{m:02d} ש׳"
    return f"{h} ש׳" if h else f"{m} דק׳"


def coverage_bar(learned: int, reviewed: int, total: int) -> str:
    """פס כיסוי ב-HTML (RTL: ההתחלה מימין). ירוק = נחזר, כתום = נלמד ועוד לא נחזר, אפור = עוד לא נלמד."""
    total = max(total, 1)
    rv = min(reviewed, learned)
    segs = [(rv, GREEN), (learned - rv, AMBER), (total - learned, GREY)]
    return '<div class="bar">' + "".join(f'<i style="width:{100 * n / total:.2f}%;background:{col}"></i>' for n, col in segs if n > 0) + "</div>"


def minutes_chart(courses: list[dict]) -> str:
    """עמודות אופקיות של לימוד השבוע לפי קורס. הסימון השחור = השבוע הקודם."""
    rows = [c for c in courses if c.get("min") or c.get("prev_min")]
    if not rows:
        return '<p class="muted">לא נרשם לימוד בשבוע הזה.</p>'
    mx = max(max(c.get("min", 0), c.get("prev_min", 0)) for c in rows) or 1
    out = []
    for i, c in enumerate(rows):
        col = PALETTE[i % len(PALETTE)]
        w = 100 * c.get("min", 0) / mx
        pv = c.get("prev_min", 0)
        mark = f'<b class="prev" style="right:{100 * pv / mx:.2f}%"></b>' if pv else ""
        out.append(f'<div class="mrow"><span class="ml">{_e(c["name"])}</span><span class="track"><i style="width:{max(w, 0.8):.2f}%;background:{col}"></i>{mark}</span>'
                   f'<span class="mv">{hm(c.get("min", 0))}</span></div>')
    return "".join(out)


def _cards(data: dict) -> str:
    tot, prev = data.get("total_min", 0), data.get("prev_total_min", 0)
    delta = ""
    if prev:
        d = tot - prev
        delta = f'<div class="sub {"up" if d >= 0 else "down"}">{"▲" if d >= 0 else "▼"} {hm(abs(d))} מול שבוע קודם</div>'
    learned = sum(c["learned"] for c in data["courses"])
    total = sum(c["total"] for c in data["courses"])
    weak = sum(len(c["weak"]) for c in data["courses"])
    rc = data.get("recaps")
    cards = [("לימוד השבוע", hm(tot), delta),
             ("כיסוי חומר", f"{learned}/{total}", '<div class="sub">נושאים שנלמדו</div>'),
             ("נושאים חלשים", str(weak), '<div class="sub">הבנה 3 ומטה</div>')]
    if rc:
        cards.append(("ריקאפים", f"{rc[0]}/{rc[1]}", '<div class="sub">שיעורים עם ריקאפ</div>'))
    return '<div class="cards">' + "".join(f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div>{s}</div>' for k, v, s in cards) + "</div>"


def build_html(data: dict) -> str:
    css = f"""
@font-face {{ font-family: Heebo; src: url('file://{FONT}'); font-weight: 100 900; }}
@page {{ size: A4; margin: 16mm 14mm 16mm 14mm; @bottom-center {{ content: counter(page) " / " counter(pages); font: 9pt Heebo; color: #9CA3AF; direction: ltr; unicode-bidi: bidi-override; }} }}
html {{ direction: rtl; font-family: Heebo, sans-serif; color: #111827; font-size: 11pt; line-height: 1.45; }}
h1 {{ font-size: 24pt; font-weight: 800; margin: 0 0 2pt; }}
h2 {{ font-size: 14pt; font-weight: 700; margin: 18pt 0 6pt; padding-bottom: 3pt; border-bottom: 2px solid #E5E7EB; }}
.range {{ color: #6B7280; font-size: 11pt; }}
.verdict {{ margin: 8pt 0 12pt; font-size: 12pt; }}
.cards {{ display: flex; gap: 8pt; }}
.card {{ flex: 1; background: #F3F4F6; border-radius: 10pt; padding: 9pt 10pt; }}
.card .k {{ color: #6B7280; font-size: 9pt; }} .card .v {{ font-size: 20pt; font-weight: 800; }}
.sub {{ font-size: 8.5pt; color: #6B7280; }} .up {{ color: {GREEN}; }} .down {{ color: {RED}; }}
.course {{ margin: 8pt 0 10pt; page-break-inside: avoid; }}
.course .name {{ font-weight: 700; font-size: 11.5pt; display: flex; justify-content: space-between; }}
.course .meta {{ font-size: 9pt; color: #6B7280; }}
.legend span {{ display: inline-block; margin-left: 10pt; font-size: 9pt; color: #4B5563; }}
.dot {{ display: inline-block; width: 8pt; height: 8pt; border-radius: 4pt; margin-left: 3pt; }}
.weak {{ border-right: 4px solid {RED}; background: #FEF2F2; padding: 5pt 8pt; margin: 4pt 0; border-radius: 6pt; page-break-inside: avoid; }}
.weak b {{ color: #991B1B; }} .weak .why {{ font-size: 9.5pt; color: #4B5563; }}
.exam {{ display: inline-block; background: #EFF6FF; border-radius: 10pt; padding: 6pt 10pt; margin: 0 0 6pt 6pt; }}
.exam .d {{ font-size: 17pt; font-weight: 800; color: #1D4ED8; }}
ul {{ margin: 3pt 0; padding-right: 16pt; }} li {{ margin: 2pt 0; }}
.muted {{ color: #9CA3AF; }}
.bar {{ display: flex; height: 13pt; border-radius: 7pt; overflow: hidden; background: {GREY}; margin-top: 3pt; }} .bar i {{ display: block; height: 100%; }}
.mrow {{ display: flex; align-items: center; margin: 5pt 0; }} .ml {{ width: 70pt; font-weight: 600; }}
.track {{ position: relative; flex: 1; height: 14pt; }} .track i {{ position: absolute; right: 0; top: 0; height: 14pt; border-radius: 5pt; }}
.prev {{ position: absolute; top: -3pt; width: 0; height: 20pt; border-right: 2px dashed #111827; }}
.mv {{ width: 52pt; text-align: left; font-size: 9.5pt; color: #4B5563; direction: ltr; }}
"""
    body = [f'<h1>{_e(data["title"])}</h1><div class="range">{_e(data.get("range", ""))}</div>',
            f'<div class="verdict">{_e(data.get("verdict", ""))}</div>', _cards(data)]
    body.append("<h2>לימוד לפי קורס</h2>" + minutes_chart(data["courses"]) +
                '<div class="sub">הקו המקווקו = השבוע הקודם</div>')
    body.append('<h2>כיסוי חומר</h2><div class="legend"><span><i class="dot" style="background:%s"></i>נחזר</span>'
                '<span><i class="dot" style="background:%s"></i>נלמד, עוד לא נחזר</span>'
                '<span><i class="dot" style="background:#D1D5DB"></i>עוד לא נלמד</span></div>' % (GREEN, AMBER))
    for c in data["courses"]:
        extra = f' · {len(c["pending"])} ממתינים לחזרה' if c.get("pending") else ""
        body.append(f'<div class="course"><div class="name"><span>{_e(c["name"])}</span>'
                    f'<span class="meta">{c["learned"]}/{c["total"]} נלמדו · {c["reviewed"]} נחזרו{extra}</span></div>'
                    f'{coverage_bar(c["learned"], c["reviewed"], c["total"])}</div>')
    weak = [(c["name"], w) for c in data["courses"] for w in c["weak"]]
    body.append("<h2>נושאים חלשים ומה לחזק</h2>")
    if weak:
        for cn, w in weak:
            why = f'<div class="why">לא היה ברור: {_e(w["unclear"])}</div>' if w.get("unclear") else ""
            body.append(f'<div class="weak"><b>{_e(cn)}</b> · {_e(w["topic"])} · הבנה {w.get("rating", "?")}/5{why}</div>')
    else:
        body.append('<p class="muted">אין נושאים חלשים פתוחים. כל הכבוד.</p>')
    if data.get("gaps"):
        body.append("<p><b>פערים פתוחים:</b> " + _e(", ".join(data["gaps"])) + "</p>")
    body.append("<h2>השבוע הבא והמבחנים</h2>")
    for x in data.get("exams", [])[:6]:
        body.append(f'<div class="exam"><div class="d">{x["days"]}</div><div class="sub">ימים</div><div><b>{_e(x["name"])}</b> · {_e(x["date"])}</div></div>')
    if data.get("next_week"):
        body.append("<ul>" + "".join(f"<li>{_e(x)}</li>" for x in data["next_week"][:12]) + "</ul>")
    if data.get("tips"):
        body.append("<h2>הצעות</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in data["tips"][:6]) + "</ul>")
    return f'<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8"><style>{css}</style></head><body>{"".join(body)}</body></html>'


def build_pdf(data: dict) -> bytes:
    from weasyprint import HTML      # ייבוא מאוחר: בלי WeasyPrint הבוט ממשיך לעבוד בטקסט
    return HTML(string=build_html(data), base_url=os.path.dirname(__file__)).write_pdf()
