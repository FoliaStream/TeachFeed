# import streamlit as st
# import os 

# from streamlit_option_menu import option_menu
# from src.fe.styles import SIDEBAR_STYLES


# SIDEBAR
# def setup_sidebar(pages,
#                   main_page):
#     with st.sidebar:
#         col1, col2, col3 = st.columns([0.2, 2.4, 0.2])
#         with col2:
#             st.image(f"{os.getcwd()}/input/images/teachfeed_sb.jpeg", use_container_width=True)

#         # Initialize session state for page if it doesn't exist
#         if 'selected_page' not in st.session_state:
#             st.session_state.selected_page = main_page
        
#         choose = option_menu("", 
#                             pages,
#                             default_index=pages.index(st.session_state.selected_page),
#                             styles=SIDEBAR_STYLES)
        
#         # Refresh
#         if choose != st.session_state.selected_page:
#             st.session_state.selected_page = choose
#             st.rerun()  
    
#     return st.session_state.selected_page


import streamlit as st
import numpy as np
import os
import tempfile
import cv2


def setup_sidebar(pages, main_page):
    with st.sidebar:
        col1, col2, col3 = st.columns([0.2, 2.4, 0.2])
        with col2:
            st.image(f"{os.getcwd()}/input/images/teachfeed_sb.jpeg", use_container_width=True)

        if "selected_page" not in st.session_state:
            st.session_state.selected_page = main_page

        for page in pages:
            is_active = page == st.session_state.selected_page
            if st.button(
                page,
                key=f"nav_{page}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state.selected_page = page
                st.rerun()

    return st.session_state.selected_page



_ROTATIONS = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}

def _small(gray):
    return cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)

def _sharpness(gray):
    h, w = gray.shape
    if w > 640:
        gray = cv2.resize(gray, (640, max(int(h * 640 / w), 1)))
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())

def extract_page_frames(video_bytes, suffix=".mp4", sample_fps=2.0, change_threshold=14.0,
                        min_stable=2, max_pages=40, rotate=0):
    """Pick one sharp, stable frame per page from a video that films the pages one after another.

    sample_fps: how many frames per second are examined.
    change_threshold: how different a frame must look to count as a new page (the main tuning knob).
    min_stable: a page must stay in view for this many examined frames to count (2 = about 1 second).
    """
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(video_bytes)

        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(int(round(fps / sample_fps)), 1)

        kept, ref, seg = [], None, None

        def finish():
            nonlocal seg
            if seg and seg["count"] >= min_stable:
                if kept and np.abs(seg["small"] - kept[-1]["small"]).mean() < change_threshold:
                    if seg["sharp"] > kept[-1]["sharp"]:
                        kept[-1] = seg            # same page filmed again: keep the sharper one
                else:
                    kept.append(seg)
            seg = None

        idx = 0
        while cap.grab():
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if ok:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    small = _small(gray)
                    if ref is None or np.abs(small - ref).mean() > change_threshold:
                        finish()
                        ref = small
                        seg = {"count": 0, "sharp": -1.0, "jpeg": None, "time": 0.0, "small": small}
                    seg["count"] += 1
                    sharp = _sharpness(gray)
                    if sharp > seg["sharp"]:
                        img = cv2.rotate(frame, _ROTATIONS[rotate]) if rotate in _ROTATIONS else frame
                        ok2, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
                        if ok2:
                            seg.update(sharp=sharp, jpeg=buf.tobytes(), time=idx / fps)
            idx += 1
        finish()
        cap.release()
    finally:
        os.unlink(path)

    if len(kept) > max_pages:
        top = sorted(kept, key=lambda s: -s["sharp"])[:max_pages]
        kept = sorted(top, key=lambda s: s["time"])
    return [{"time": s["time"], "jpeg": s["jpeg"], "sharpness": s["sharp"]} for s in kept]