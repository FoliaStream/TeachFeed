import io
import re
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageOps
from google import genai
from google.genai import types
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from src.be.model_router import generate_with_fallback

# Simple Gemini tasks use the cheap models first, to save the small daily quota of the Flash models
LITE_FIRST = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"]

MAX_MATERIAL_CHARS = 12000


class ExerciseError(Exception):
    pass


################################
# --- 1. PHOTOS -> TEXT (Gemini) ---
################################

class Transcript(BaseModel):
    text: str = Field(description="All the text on the pages, in reading order")

TRANSCRIBE_SYSTEM = """You transcribe pages of lesson material for a teacher.
Rules:
- Copy all the text exactly, in reading order, page after page. Keep headings, numbering and lists.
- Do not correct, translate, summarise or add anything.
- If a picture is needed to understand the text, describe it in square brackets in one short phrase.
- Write [illegible] for words you cannot read."""

def _shrink(data: bytes, side=1600) -> bytes:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()

def transcribe_material(gemini_key, images):
    """images: list of {"data": bytes}. Returns (text, model that answered)."""
    client = genai.Client(api_key=gemini_key.strip(), http_options=types.HttpOptions(timeout=180_000))
    parts = []
    for i, img in enumerate(images, 1):
        parts += [f"PAGE {i}:", types.Part.from_bytes(data=_shrink(img["data"]), mime_type="image/jpeg")]
    parts.append("Transcribe the pages above.")

    def config(use_thinking):
        return types.GenerateContentConfig(
            system_instruction=TRANSCRIBE_SYSTEM,
            response_mime_type="application/json",
            response_schema=Transcript,
            temperature=0.0,
            max_output_tokens=16384,
            thinking_config=types.ThinkingConfig(thinking_level="low") if use_thinking else None,
        )

    parsed, _, model = generate_with_fallback(client, parts, config, Transcript, preferred=LITE_FIRST)
    return parsed.text, model


###################################
# --- 2. EXERCISES (Gemini) ---
###################################

class Item(BaseModel):
    question: str = Field(description="The question text. For multiple choice, include the options A) B) C) D) on separate lines.")
    answer: str = Field(description="The correct answer")

class Exercise(BaseModel):
    type: str = Field(description="Exercise type, e.g. Gap fill")
    instructions: str = Field(description="One short instruction for the students")
    options: str = Field(description="For matching exercises: the lettered list the numbered items are matched to. Empty otherwise.")
    items: list[Item]

class Sheet(BaseModel):
    title: str = Field(description="Short lesson title")
    exercises: list[Exercise]

EXERCISE_SYSTEM = """You are an experienced English teacher who writes classroom exercises.
Return the exercises in the given structure.
Rules:
- Write exactly the number of exercises the teacher requires, with exactly the required number of questions in each. An exercise is one numbered activity with its own short instruction and several questions. Never merge exercises and never stop early.
- The required counts in the message override anything else the teacher wrote about numbers.
- Use the exercise types listed. If more exercises are required than types, reuse the types with different content and slightly different difficulty. If no type is listed, vary the types.
- Never repeat a question or an answer across the exercises.
- Take the content from the lesson material and stay consistent with it.
- Use vocabulary and grammar suited to the student level.
- Every question has exactly one clearly correct answer. Check each answer before you write it.
- Multiple choice: put the options A), B), C), D) on separate lines inside the question text.
- Matching: each question is a numbered item, `options` holds the lettered list, and the answer is the matching letter.
- Write the instructions for the students in the language the teacher asks for in their notes; if none is requested, use English. Questions and answers stay in English."""

def sheet_to_text(title, exercises):
    """The sheet as plain text for the review box, in the layout the slide step expects."""
    lines = [f"TITLE: {title}", ""]
    for i, e in enumerate(exercises, 1):
        lines += [f"EXERCISE {i}: {e.type}", f"Instructions: {e.instructions}"]
        if e.options.strip():
            lines.append(e.options.strip())
        lines += [f"{j}. {it.question}" for j, it in enumerate(e.items, 1)]
        lines.append("")
    lines.append("ANSWER KEY")
    for i, e in enumerate(exercises, 1):
        lines.append(f"Exercise {i}: " + "  ".join(f"{j}. {it.answer}" for j, it in enumerate(e.items, 1)))
    return "\n".join(lines)

