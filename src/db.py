import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor

@st.cache_resource
def get_conn():
    """
    Abre uma conexão persistente com o PostgreSQL usando a URL do st.secrets.
    cache_resource evita abrir conexão a cada rerun do Streamlit.
    """
    return psycopg2.connect(st.secrets["database"]["url"], cursor_factory=RealDictCursor)

def fetch_df(sql: str, params=None):
    """
    Executa SELECT e retorna lista de dicts (bom para virar DataFrame).
    """
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()
