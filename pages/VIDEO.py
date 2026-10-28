import streamlit as st
import os 
import yaml
import uuid
import re

from pathlib import Path
from src.be.assess_video import assess_video, mmss_to_seconds, build_text_report

from src.fe.support_functions import setup_sidebar
from src.fe.styles import HIDE_SIDEBAR_NAV, CATEGORY_COLORS

################
# --- SET UP ---
################

st.set_page_config(page_title="VIDEO", layout="wide")
st.markdown(HIDE_SIDEBAR_NAV, unsafe_allow_html=True)

with open(f"{os.getcwd()}/src/fe/config.yaml", "r") as config_file:
    config = yaml.safe_load(config_file)

selected_page = setup_sidebar(
    pages=config['pages'],
    main_page=config['main_page'])

if selected_page == "HOME":
    st.switch_page("HOME.py")
elif selected_page == "VIDEO":
    pass

# --- VIDEO INPUT ---
st.title("Video assessment")
st.caption("Upload a student video, add your notes, and get a draft assessment to review.")

col_video, col_details = st.columns([3, 2], gap="large")

# --- 1. Video ---
with col_video:
    st.subheader("1. Student video")
    video_file = st.file_uploader(
        "Upload the video",
        type=["mp4", "mov", "m4v", "webm"],
        help="The default upload limit is 200 MB.",
    )
    if video_file is not None:
        st.video(video_file)
        st.caption(f"{video_file.name} · {video_file.size / 1_000_000:.1f} MB")

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
        placeholder="e.g. Present your weekend plans in 2 minutes.",
        height=120,
    )

# --- 3. Teacher notes ---
st.subheader("3. Your notes")
teacher_note = st.text_area(
    "What should the feedback take into account?",
    placeholder="e.g. Shy student, be encouraging. We worked on past tenses last week, so check those.",
    height=140,
    max_chars=1500,
    help="Used to adjust tone and focus. Scores and quoted mistakes still come from the video.",
)

# --- Submit ---
ready = video_file is not None
if not ready:
    st.caption("Upload a video to continue.")


if st.button("Generate assessment", type="primary", disabled=not ready):
    for k in ("report", "usage", "play_from"):          # ← add these two lines
        st.session_state.pop(k, None)
        
    upload_dir = Path(os.getcwd()) / "input" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # delete the previous videos
    for old_file in upload_dir.iterdir():
        if old_file.is_file():
            old_file.unlink()

    saved_path = upload_dir / f"{uuid.uuid4().hex}{Path(video_file.name).suffix.lower()}"
    saved_path.write_bytes(video_file.getbuffer())        

    st.session_state["video_request"] = {
        "video_path": str(saved_path),
        "original_name": video_file.name,
        "student_label": student_label,
        "task_description": task_description,
        "teacher_note": teacher_note,
    }

    with st.spinner("Analysing the video, this can take a minute..."):
        try:
            report, usage = assess_video(
                api_key=st.secrets["GEMINI_API_KEY"],
                video_path=str(saved_path),
                task_description=task_description,
                teacher_note=teacher_note,
                )
            st.session_state["report"] = report.model_dump()
        except Exception as e:
            st.error(f"Assessment failed: {e}")

if "report" in st.session_state:
    report = st.session_state["report"]
    request = {**st.session_state["video_request"], "student_label": student_label}
    
    st.divider()
    st.header("Draft report")
    col_player, col_report = st.columns([2, 3], gap="large")

    with col_player:
        st.video(request["video_path"], start_time=st.session_state.get("play_from", 0))
        if "usage" in st.session_state:
            u = st.session_state["usage"]
            st.caption(f"Tokens used: {u['input']:,} in · {u['output']:,} out · {u['total']:,} total")

    with col_report:
        st.subheader("Summary")
        st.write(report["summary"])

        col_s, col_a = st.columns(2)
        with col_s:
            st.markdown("**Strengths**")
            for s in report["strengths"]:
                st.markdown(f"- {s}")
        with col_a:
            st.markdown("**Areas to improve**")
            for a in report["areas_to_improve"]:
                st.markdown(f"- {a}")

        st.subheader(f"Comments ({len(report['comments'])})")
        for i, c in enumerate(report["comments"]):
            color = CATEGORY_COLORS.get(c["category"].lower(), "gray")
            with st.container(border=True):
                top, btn = st.columns([4, 1])
                top.markdown(f":{color}-background[{c['category']}] **{c['timestamp']}**")
                if btn.button("▶ Play", key=f"play_{i}"):
                    st.session_state["play_from"] = max(mmss_to_seconds(c["timestamp"]) - 1, 0)
                    st.rerun()
                st.markdown(f"> {c['quote']}")
                st.markdown(f"**Issue:** {c['issue']}")
                st.markdown(f"**Correction:** {c['correction']}")
                st.caption(c["explanation"])
        
        safe_name = re.sub(r"[^\w-]+", "_", request["student_label"]).strip("_") or "student"
        st.download_button(
            "⬇ Download text report",
            data=build_text_report(report, request),
            file_name=f"report_{safe_name}.txt",
            mime="text/plain",
        )