def generate_exercises(gemini_key, material, focus, details="", skills=None, level="B1",
                       types_wanted=None, notes="", n_exercises=6, per_exercise=5):
    """Returns (exercise text, usage dict).
    focus: short description of the lesson focus. details: what the exercises should be about.
    notes: extra instructions, including the language.
    The number of exercises is checked: if the answer has fewer, more are requested until it matches."""
    material = material.strip()
    cut = ""
    if len(material) > MAX_MATERIAL_CHARS:
        material = material[:MAX_MATERIAL_CHARS]
        cut = "\n(The material was cut because it was too long.)"

    base = (
        f"REQUIRED COUNTS: exactly {n_exercises} exercises, each with exactly {per_exercise} questions.\n"
        f"Lesson focus: {focus.strip() or '(see details)'}\n"
        f"Details from the teacher: {details.strip() or '(none)'}\n"
        f"Skills to practise: {', '.join(skills) if skills else '(not specified)'}\n"
        f"Student level: {level}\n"
        f"Exercise types: {', '.join(types_wanted) if types_wanted else '(choose suitable types)'}\n"
        f"Extra notes from the teacher: {notes.strip() or '(none)'}\n\n"
        f"LESSON MATERIAL:\n{material}{cut}"
    )

    client = genai.Client(api_key=gemini_key.strip(), http_options=types.HttpOptions(timeout=240_000))

    def config(use_thinking):
        return types.GenerateContentConfig(
            system_instruction=EXERCISE_SYSTEM,
            response_mime_type="application/json",
            response_schema=Sheet,
            temperature=0.7,
            max_output_tokens=32768,
            thinking_config=types.ThinkingConfig(thinking_level="low") if use_thinking else None,
        )

    usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def ask(user):
        # Exercise generation is the core content, so use the default Flash-first order.
        parsed, response, _ = generate_with_fallback(client, [user], config, Sheet)
        um = response.usage_metadata
        if um:
            usage["prompt_tokens"] += um.prompt_token_count or 0
            usage["completion_tokens"] += um.candidates_token_count or 0
        return parsed

    sheet = ask(base)
    exercises = list(sheet.exercises)

    for _ in range(3):                                        # top up if the answer has too few exercises
        if len(exercises) >= n_exercises:
            break
        missing = n_exercises - len(exercises)
        have = "\n".join(
            f"{i}. {e.type}: {e.items[0].question[:70] if e.items else ''}" for i, e in enumerate(exercises, 1))
        more = ask(base + f"\n\nALREADY WRITTEN ({len(exercises)} exercises):\n{have}\n\n"
                          f"Write exactly {missing} MORE exercises, different from the ones above. "
                          f"Return only the new ones.")
        exercises += list(more.exercises)

    exercises = exercises[:n_exercises]
    if not exercises:
        raise ExerciseError("Gemini returned no exercises. Try again.")
    return sheet_to_text(sheet.title, exercises), usage


###################################
# --- 3. SLIDE CONTENT (Gemini) ---
###################################

class Slide(BaseModel):
    title: str
    kind: str = Field(description="One of: exercise, answers, info")
    bullets: list[str]
    notes: str = Field(description="A short note for the teacher; empty if none")

class Deck(BaseModel):
    title: str
    subtitle: str
    slides: list[Slide]

DECK_SYSTEM = """You turn a set of classroom exercises into a presentation that a teacher shows in class.
Rules:
- Keep every question and every answer exactly as written. Shorten wording only if it will not fit a slide, and never change the meaning.
- Do not add exercises, questions or answers of your own.
- The title slide is made separately: do not include it in slides. Give the deck a title and a short subtitle (lesson focus and level).
- Each exercise starts on its own slide with kind "exercise". Slide title: "Exercise N: type". The first bullet is the instruction, the following bullets are the questions, numbered as in the source.
- At most 7 bullets per slide and about 110 characters per bullet. If an exercise has more, continue on another slide titled "Exercise N: type (cont.)".
- For multiple choice put the options in the same bullet as the question, separated by " / ".
- After all the exercises, add the answers with kind "answers". Title "Answers: Exercise N", one bullet per answer. When there are many short exercises, put the answers of several exercises on one slide (title "Answers: Exercises 1-3"), at most 7 bullets per slide.
- notes: one short teaching tip or instruction for the teacher for each exercise slide. Leave it empty for answer slides.
- Write slide text in the same language as the exercises and their instructions."""

