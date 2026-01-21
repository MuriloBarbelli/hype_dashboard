import streamlit as st
from ui.sidebar import render_sidebar_menu

st.session_state["current_page"] = "Usuários (Em Breve)"
render_sidebar_menu()
