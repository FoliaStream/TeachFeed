import streamlit as st 
import os 
import re
import yaml 

from src.be.exercises import (generate_exercises, transcribe_material, create_deck,
                              build_pptx, build_pdf_slides)
from src.fe.support_functions import setup_sidebar
from src.fe.styles import HIDE_SIDEBAR_NAV, CATEGORY_COLORS

################
# --- SET UP ---
################

st.set_page_config(page_title="EXERCISE", layout="wide")
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
    st.switch_page("pages/TEXT.py")
elif selected_page == "EXERCISE":
    pass

# Everything this page keeps in st.session_state starts with "exercise_",
# so it can never clash with the other pages.
EXERCISE_KEYS = ("exercise_text", "exercise_material", "exercise_usage", "exercise_deck",
                 "exercise_pptx", "exercise_pdf", "exercise_pdf_warning", "exercise_deck_model")

EXERCISE_TYPES = ["Gap fill", "Multiple choice", "Matching", "Error correction",
                  "Sentence transformation", "Short answer", "True / False"]

# --- INPUT ---
st.title("Exercises preparation")
st.caption("Upload pictures of the target lesson, add your notes, and get a draft presentation.")

col_upload, col_details = st.columns([3, 2], gap="large")

# --- 1. Lesson material ---
with col_upload:
    st.subheader("1. Lesson material")
    source = st.radio("Material as", ["Photos", "Pasted text"], horizontal=True)

    material_text, photos = "", []
    if source == "Photos":
        photos = st.file_uploader(
            "Pictures of the target lesson",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            help="The text is read from the pictures first. You can check it below the exercises.",
        )
        photos = sorted(photos, key=lambda f: f.name)
        if photos:
            thumbs = st.columns(3)
            for i, f in enumerate(photos):
                thumbs[i % 3].image(f, caption=f"Page {i + 1}")
    else:
        material_text = st.text_area(
            "Paste the text, word list or notes the exercises should be based on",
            height=260,
            placeholder="e.g. a reading passage, a vocabulary list with meanings, grammar notes...",
        )

# --- 2. Context ---
with col_details:
    st.subheader("2. Context")
    exercise_label = st.text_input(
        "Description",
        placeholder="Exercise focus, e.g. past simple",
        help="The lesson focus. It is sent to the AI and used as the name of the presentation file.",
    )
    task_description = st.text_area(
        "What is the exercise about?",
        placeholder="e.g. Number of exercises or focus grammar.",
        height=120,
    )
    skills_selected = st.multiselect("Focus skills", ["Writing", "Reading", "Speaking"])
    level = st.selectbox("Student level", ["A1", "A2", "B1", "B2", "C1", "C2"], index=2)
    types_wanted = st.multiselect(
        "Exercise types (optional)", EXERCISE_TYPES,
        help="Leave empty to let the AI choose suitable types.",
    )

# --- 3. Teacher notes ---
st.subheader("3. Your notes")
teacher_note = st.text_area(
    "Extra instructions (optional)",
    placeholder="e.g. Write the instructions in Vietnamese. Use sentences about school life.",
    height=100,
    max_chars=800,
    help="Used for the language of the instructions, tone and anything else you want.",
)

# --- Submit ---
has_material = bool(material_text.strip()) if source == "Pasted text" else bool(photos)
focus_ok = bool(exercise_label.strip() or task_description.strip())
ready = has_material and focus_ok

if not has_material:
    st.caption("Add the lesson material to continue.")
elif not focus_ok:
    st.caption("Describe the focus of the exercises to continue.")

if st.button("Generate exercises", type="primary", disabled=not ready):
    for k in EXERCISE_KEYS:
        st.session_state.pop(k, None)

    try:
        with st.spinner("Preparing the exercises..."):
            if source == "Photos":
                material, read_by = transcribe_material(
                    st.secrets["GEMINI_API_KEY"],
                    [{"data": f.getvalue()} for f in photos],
                )
            else:
                material = material_text
            text, usage = generate_exercises(
                st.secrets["GEMINI_API_KEY"], material,
                exercise_label, task_description, skills_selected, level, types_wanted, teacher_note,
            )
        st.session_state["exercise_material"] = material if source == "Photos" else ""
        st.session_state["exercise_text"] = text          # the review box below is bound to this key
        st.session_state["exercise_usage"] = usage
    except Exception as e:
        st.error(f"Could not generate the exercises: {e}")

# --- 4. Review ---
if "exercise_text" in st.session_state:
    st.divider()
    st.subheader("4. Review the exercises")
    st.caption("Edit anything you like. The presentation is made from exactly this text, "
               "including the answer key at the end.")

    st.text_area("Exercises", key="exercise_text", height=520, label_visibility="collapsed")

    usage = st.session_state.get("exercise_usage") or {}
    if usage:
        st.caption(f"Gemini tokens: {usage.get('prompt_tokens', 0):,} in · "
                   f"{usage.get('completion_tokens', 0):,} out")

    if st.session_state.get("exercise_material"):
        with st.expander("Text read from the pictures (check it against the pages)"):
            st.text(st.session_state["exercise_material"])

    # --- 5. Presentation (Gemini writes the slides, this app builds the files) ---
    st.subheader("5. Presentation")
    if st.button("Create presentation"):
        for k in ("exercise_deck", "exercise_pptx", "exercise_pdf", "exercise_pdf_warning",
                  "exercise_deck_model"):
            st.session_state.pop(k, None)
        try:
            with st.spinner("Designing the slides..."):
                deck, _, model = create_deck(
                    st.secrets["GEMINI_API_KEY"], st.session_state["exercise_text"])
                st.session_state["exercise_deck"] = deck
                st.session_state["exercise_deck_model"] = model
                st.session_state["exercise_pptx"] = build_pptx(deck)
                pdf_bytes, pdf_warning = build_pdf_slides(deck)
                st.session_state["exercise_pdf"] = pdf_bytes
                st.session_state["exercise_pdf_warning"] = pdf_warning
        except Exception as e:
            st.error(f"Could not create the presentation: {e}")

    if "exercise_pptx" in st.session_state:
        deck = st.session_state["exercise_deck"]
        st.success(f"{len(deck['slides']) + 1} slides ready · text written by "
                   f"{st.session_state.get('exercise_deck_model', '?')}")

        name_source = exercise_label.strip() or deck["title"]
        safe_name = re.sub(r"[^\w-]+", "_", name_source).strip("_") or "exercises"

        col_pptx, col_pdf = st.columns(2)
        col_pptx.download_button(
            "⬇ Download PowerPoint (.pptx)", data=st.session_state["exercise_pptx"],
            file_name=f"{safe_name}.pptx",
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation")
        col_pdf.download_button(
            "⬇ Download PDF", data=st.session_state["exercise_pdf"],
            file_name=f"{safe_name}.pdf", mime="application/pdf")

        if st.session_state.get("exercise_pdf_warning"):
            st.warning(st.session_state["exercise_pdf_warning"])

        with st.expander("Slide outline"):
            st.markdown(f"**{deck['title']}** — {deck['subtitle']}")
            for s in deck["slides"]:
                st.markdown(f"**{s['title']}**")
                for b in s["bullets"]:
                    st.markdown(f"- {b}")