def create_deck(gemini_key, exercises_text):
    """Returns (deck dict, usage, model that answered)."""
    client = genai.Client(api_key=gemini_key.strip(), http_options=types.HttpOptions(timeout=180_000))
    prompt = f"EXERCISES:\n{exercises_text}"

    def config(use_thinking):
        return types.GenerateContentConfig(
            system_instruction=DECK_SYSTEM,
            response_mime_type="application/json",
            response_schema=Deck,
            temperature=0.3,
            max_output_tokens=32768,
            thinking_config=types.ThinkingConfig(thinking_level="low") if use_thinking else None,
        )

    parsed, response, model = generate_with_fallback(client, [prompt], config, Deck, preferred=LITE_FIRST)
    deck = parsed.model_dump()
    deck["slides"] = [s for s in deck["slides"] if s["bullets"] or s["title"].strip()]
    if not deck["slides"]:
        raise ExerciseError("The presentation came back empty. Try again.")
    return deck, response.usage_metadata, model


###################################
# --- 4. POWERPOINT FILE ---
###################################

BLUE = RGBColor(0x2B, 0x6C, 0xA3)
TEAL = RGBColor(0x3A, 0x9A, 0x9F)
INK = RGBColor(0x1F, 0x3B, 0x57)
PAPER = RGBColor(0xF7, 0xF9, 0xF4)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

def _rect(slide, x, y, w, h, color):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, x, y, w, h)
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape

def _text(slide, x, y, w, h, text, size, color, bold=False, anchor=MSO_ANCHOR.TOP, align=PP_ALIGN.LEFT):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    return box

def _body_size(bullets):
    chars = sum(len(b) for b in bullets)
    if chars < 350 and len(bullets) <= 5:
        return 28
    if chars < 550:
        return 24
    if chars < 800:
        return 20
    return 17

def build_pptx(deck: dict) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    W, H = prs.slide_width, prs.slide_height

    # title slide
    s = prs.slides.add_slide(blank)
    _rect(s, 0, 0, W, H, PAPER)
    _rect(s, 0, 0, Inches(0.5), H, BLUE)
    _rect(s, Inches(0.5), 0, Inches(0.15), H, TEAL)
    _text(s, Inches(1.3), Inches(2.3), Inches(11), Inches(1.8), deck["title"], 48, BLUE,
          bold=True, anchor=MSO_ANCHOR.BOTTOM)
    _text(s, Inches(1.3), Inches(4.3), Inches(11), Inches(1.2), deck["subtitle"], 26, TEAL)

    # content slides
    for item in deck["slides"]:
        s = prs.slides.add_slide(blank)
        is_answers = item["kind"] == "answers"
        _rect(s, 0, 0, W, H, PAPER)
        _rect(s, 0, 0, W, Inches(1.2), TEAL if is_answers else BLUE)
        _text(s, Inches(0.7), 0, Inches(12), Inches(1.2), item["title"], 34, WHITE,
              bold=True, anchor=MSO_ANCHOR.MIDDLE)

        bullets = item["bullets"]
        size = _body_size(bullets)
        box = s.shapes.add_textbox(Inches(0.8), Inches(1.6), Inches(11.7), Inches(5.4))
        tf = box.text_frame
        tf.word_wrap = True
        for i, b in enumerate(bullets):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            numbered = re.match(r"^\s*(\d+[\.\)]|[A-Da-d][\.\)])\s", b)
            run = p.add_run()
            run.text = b if numbered else f"•  {b}"
            run.font.size = Pt(size)
            run.font.color.rgb = INK
            p.space_after = Pt(size * 0.6)

        if item.get("notes"):
            s.notes_slide.notes_text_frame.text = item["notes"]

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


###################################
# --- 5. PDF SLIDES ---
###################################

