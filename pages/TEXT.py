import streamlit as st
import os 
import io
import re
import yaml 
import uuid

from pathlib import Path
from PIL import Image

from src.be.assess_text import assess_pages, draw_page
from src.be.make_pdf import build_pdf
from src.fe.support_functions import setup_sidebar, extract_page_frames
from src.fe.styles import HIDE_SIDEBAR_NAV, CATEGORY_COLORS

################
# --- SET UP ---
################

st.set_page_config(page_title="TEXT", layout="wide")
st.markdown(HIDE_SIDEBAR_NAV, unsafe_allow_html=True)

with open(f"{os.getcwd()}/src/fe/config.yaml", "r") as config_file:
    config = yaml.safe_load(config_file)

selected_page = setup_sidebar(
    pages=config['pages'],
    main_page=config['main_page'])

if selected_page == "HOME":
    st.switch_page("HOME.py")
elif selected_page == "VIDEO":
    st.switch_page("pages/VIDEO.py")
elif selected_page == "TEXT":
    pass
elif selected_page == "EXERCISE":
    st.switch_page("pages/EXERCISE.py")

# --- INPUT ---
st.title("Text Assessment")
st.caption("Upload the video of the students' pages, add the answer key and your notes, and get a draft assessment to review.")

def pack(f):
    """Turn an uploaded file into a plain dict, so nothing has to be saved on disk."""
    return {"name": f.name, "mime": f.type, "data": f.getvalue()}

col_pages, col_details = st.columns([3, 2], gap="large")

# --- 1. Student pages ---
with col_pages:
    st.subheader("1. Student pages (video)")
    pages_video = st.file_uploader(
        "Upload the video of the pages",
        type=["mp4", "mov", "m4v", "webm"],
        help="Film the pages one after another and hold each page still for 2-3 seconds.",
    )
    rotate = st.radio(
        "Rotate the pages", [0, 90, 180, 270], horizontal=True,
        help="Use this only if the pages come out sideways.",
    )

    if pages_video is not None and st.button("Extract pages from the video"):
        for k in [k for k in st.session_state if k.startswith("keep_")]:
            del st.session_state[k]
        with st.spinner("Looking for the pages..."):
            st.session_state["frames"] = extract_page_frames(
                pages_video.getvalue(),
                suffix=Path(pages_video.name).suffix or ".mp4",
                rotate=rotate,
            )
        st.session_state["frames_source"] = pages_video.name

    page_frames = []
    frames = st.session_state.get("frames", [])
    if pages_video is not None and st.session_state.get("frames_source") == pages_video.name:
        if not frames:
            st.warning("No stable pages found. Try a slower video, holding each page still.")
        else:
            st.caption(f"{len(frames)} pages found. Untick anything that is not a page, such as a blurry frame or a hand over the text.")
            grid = st.columns(3)
            for i, fr in enumerate(frames):
                with grid[i % 3]:
                    st.image(fr["jpeg"], caption=f"{fr['time']:.1f} s")
                    if st.checkbox("Use", value=True, key=f"keep_{i}"):
                        page_frames.append({
                            "name": f"page_{len(page_frames) + 1}.jpg",
                            "mime": "image/jpeg",
                            "data": fr["jpeg"],
                        })

# --- 2. Context ---
with col_details:
    st.subheader("2. Context")
    student_label = st.text_input(
        "Student name or ID",
        placeholder="e.g. Linh N.",
        help="Stays in your app and is not sent to the AI.",
    )
    task_description = st.text_area(
        "What was the student asked to do?",
        placeholder="e.g. Unit 5 test: reading questions, sentence rewriting and a short paragraph.",
        height=120,
    )
    skills_selected = st.multiselect("Skills assessed", ["Writing", "Reading", "Listening"])

    audio_file = None
    if "Listening" in skills_selected:
        audio_file = st.file_uploader(
            "Listening audio",
            type=["mp3", "wav", "m4a", "aac", "ogg", "flac"],
            help="The audio the students listened to. Needed only if you don't provide an answer key.",
        )
        if audio_file is not None:
            st.audio(audio_file)

# --- 3. Answer key ---
st.subheader("3. Answer key (optional)")
key_mode = st.radio(
    "How do you want to provide it?",
    ["No answer key", "Typed text", "Photos of the correct pages", "Video of the pages"],
    horizontal=True,
)

key_text, key_images, key_video = "", [], None
if key_mode == "Typed text":
    key_text = st.text_area(
        "Question number and answer",
        placeholder="1 - B\n2 - has been waiting\n3 - went\n4 - ...",
        height=160,
    )
