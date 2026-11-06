import io
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from src.be.assess_text import KIND_COLORS, draw_page

##############
# --- FONTS ---
##############

# The font must support accents (Vietnamese). The first pair that exists is used.
# Relative paths are looked up from the folder you run Streamlit from.
FONT_CANDIDATES = [
    ("assets/fonts/DejaVuSans.ttf", "assets/fonts/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]

_FONTS = {}

def _register_fonts():
    """Returns (body font name, bold font name, warning or None)."""
    if "result" in _FONTS:
        return _FONTS["result"]

    for regular, bold in FONT_CANDIDATES:
        r, b = Path(regular), Path(bold)
        if not r.is_absolute():
            r, b = Path.cwd() / r, Path.cwd() / b
        if r.exists() and b.exists():
            pdfmetrics.registerFont(TTFont("PdfBody", str(r)))
            pdfmetrics.registerFont(TTFont("PdfBody-Bold", str(b)))
            pdfmetrics.registerFontFamily("PdfBody", normal="PdfBody", bold="PdfBody-Bold",
                                          italic="PdfBody", boldItalic="PdfBody-Bold")
            _FONTS["result"] = ("PdfBody", "PdfBody-Bold", None)
            return _FONTS["result"]

    warning = ("No font with accent support was found, so accented letters (for example Vietnamese) "
               "may be missing in the PDF. Put DejaVuSans.ttf and DejaVuSans-Bold.ttf in assets/fonts/.")
    _FONTS["result"] = ("Helvetica", "Helvetica-Bold", warning)
    return _FONTS["result"]

#################
# --- HELPERS ---
#################

def _esc(text):
    return escape(text or "").replace("\n", "<br/>")

def _styles(body, bold):
    return {
        "h1": ParagraphStyle("h1", fontName=bold, fontSize=20, leading=25),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=13, leading=17),
        "body": ParagraphStyle("body", fontName=body, fontSize=10.5, leading=14),
        "small": ParagraphStyle("small", fontName=body, fontSize=9, leading=12,
                                textColor=colors.HexColor("#666666")),
        "bullet": ParagraphStyle("bullet", fontName=body, fontSize=10.5, leading=14,
                                 leftIndent=14, bulletIndent=2),
    }

def _flow(c, items, x, width, top, bottom):
    """Draws (paragraph, gap_before) items from the top down. Returns the items that did not fit.
    The first item is always drawn, so a very long paragraph can never cause an endless loop."""
    y, first = top, True
    for i, (p, gap) in enumerate(items):
        _, h = p.wrap(width, 100000)
        if not first and y - gap - h < bottom:
            return items[i:]
        if not first:
            y -= gap
        p.drawOn(c, x, y - h)
        y -= h
        first = False
    return []

def _note_paragraph(f, body, size, arrow, fill):
    low = f["confidence"] == "low"
    color = "#b38600" if low else "#%02x%02x%02x" % KIND_COLORS.get(f["kind"], (230, 57, 70))
    style = ParagraphStyle("note", fontName=body, fontSize=size, leading=size * 1.25)
    label = f" {_esc(f['label'])}" if f["label"] else ""
    head = f'<font color="{color}"><b>#{f["n"]}</b></font>{label}'
    if low:
        head += " (unclear)"
    if f["kind"] == "good":
        main = f'"{_esc(f["quote"])}"'
    else:
        main = f'{_esc(f["quote"])} {arrow} <b>{_esc(f["correction"])}</b>'
    expl = f'<font size="{size - 1}" color="#555555">{_esc(fill(f["explanation"]))}</font>'
    return Paragraph(f"{head}<br/>{main}<br/>{expl}", style)

def _place_notes(paras, width, wants, top_limit, bottom_limit, gap=5):
    """Each note starts level with its error, and is pushed down if the one above is in the way."""
    placed, cursor = [], top_limit
    for p, want in zip(paras, wants):
        _, h = p.wrap(width, 100000)
        top = min(want, cursor)
        if top - h < bottom_limit:
            break
        placed.append((p, top, h))
        cursor = top - h - gap
    return placed

################
# --- PAGES ---
################

def _summary_pages(c, st, report, student_label, score, fill):
    title = "Text assessment" + (f" - {student_label}" if student_label else "")
    items = [(Paragraph(_esc(title), st["h1"]), 0),
             (Paragraph(date.today().strftime("%d/%m/%Y"), st["small"]), 3)]
    if score:
        items.append((Paragraph(f"<b>Score:</b> {_esc(score)}", st["body"]), 10))
    items += [(Paragraph("Summary", st["h2"]), 16),
              (Paragraph(_esc(fill(report["summary"])), st["body"]), 5)]
    items.append((Paragraph("Strengths", st["h2"]), 16))
    for s in report["strengths"]:
        items.append((Paragraph(_esc(fill(s)), st["bullet"], bulletText="•"), 4))
    items.append((Paragraph("Areas to improve", st["h2"]), 16))
    for a in report["areas_to_improve"]:
        items.append((Paragraph(_esc(fill(a)), st["bullet"], bulletText="•"), 4))

    W, H = A4
    m = 48
    while items:
        c.setPageSize(A4)
        items = _flow(c, items, m, W - 2 * m, H - m, m)
        c.showPage()

def _annotated_page(c, body, jpeg, findings, page, arrow, fill):
    """One page image with highlights and numbers, and the corrections in a margin on the right.
    Returns the findings that did not fit in the margin."""
    img = Image.open(io.BytesIO(jpeg))
    iw, ih = img.size
    pad, margin_w = 24, 290
    scale = min(640 / iw, 860 / ih)
    img_w, img_h = iw * scale, ih * scale
    page_w, page_h = img_w + margin_w + 2 * pad, img_h + 2 * pad
    c.setPageSize((page_w, page_h))

    annotated = Image.open(io.BytesIO(draw_page(jpeg, findings, page))).convert("RGB")
    buf = io.BytesIO()
    annotated.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    c.drawImage(ImageReader(buf), pad, pad, img_w, img_h)

    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.line(pad + img_w + 8, pad, pad + img_w + 8, page_h - pad)

    note_x, note_w = pad + img_w + 18, margin_w - 18
    top_limit, bottom_limit = page_h - pad, pad
    wants = [top_limit - (f["box"][0] / 1000) * img_h for f in findings]

    placed = []
    for size in (8.5, 7.5, 6.5):                     # shrink the notes until they all fit
        paras = [_note_paragraph(f, body, size, arrow, fill) for f in findings]
        placed = _place_notes(paras, note_w, wants, top_limit, bottom_limit)
        if len(placed) == len(paras):
            break
    for p, top, h in placed:
        p.drawOn(c, note_x, top - h)

    if not findings:
        c.setFont(body, 9)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawString(note_x, top_limit - 12, "No comments on this page.")

    c.showPage()
    return findings[len(placed):]

def _overflow_pages(c, st, body, page, findings, arrow, fill):
    items = [(Paragraph(f"Page {page}: comments continued", st["h2"]), 0)]
    items += [(_note_paragraph(f, body, 9, arrow, fill), 8) for f in findings]
    W, H = A4
    m = 48
    while items:
        c.setPageSize(A4)
        items = _flow(c, items, m, W - 2 * m, H - m, m)
        c.showPage()

#################
# --- BUILDER ---
#################

def build_pdf(report, findings, pages, student_label="", score=""):
    """report: the report dict; findings: ONLY the accepted findings; pages: prepared JPEG bytes.
    Returns (pdf bytes, font warning or None). Numbers on the PDF run 1..N over the findings kept."""
    body, bold, warning = _register_fonts()
    arrow = "->" if warning else "→"
    st = _styles(body, bold)

    def fill(text):
        return text.replace("[STUDENT]", student_label or "the student")

    ordered = sorted(findings, key=lambda f: (f["page"], f["box"][0], f["box"][1]))
    numbered = [dict(f, n=i) for i, f in enumerate(ordered, start=1)]

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Text assessment")

    _summary_pages(c, st, report, student_label, score, fill)
    for p, jpeg in enumerate(pages, start=1):
        page_findings = [f for f in numbered if f["page"] == p]
        leftover = _annotated_page(c, body, jpeg, page_findings, p, arrow, fill)
        if leftover:
            _overflow_pages(c, st, body, p, leftover, arrow, fill)

    c.save()
    return buf.getvalue(), warning