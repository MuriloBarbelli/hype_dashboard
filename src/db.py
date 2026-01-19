import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor

@st.cache_resource
def get_conn():
    """
    Abre uma conexão persistente com o PostgreSQL usando a URL do st.secrets.
    cache_resource evita abrir conexão a cada rerun do Streamlit.
    """
    conn = psycopg2.connect(
        st.secrets["database"]["url"],
        cursor_factory=RealDictCursor
    )

    # Recomendado para apps Streamlit (muitas leituras, reruns):
    # evita ficar preso em transações e reduz chance de "aborted transaction".
    conn.autocommit = True
    return conn

def fetch_df(sql: str, params=None):
    """
    Executa SELECT e retorna lista de dicts (bom para virar DataFrame).
    Se uma query falhar, faz rollback para não "quebrar" a conexão cacheada.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()
    except Exception:
        # Essencial quando a conexão é reaproveitada (cache_resource)
        try:
            conn.rollback()
        except Exception:
            pass
        raise

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

def refresh_materialized_views():
    """
    Atualiza as materialized views após ingestão.
    Sem CONCURRENTLY para evitar exigência de índice UNIQUE.
    """
    sql = """
    refresh materialized view public.mv_passages_v5;
    refresh materialized view public.mv_passage_classification_v5;
    """
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(sql)
    try:
        conn.commit()
    except Exception:
        pass