elif key_mode == "Photos of the correct pages":
    key_images = st.file_uploader(
        "Photos of the pages completed correctly",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="key_images",
    )
    key_images = sorted(key_images, key=lambda f: f.name)
elif key_mode == "Video of the pages":
    key_video = st.file_uploader(
        "Video of the correct pages",
        type=["mp4", "mov", "m4v", "webm"],
        key="key_video",
    )

# --- 4. Teacher notes ---
st.subheader("4. Your notes")
teacher_note = st.text_area(
    "How should the AI assess the work, and what should the feedback take into account?",
    placeholder="e.g. Write the report in Vietnamese. Reading answers are worth 1 point each. Be encouraging, we worked on past tenses last week.",
    height=140,
    max_chars=1500,
    help="Used for the marking instructions, tone, focus and the language of the report. Findings still come from the pages.",
)

# --- Submit ---
has_key = bool(
    (key_mode == "Typed text" and key_text.strip())
    or (key_mode == "Photos of the correct pages" and key_images)
    or (key_mode == "Video of the pages" and key_video is not None)
)
needs_audio = "Listening" in skills_selected and not has_key
ready = bool(page_frames) and bool(skills_selected) and (audio_file is not None or not needs_audio)

if not page_frames:
    st.caption("Extract the pages from the video and keep at least one to continue.")
elif not skills_selected:
    st.caption("Select the skills assessed to continue.")
elif needs_audio and audio_file is None:
    st.caption("Upload the listening audio, or provide an answer key, to continue.")

if st.button("Generate assessment", type="primary", disabled=not ready):
    for k in ("report", "usage", "selected_finding", "page_images",
              "removed", "score_edit", "pdf_bytes", "pdf_warning"):
        st.session_state.pop(k, None)

    answer_key = {"mode": key_mode if has_key else "No answer key"}
    if has_key:
        if key_mode == "Typed text":
            answer_key["text"] = key_text.strip()
        elif key_mode == "Photos of the correct pages":
            answer_key["files"] = [pack(f) for f in key_images]
        elif key_mode == "Video of the pages":
            answer_key["files"] = [pack(key_video)]

    request = {
        "pages": page_frames,
        "skills": skills_selected,
        "student_label": student_label,
        "task_description": task_description,
        "teacher_note": teacher_note,
        "listening_audio": pack(audio_file) if audio_file is not None else None,
        "answer_key": answer_key,
    }
    st.session_state["text_request"] = request

    with st.spinner("Reading the pages, this can take a few minutes..."):
        try:
            report, pages, usage = assess_pages(st.secrets["GEMINI_API_KEY"], request)
            st.session_state["report"] = report
            st.session_state["page_images"] = pages
            st.session_state["removed"] = set()
            st.session_state["view_page"] = 1
            st.session_state["usage"] = {
                "input": usage.prompt_token_count or 0,
                "output": usage.candidates_token_count or 0,
                "total": usage.total_token_count or 0,
            }
        except Exception as e:
            st.error(f"Assessment failed: {e}")

# --- Report display ---
KIND_BADGE = {"error": "red", "wrong_answer": "orange", "matching": "violet", "good": "green"}

def invalidate_pdf():
    st.session_state.pop("pdf_bytes", None)

def select_finding(n, page):
    st.session_state["selected_finding"] = n
    st.session_state["view_page"] = page

def close_zoom():
    st.session_state["selected_finding"] = None

def remove_finding(n):
    st.session_state["removed"].add(n)
    if st.session_state.get("selected_finding") == n:
        st.session_state["selected_finding"] = None
    invalidate_pdf()

def restore_finding(n):
    st.session_state["removed"].discard(n)
    invalidate_pdf()

def crop_finding(jpeg, f, pad=0.04):
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    W, H = img.size
    ymin, xmin, ymax, xmax = f["box"]
    box = (max(xmin / 1000 - pad, 0) * W, max(ymin / 1000 - pad, 0) * H,
           min(xmax / 1000 + pad, 1) * W, min(ymax / 1000 + pad, 1) * H)
    return img.crop(tuple(int(v) for v in box))

