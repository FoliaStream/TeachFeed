import streamlit as st

import os 

from streamlit_option_menu import option_menu
from src.fe.styles import SIDEBAR_STYLES


# SIDEBAR
def setup_sidebar(pages,
                  main_page):
    with st.sidebar:
        col1, col2, col3 = st.columns([0.2, 2.4, 0.2])
        with col2:
            st.image(f"{os.getcwd()}/input/images/teachfeed_sb.jpeg", use_container_width=True)

        # Initialize session state for page if it doesn't exist
        if 'selected_page' not in st.session_state:
            st.session_state.selected_page = main_page
        
        choose = option_menu("", 
                            pages,
                            default_index=pages.index(st.session_state.selected_page),
                            styles=SIDEBAR_STYLES)
        
        # Refresh
        if choose != st.session_state.selected_page:
            st.session_state.selected_page = choose
            st.rerun()  
    
    return st.session_state.selected_page