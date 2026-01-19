import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

if "data_mode" not in st.session_state:
    st.session_state["data_mode"] = "anon"  # padrão seguro

def get_events_source() -> str:
    return "public.events" if st.session_state["data_mode"] == "real" else "public.vw_events_anon"

st.switch_page("pages/1_Relatorios.py")  # ou o seu "0_Relatorios.py"
st.stop()