if "report" in st.session_state:
    report = st.session_state["report"]
    pages = st.session_state["page_images"]
    findings = report["findings"]
    removed = st.session_state.get("removed", set())
    active = [f for f in findings if f["n"] not in removed]       # the comments the teacher keeps
    selected = st.session_state.get("selected_finding")

    def fill(text):
        return text.replace("[STUDENT]", student_label or "the student")

    st.divider()
    st.header("Draft report")
    score = st.text_input(
        "Score (editable, used in the PDF)", value=report["score"],
        key="score_edit", on_change=invalidate_pdf,
        help="The AI's score counts every comment it made. Adjust it if you remove comments.",
    )
    st.write(fill(report["summary"]))

    col_s, col_a = st.columns(2)
    with col_s:
        st.markdown("**Strengths**")
        for s in report["strengths"]:
            st.markdown(f"- {fill(s)}")
    with col_a:
        st.markdown("**Areas to improve**")
        for a in report["areas_to_improve"]:
            st.markdown(f"- {fill(a)}")

    if "usage" in st.session_state:
        u = st.session_state["usage"]
        st.caption(f"Tokens used: {u['input']:,} in · {u['output']:,} out · {u['total']:,} total"
                   f" · model: {report.get('model', '?')}")

    with st.expander("Transcription (as read by the AI, check it against the pages)"):
        for pt in report["transcriptions"]:
            st.markdown(f"**Page {pt['page']}**")
            text = re.sub(r"\[crossed out: (.*?)\]", r"~~\1~~", pt["text"])
            st.markdown(text.replace("\n", "  \n"))

    st.subheader("Pages and comments")
    view_page = st.radio(
        "Page", list(range(1, len(pages) + 1)), horizontal=True,
        key="view_page", format_func=lambda p: f"Page {p}",
    )

    col_img, col_comments = st.columns([3, 2], gap="large")

    with col_img:
        st.image(draw_page(pages[view_page - 1], active, view_page, selected),
                 caption=f"Page {view_page}")
        st.caption("Red: error · Orange: wrong answer · Purple: matching · Green: well done · "
                   "Yellow: unclear, please check")

    with col_comments:
        sel = next((f for f in active if f["n"] == selected), None)
        if sel:
            if sel["box"][2] > sel["box"][0] and sel["box"][3] > sel["box"][1]:
                st.image(crop_finding(pages[sel["page"] - 1], sel), caption=f"#{sel['n']} zoomed")
            st.button("✕ Close zoom", key="close_zoom", on_click=close_zoom)

        page_findings = [f for f in active if f["page"] == view_page]
        st.markdown(f"**Comments on page {view_page} ({len(page_findings)})**")
        if not page_findings:
            st.caption("Nothing flagged on this page.")
        for f in page_findings:
            low = f["confidence"] == "low"
            color = "gray" if low else KIND_BADGE.get(f["kind"], "gray")
            label = f" · {f['label']}" if f["label"] else ""
            with st.container(border=True):
                top, btn_show, btn_remove = st.columns([3, 1, 1])
                top.markdown(f":{color}-background[#{f['n']} {f['kind'].replace('_', ' ')}]{label}"
                             + (" ⚠ unclear" if low else ""))
                btn_show.button("Show", key=f"show_{f['n']}", on_click=select_finding,
                                args=(f["n"], f["page"]))
                btn_remove.button("Remove", key=f"remove_{f['n']}", on_click=remove_finding,
                                  args=(f["n"],))
                st.markdown(f"> {f['quote']}")
                st.markdown(f"**Correct:** {f['correction']}")
                st.caption(fill(f["explanation"]))

    removed_findings = [f for f in findings if f["n"] in removed]
    if removed_findings:
        with st.expander(f"Removed comments ({len(removed_findings)})"):
            for f in removed_findings:
                c1, c2 = st.columns([5, 1])
                c1.markdown(f"#{f['n']} · page {f['page']} · {f['quote']} → {f['correction']}")
                c2.button("Restore", key=f"restore_{f['n']}", on_click=restore_finding,
                          args=(f["n"],))

    # --- PDF report ---
    st.subheader("Report document")
    st.caption(f"The PDF contains all the pages and the {len(active)} comments you kept. "
               "Numbers in the PDF run 1, 2, 3... over those comments.")
    if st.button("Create PDF report"):
        with st.spinner("Building the PDF..."):
            try:
                pdf_bytes, pdf_warning = build_pdf(report, active, pages, student_label, score)
                st.session_state["pdf_bytes"] = pdf_bytes
                st.session_state["pdf_warning"] = pdf_warning
            except Exception as e:
                st.error(f"Could not build the PDF: {e}")

    if "pdf_bytes" in st.session_state:
        if st.session_state.get("pdf_warning"):
            st.warning(st.session_state["pdf_warning"])
        safe_name = re.sub(r"[^\w-]+", "_", student_label).strip("_") or "student"
        st.download_button(
            "⬇ Download PDF report",
            data=st.session_state["pdf_bytes"],
            file_name=f"report_{safe_name}.pdf",
            mime="application/pdf",
        )