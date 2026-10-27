import streamlit as st
import os 
import yaml

from src.fe.support_functions import setup_sidebar
from src.fe.styles import HIDE_SIDEBAR_NAV

################
# --- SET UP ---
################

# --- PAGE CONFIG --- 
st.set_page_config(page_title="HOME", layout="wide")

# --- STYLES ---
st.markdown(HIDE_SIDEBAR_NAV, unsafe_allow_html=True)

# --- CONFIG ---
with open(f"{os.getcwd()}/src/fe/config.yaml", "r") as config_file:
    config = yaml.safe_load(config_file)

# --- SIDEBAR & TITLE ---
selected_page = setup_sidebar(
    pages=config['pages'],
    main_page=config['main_page'])

# Navigation on click
if selected_page == "HOME":
    pass
elif selected_page == "VIDEO":
    st.switch_page("pages/VIDEO.py")

# --- LOGO-TITLE ---
col1,col2,col3 = st.columns([1,2,1])
with col2:
    st.image(f"{os.getcwd()}/input/images/teachfeed.png")

# --- DESCRIPTION ---
col1, col2 = st.columns([1,1])
with col1:
    st.header("Welcome to TeachFeed!")
    st.info("""
            This interactive web application aims to use AI to support teachers' assessment process 

            Get started by exploring the pages!
            """)

with col2: 
    st.header("PAGES:")
    with st.expander("**VIDEO**"):
        st.subheader("Video Assessment")
        st.info("Evaluate videos using AI")

        if st.button(label="EXPLORE", key='home_overview'):
            st.session_state.selected_page = "VIDEO"
            selected_page = "VIDEO"
            st.switch_page("pages/VIDEO.py")


# --- FOOTER ---
st.markdown("#")
st.divider()

col1, col2, col3 = st.columns([1,1,1])
with col1:
    st.markdown("**Mail**")
    st.markdown("📩 foliastream+teachfeed@gmail.com")
with col2:
    st.markdown("**Link**")
    st.caption("🌐 https://teachfeed.streamlit.app")
with col3:
    st.markdown("**GitHub**")
    st.caption("</>  https://github.com/FoliaStream/TeachFeed")