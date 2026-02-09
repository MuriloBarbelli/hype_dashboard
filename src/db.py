import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import OperationalError, InterfaceError, DatabaseError

def _open_conn():
    conn = psycopg2.connect(
        st.secrets["database"]["url"],
        cursor_factory=RealDictCursor,
        connect_timeout=10,
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )
    conn.autocommit = True
    return conn

@st.cache_resource
def get_conn():
    """
    Retorna uma conexão cacheada, MAS:
    - se ela morrer, será recriada automaticamente
    """
    return _open_conn()

def _ensure_conn_alive(conn):
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
        return conn
    except (OperationalError, InterfaceError, DatabaseError):
        # conexão morreu → recria
        st.cache_resource.clear()
        return get_conn()

def fetch_df(sql: str, params=None):
    """
    Executa SELECT e retorna lista de dicts (bom para virar DataFrame).
    Se uma query falhar, faz rollback para não "quebrar" a conexão cacheada.
    """
    conn = get_conn()
    conn = _ensure_conn_alive(conn)

    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or {})
            return cur.fetchall()
    except (OperationalError, InterfaceError, DatabaseError):
        # se caiu DURANTE a query, tenta 1x de novo
        st.cache_resource.clear()
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

def ensure_db_objects():
    """
    Garante objetos essenciais no banco (ex: índices) para performance.
    Pode ser chamado em toda ingestão sem medo: usa IF NOT EXISTS.
    """
    sql = """
    -- =========================================================
    -- EVENTS (tabela base)
    -- =========================================================
    create index if not exists idx_events_timestamp
    on public.events (event_timestamp);

    create index if not exists idx_events_access_ts
    on public.events (access_name, event_timestamp);

    -- =========================================================
    -- MATERIALIZED VIEW: mv_passage_classification_v5
    -- (essa é a que costuma deixar a auditoria lenta)
    -- =========================================================
    create index if not exists idx_mv_passages_open_ts
    on public.mv_passage_classification_v5 (open_ts);

    create index if not exists idx_mv_passages_access_open
    on public.mv_passage_classification_v5 (door_access_name, open_ts);

    create index if not exists idx_mv_passages_access_open_close
    on public.mv_passage_classification_v5 (door_access_name, open_ts, close_ts);


    -- (opcional, mas ajuda muito quando você filtra por open_event_id)
    create index if not exists idx_mv_passages_open_event_id
    on public.mv_passage_classification_v5 (open_event_id);
    """

    conn = get_conn()
    conn = _ensure_conn_alive(conn)
    with conn.cursor() as cur:
        cur.execute(sql)
    try:
        conn.commit()
    except Exception:
        pass

