import io
import time
from pathlib import Path

from PIL import Image, ImageOps, ImageDraw, ImageFont
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from src.be.model_router import generate_with_fallback

MAX_SIDE = 1024                          # longest side of a page image, in pixels [2000, 1536, 1024]

# only used if the browser reports an odd mime type; m4a may need converting to mp3
AUDIO_MIME = {".mp3": "audio/mp3", ".wav": "audio/wav", ".m4a": "audio/mp4",
              ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac"}

###############
# --- SCHEMA ---
###############

class PageText(BaseModel):
    page: int = Field(description="1-based index of the student page")
    text: str = Field(description="Faithful transcription of everything written on that page, in reading order. Show crossed-out text as [crossed out: ...] in the place where it was written. Do not correct anything. Write [illegible] for words you cannot read.")

class Finding(BaseModel):
    page: int = Field(description="1-based index of the student page")
    kind: str = Field(description="One of: error, wrong_answer, matching, good")
    label: str = Field(description="Question or section, e.g. 'Q3', 'Rewrite 2', 'Paragraph'; empty if unclear")
    quote: str = Field(description="Exactly what the student wrote in the final version, without crossed-out text, as read from the page")
    correction: str = Field(description="The corrected text, or the expected answer from the key")
    explanation: str = Field(description="One short sentence on why")
    confidence: str = Field(description="One of: high, medium, low")
    box: list[int] = Field(description="[ymin, xmin, ymax, xmax] on a 0-1000 scale relative to that page image, tightly around the quoted text")

class PagesReport(BaseModel):
    transcriptions: list[PageText]
    summary: str
    strengths: list[str]
    areas_to_improve: list[str]
    score: str = Field(description="e.g. '14/20' if the key or notes give points; empty otherwise")
    findings: list[Finding]

SYSTEM = """You mark a student's handwritten paper test. Input: student page images (STUDENT PAGE 1, 2, ...), optional answer key (typed, photos, or video of correct pages) and listening audio.
Transcribe first: each page faithfully in transcriptions, never correct, [illegible] for unreadable words.
Then findings in page order; each needs an exact quote of the student's final text and a box around it.
Kinds:
- error: language mistake in the student's own writing (grammar, vocabulary, word choice, spelling, punctuation, sentence structure), including rewrites and paragraphs.
- wrong_answer: answer differing from the key or the correct answer.
- matching: matching or line-drawing exercise; quote pairs as drawn (e.g. "1 -> C, 2 -> A"), give expected pairs in correction, box covers the whole exercise.
- good: notable strength; use sparingly.
Marking:
- With a key: compare each answer to it, pairing key pages by question content (never by page position). Without a key: own judgement and, for listening, the audio. Accept answers equivalent in meaning when reasonable and say so in the explanation.
- Never invent mistakes. Report every real mistake, one finding each; don't stop after a few.
Student edits:
- Cross-outs, scribbles, erasures, overwrites: the final answer is what is NOT crossed out. Judge only the final version; never flag or treat crossed-out text as the answer. A self-correction to the right answer is correct.
- Words above the line or marked with an arrow/caret belong where the mark points; a rewrite beside or below a crossed-out answer replaces it.
- If which version is final is unclear (partial strike, several attempts, heavy scribble, faint erasure), assess the version the student intended, set confidence "low", and note the ambiguity in the explanation.
Confidence:
- Blurry or angled pages: "low" when text is hard to read.
- Don't flag a spelling or word mistake unless sure the student wrote it. Unclear handwriting → "low"; "high" only when the text is clearly read and the finding is certain.
Box: [ymin, xmin, ymax, xmax], 0-1000 scale relative to the page image, tight around the quote.
Score: earned/possible if the key or notes give points; otherwise empty.
Privacy: never use or guess personal names; write [STUDENT].
Teacher's notes: carry marking instructions, context, tone and focus. Follow the marking instructions but never invent or hide findings.
Language: summary, strengths, areas_to_improve and explanation in the language the teacher requests in their notes (default English); transcriptions, quote and correction in the student's language; kind and confidence as the exact English values listed."""

#################
# --- HELPERS ---
#################

def prepare_page(data: bytes) -> bytes:
    """Fix phone rotation and shrink. The AI and the drawing must use this same image."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()

def _upload_bytes(client, data: bytes, mime: str, timeout=300):
    f = client.files.upload(file=io.BytesIO(data), config=types.UploadFileConfig(mime_type=mime))
    start = time.time()
    while f.state.name == "PROCESSING":
        if time.time() - start > timeout:
            raise TimeoutError("File processing took too long.")
        time.sleep(2)
        f = client.files.get(name=f.name)
    if f.state.name != "ACTIVE":
        raise RuntimeError(f"File could not be processed (state: {f.state.name}).")
    return f

def _image_part(jpeg: bytes):
    return types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")

def _build_contents(client, request, uploaded):
    """Builds the labelled parts sent to the model. Returns (parts, prepared page images)."""
    pages = [prepare_page(p["data"]) for p in request["pages"]]
    parts = []
    for i, jpg in enumerate(pages, 1):
        parts += [f"STUDENT PAGE {i}:", _image_part(jpg)]

    key = request["answer_key"]
    if key["mode"] == "Typed text":
        parts.append("ANSWER KEY (typed, question and answer):\n" + key["text"])
    elif key["mode"] == "Photos of the correct pages":
        for i, f in enumerate(key["files"], 1):
            parts += [f"ANSWER KEY PAGE {i} (completed correctly by the teacher):",
                      _image_part(prepare_page(f["data"]))]
    elif key["mode"] == "Video of the pages":
        f = key["files"][0]
        video = _upload_bytes(client, f["data"], f["mime"])
        uploaded.append(video)
        parts += ["ANSWER KEY VIDEO (the teacher's correctly completed pages):", video]

    audio = request.get("listening_audio")
    if audio:
        mime = AUDIO_MIME.get(Path(audio["name"]).suffix.lower(), audio["mime"])
        af = _upload_bytes(client, audio["data"], mime)
        uploaded.append(af)
        parts += ["LISTENING AUDIO (what the students listened to):", af]

    parts.append(
        f"Skills assessed: {', '.join(request['skills'])}\n\n"
        f"Task description:\n{request['task_description'] or '(not provided)'}\n\n"
        f"Teacher's notes and marking instructions:\n{request['teacher_note'] or '(none)'}\n\n"
        "Transcribe and assess the student pages above."
    )
    return parts, pages

def number_findings(findings: list[dict], n_pages: int) -> list[dict]:
    """Cleans the findings and numbers them here, not by the AI: by page, top to bottom, left to right."""
    cleaned = []
    for f in findings:
        if not 1 <= f["page"] <= n_pages:
            continue                                      # the model referred to a page that does not exist
        box = list(f["box"])[:4]
        if len(box) != 4:
            box = [0, 0, 0, 0]
        box = [max(0, min(1000, int(v))) for v in box]    # keep inside the 0-1000 scale
        ymin, ymax = sorted((box[0], box[2]))
        xmin, xmax = sorted((box[1], box[3]))
        f["box"] = [ymin, xmin, ymax, xmax]
        cleaned.append(f)

    cleaned.sort(key=lambda f: (f["page"], f["box"][0], f["box"][1]))
    for n, f in enumerate(cleaned, start=1):
        f["n"] = n
    return cleaned

def _config(system, use_thinking=True):
    return types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=PagesReport,
        temperature=0.2,
        max_output_tokens=32768,
        thinking_config=types.ThinkingConfig(thinking_level="low") if use_thinking else None,
    )

####################
# --- ASSESSMENT ---
####################

def assess_pages(api_key, request):
    """Returns (report dict with numbered findings, prepared page images as JPEG bytes, usage).
    report["model"] holds the name of the model that answered."""
    client = genai.Client(
        api_key=api_key.strip(),
        http_options=types.HttpOptions(timeout=300_000),     # 5 minutes, in milliseconds
    )
    uploaded = []
    try:
        contents, pages = _build_contents(client, request, uploaded)
        response, model = generate_with_fallback(client, contents, lambda t: _config(SYSTEM, t))

        report = response.parsed.model_dump()
        report["findings"] = number_findings(report["findings"], len(pages))
        report["model"] = model
        return report, pages, response.usage_metadata
    finally:
        for f in uploaded:                                    # remove uploaded copies from Google's storage
            try:
                client.files.delete(name=f.name)
            except Exception:
                pass

###################
# --- DRAWING -----
###################

KIND_COLORS = {"error": (230, 57, 70), "wrong_answer": (230, 126, 34),
               "matching": (142, 68, 173), "good": (39, 174, 96)}
LOW_CONFIDENCE = (241, 196, 15)

def _font(size):
    for name in ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "Arial Bold.ttf",
                 "Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)      # needs Pillow 10.1 or newer
    except TypeError:
        return ImageFont.load_default()

def draw_page(jpeg: bytes, findings: list[dict], page: int, selected=None) -> bytes:
    """Highlights and numbered markers on one page. `selected` emphasizes one finding number."""
    base = Image.open(io.BytesIO(jpeg)).convert("RGBA")
    W, H = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    r = max(13, W // 70)
    font = _font(int(r * 1.2))

    for f in findings:
        if f["page"] != page:
            continue
        ymin, xmin, ymax, xmax = f["box"]
        x0, y0, x1, y1 = xmin * W / 1000, ymin * H / 1000, xmax * W / 1000, ymax * H / 1000
        if x1 <= x0 or y1 <= y0:
            continue
        low = f["confidence"] == "low"
        color = LOW_CONFIDENCE if low else KIND_COLORS.get(f["kind"], (230, 57, 70))
        faded = selected is not None and f["n"] != selected
        fill_a, line_a, dot_a = (25, 80, 120) if faded else (70, 230, 255)
        d.rectangle([x0, y0, x1, y1], fill=color + (fill_a,), outline=color + (line_a,),
                    width=5 if f["n"] == selected else 2)

        cx, cy = min(max(x0, r), W - r), min(max(y0, r), H - r)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (dot_a,))
        label = str(f["n"])
        bb = d.textbbox((0, 0), label, font=font)          # centre the number by hand, works with any font
        tx = cx - (bb[2] - bb[0]) / 2 - bb[0]
        ty = cy - (bb[3] - bb[1]) / 2 - bb[1]
        d.text((tx, ty), label, fill=(0, 0, 0, 255) if low else (255, 255, 255, 255), font=font)

    out = Image.alpha_composite(base, overlay).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()