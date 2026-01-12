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

@st.cache_data(ttl=60)
def fetch_distinct_values(column: str):
    # proteção simples pra evitar SQL injection por nome de coluna
    allowed = {"event_type_code", "access_name", "unit_group", "unit", "user_name", "user_profile"}
    if column not in allowed:
        raise ValueError(f"Coluna não permitida: {column}")

    sql = f"""
    select distinct {column} as value
    from public.events
    where {column} is not null
    order by 1;
    """
    rows = fetch_df(sql)
    return [r["value"] for r in rows]