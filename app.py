import os
import sys
from pathlib import Path
import streamlit as st
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).parent))

load_dotenv()

if "data_mode" not in st.session_state:
    st.session_state["data_mode"] = "anon"  # padrão seguro

def get_events_source() -> str:
    return "public.events" if st.session_state["data_mode"] == "real" else "public.vw_events_anon"

st.switch_page("pages/0_Contexto_do_Projeto.py")  # Página padrão de abertura do link
st.stop()