# The font must support accents (Vietnamese). The first pair that exists is used.
FONT_CANDIDATES = [
    ("assets/fonts/DejaVuSans.ttf", "assets/fonts/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]
_FONT_CACHE = {}

def _pdf_fonts():
    """Returns (body font, bold font, warning or None)."""
    if "result" in _FONT_CACHE:
        return _FONT_CACHE["result"]
    for regular, bold in FONT_CANDIDATES:
        r, b = Path(regular), Path(bold)
        if not r.is_absolute():
            r, b = Path.cwd() / r, Path.cwd() / b
        if r.exists() and b.exists():
            pdfmetrics.registerFont(TTFont("SlideBody", str(r)))
            pdfmetrics.registerFont(TTFont("SlideBody-Bold", str(b)))
            _FONT_CACHE["result"] = ("SlideBody", "SlideBody-Bold", None)
            return _FONT_CACHE["result"]
    warning = ("No font with accent support was found, so accented letters (for example Vietnamese) "
               "may be missing in the PDF. Put DejaVuSans.ttf and DejaVuSans-Bold.ttf in assets/fonts/.")
    _FONT_CACHE["result"] = ("Helvetica", "Helvetica-Bold", warning)
    return _FONT_CACHE["result"]

def _esc(text):
    return escape(text or "").replace("\n", "<br/>")

def build_pdf_slides(deck: dict):
    """The same slides as the PowerPoint, as a PDF. Returns (pdf bytes, font warning or None)."""
    body, bold, warning = _pdf_fonts()
    W, H = 960, 540                                   # 16:9, the same size as the PowerPoint slides (in points)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    c.setTitle(deck["title"])

    def rect(x, y, w, h, hex_color):
        c.setFillColor(colors.HexColor(hex_color))
        c.rect(x, y, w, h, stroke=0, fill=1)

    # title slide
    rect(0, 0, W, H, "#F7F9F4")
    rect(0, 0, 36, H, "#2B6CA3")
    rect(36, 0, 11, H, "#3A9A9F")
    title = Paragraph(_esc(deck["title"]), ParagraphStyle(
        "title", fontName=bold, fontSize=46, leading=54, textColor=colors.HexColor("#2B6CA3")))
    sub = Paragraph(_esc(deck["subtitle"]), ParagraphStyle(
        "sub", fontName=body, fontSize=24, leading=30, textColor=colors.HexColor("#3A9A9F")))
    _, th = title.wrap(780, 400)
    _, sh = sub.wrap(780, 200)
    title.drawOn(c, 96, 280)
    sub.drawOn(c, 96, 280 - 24 - sh)
    c.showPage()

    # content slides
    header_h = 86
    for item in deck["slides"]:
        answers = item["kind"] == "answers"
        rect(0, 0, W, H, "#F7F9F4")
        rect(0, H - header_h, W, header_h, "#3A9A9F" if answers else "#2B6CA3")
        heading = Paragraph(_esc(item["title"]), ParagraphStyle(
            "heading", fontName=bold, fontSize=30, leading=36, textColor=colors.white))
        _, hh = heading.wrap(W - 100, header_h)
        heading.drawOn(c, 50, H - header_h + (header_h - hh) / 2)

        bullets = item["bullets"]
        size = _body_size(bullets)
        x, width = 54, W - 108
        top, bottom = H - header_h - 34, 36
        while True:                                    # shrink the text until it fits the slide
            paras = []
            for b in bullets:
                numbered = re.match(r"^\s*(\d+[\.\)]|[A-Da-d][\.\)])\s", b)
                paras.append(Paragraph(_esc(b if numbered else f"•  {b}"), ParagraphStyle(
                    "body", fontName=body, fontSize=size, leading=size * 1.25,
                    textColor=colors.HexColor("#1F3B57"))))
            gap = size * 0.6
            total = sum(p.wrap(width, 10000)[1] for p in paras) + gap * max(len(paras) - 1, 0)
            if total <= top - bottom or size <= 12:
                break
            size -= 1

        y = top
        for p in paras:
            _, h = p.wrap(width, 10000)
            p.drawOn(c, x, y - h)
            y -= h + gap
        c.showPage()

    c.save()
    return buf.getvalue(), warning