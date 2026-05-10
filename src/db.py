import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import OperationalError, InterfaceError, DatabaseError
import psycopg2
import psycopg2

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
    except psycopg2.errors.QueryCanceled as e:
        # timeout/statement_timeout -> NÃO retenta
        raise e
    
    except psycopg2.errors.QueryCanceled as e:
        # timeout/statement_timeout -> NÃO retenta
        raise e
    
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

def pick_cause_event_for_passage(events_in_window, open_event_id, open_ts, slack_seconds: int):
    """
    Escolhe o evento 'causa' (gatilho) para uma passagem.
    - Procura eventos ANTES do open_ts, dentro de (slack_seconds)
    - Prioriza tipos (facial/botoeira/comando)
    - Fallback: o próprio OPEN (165)
    """
    if events_in_window is None or events_in_window.empty:
        return {"cause_event_id": open_event_id, "cause_code": 165, "cause_desc": "PORTA ABRIU"}

    # somente eventos até o open_ts e dentro da janela
    window_start = open_ts - pd.Timedelta(seconds=slack_seconds)
    cand = events_in_window[
        (events_in_window["event_timestamp"] >= window_start) &
        (events_in_window["event_timestamp"] <= open_ts)
    ].copy()

    if cand.empty:
        return {"cause_event_id": open_event_id, "cause_code": 165, "cause_desc": "PORTA ABRIU"}

    # Nunca permitir 167 como causa
    cand = cand[cand["event_type_code"] != 167]
    if cand.empty:
        return {"cause_event_id": open_event_id, "cause_code": 165, "cause_desc": "PORTA ABRIU"}

    # Prioridade (ajuste aqui quando você mapear os códigos reais)
    PRIORITY = {
        701: 1,  # exemplo: facial entrada
        702: 1,  # exemplo: facial saída
        177: 2,  # botoeira
        165: 9,  # porta abriu (fallback fraco)
    }
    cand["prio"] = cand["event_type_code"].map(PRIORITY).fillna(5)

    # critério: menor prio, e o mais próximo do open_ts (mais recente)
    cand = cand.sort_values(["prio", "event_timestamp"], ascending=[True, False])

    top = cand.iloc[0]
    return {
        "cause_event_id": str(top["event_id"]),
        "cause_code": int(top["event_type_code"]),
        "cause_desc": str(top.get("event_description") or ""),
    }

def refresh_materialized_views():
    """
    Atualiza as materialized views após ingestão.
    Sem CONCURRENTLY para evitar exigência de índice UNIQUE.
    """
    sql = """
    refresh materialized view public.mv_passages_v5;
    refresh materialized view public.mv_passages_v6;

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
    -- MATERIALIZED VIEW: mv_passage_classification_v5
    -- =========================================================
    -- Bitmap Index Scan primário em filtros de período (usado pelo planner)
    create index if not exists idx_mv_passages_open_ts
    on public.mv_passage_classification_v5 (open_ts);

    -- Cobre queries com filtro simultâneo por porta + período + close_ts
    create index if not exists idx_mv_passages_access_open_close
    on public.mv_passage_classification_v5 (door_access_name, open_ts, close_ts);

    -- =========================================================
    -- MATERIALIZED VIEW: mv_passages_v6 (PORTAS)
    -- =========================================================
    create index if not exists idx_mv_passages_v6_open_ts
    on public.mv_passages_v6 (open_ts);

    create index if not exists idx_mv_passages_v6_door_open
    on public.mv_passages_v6 (door_access_name, open_ts);

    create index if not exists idx_mv_passages_v6_seconds
    on public.mv_passages_v6 (seconds_open);
    """

    conn = get_conn()
    conn = _ensure_conn_alive(conn)
    with conn.cursor() as cur:
        cur.execute(sql)
    try:
        conn.commit()
    except Exception:
        pass

def build_event_audit_map_for_source_file(source_file: str, slack_seconds: int = 30):
    """
    Calcula/atualiza o cache de auditoria SOMENTE para os eventos de um source_file.
    Roda depois do INSERT + refresh das MVs.
    """
    sql = """
    with bounds as (
    select
        min(event_timestamp) as min_ts,
        max(event_timestamp) as max_ts
    from public.events
    where source_file = %(source_file)s
    ),

    -- passagens "cruas" (tem close_event_id)
    p_base as (
    select
        p.open_event_id,
        p.open_ts,
        p.close_ts,
        p.close_event_id,
        p.seconds_open,
        p.door_access_name
    from public.mv_passages_v5 p
    join bounds b on true
    where p.open_ts >= (b.min_ts - (%(slack)s || ' seconds')::interval)
        and p.open_ts <= (b.max_ts + (%(slack)s || ' seconds')::interval)
    ),

    -- classificação (tem passage_kind / causa / confiança etc)
    p_class as (
    select
        c.open_event_id,
        c.cause_event_id,
        c.cause_ts,
        c.cause_code,
        c.cause_desc,
        c.user_name,
        c.user_profile,
        c.unit,
        c.unit_group,
        c.handler_name,
        c.handler_profile,
        c.has_held_open,
        c.has_failed_close,
        c.has_door_alert,
        c.passage_kind,
        c.confianca_causa
    from public.vw_passage_classification_v5 c
    join bounds b on true
    where c.open_ts >= (b.min_ts - (%(slack)s || ' seconds')::interval)
        and c.open_ts <= (b.max_ts + (%(slack)s || ' seconds')::interval)
    ),

    -- junta base + classificação
    p as (
    select
        pb.open_event_id,
        pb.open_ts,
        pb.close_ts,
        pb.close_event_id,
        pb.seconds_open,
        pb.door_access_name,
        pc.cause_event_id,
        pc.cause_ts,
        pc.cause_code,
        pc.cause_desc,
        pc.user_name,
        pc.user_profile,
        pc.unit,
        pc.unit_group,
        pc.handler_name,
        pc.handler_profile,
        pc.has_held_open,
        pc.has_failed_close,
        pc.has_door_alert,
        pc.passage_kind,
        pc.confianca_causa
    from p_base pb
    left join p_class pc using (open_event_id)
    ),

    e as (
    select
        event_id,
        event_timestamp,
        access_name
    from public.events
    where source_file = %(source_file)s
    ),

    candidates as (
    select
        e.event_id,
        p.open_event_id as audit_group,
        p.open_ts as matched_open_ts,
        p.close_ts as matched_close_ts,
        p.door_access_name as matched_door_access_name,
        p.cause_event_id,
        p.cause_code,
        p.confianca_causa,
        p.passage_kind
    from e
    join p
        on e.access_name = p.door_access_name
    and e.event_timestamp >= (p.open_ts  - (%(slack)s || ' seconds')::interval)
    and e.event_timestamp <= (p.close_ts + (%(slack)s || ' seconds')::interval)
    ),

    best as (
    select distinct on (event_id)
        *
    from candidates
    order by event_id,
            abs(extract(epoch from (matched_open_ts - (select event_timestamp from public.events ee where ee.event_id=candidates.event_id))))
    ),

    final as (
    select
        e.event_id,
        b.audit_group,
        b.matched_open_ts,
        b.matched_close_ts,
        b.matched_door_access_name,
        case
        when b.audit_group is null then 'UNGROUPED'
        when e.event_id = b.audit_group then 'OPEN'
        when e.event_id = (select close_event_id from public.mv_passages_v5 pp where pp.open_event_id=b.audit_group limit 1) then 'CLOSE'
        when e.event_id = b.cause_event_id then 'CAUSE'
        else 'IN_GROUP'
        end as audit_role,
        b.passage_kind,
        b.cause_code,
        b.confianca_causa
    from e
    left join best b on b.event_id = e.event_id
    )

    insert into public.event_audit_map (
    event_id,
    audit_group,
    matched_open_ts,
    matched_close_ts,
    matched_door_access_name,
    audit_role,
    passage_kind,
    cause_code,
    confianca_causa,
    computed_at
    )
    select
    event_id,
    audit_group,
    matched_open_ts,
    matched_close_ts,
    matched_door_access_name,
    audit_role,
    passage_kind,
    cause_code,
    confianca_causa,
    now()
    from final
    where event_id is not null
    on conflict (event_id) do update set
    audit_group = excluded.audit_group,
    matched_open_ts = excluded.matched_open_ts,
    matched_close_ts = excluded.matched_close_ts,
    matched_door_access_name = excluded.matched_door_access_name,
    audit_role = excluded.audit_role,
    passage_kind = excluded.passage_kind,
    cause_code = excluded.cause_code,
    confianca_causa = excluded.confianca_causa,
    computed_at = excluded.computed_at;
    """

    conn = get_conn()
    conn = _ensure_conn_alive(conn)
    with conn.cursor() as cur:
        cur.execute(sql, {"source_file": source_file, "slack": int(slack_seconds)})
    try:
        conn.commit()
    except Exception:
        pass

def fetch_distinct_source_files():
    sql = """
    select distinct source_file
    from public.events
    where source_file is not null and btrim(source_file) <> ''
    order by 1;
    """
    rows = fetch_df(sql)
    return [r["source_file"] for r in rows]

def rebuild_event_audit_map_all_sources(slack_seconds: int = 30):
    """
    Recalcula o event_audit_map para TODO o banco, por source_file.
    Não apaga nada: é UPSERT (on conflict do update).
    """
    source_files = fetch_distinct_source_files()
    for sf in source_files:
        build_event_audit_map_for_source_file(sf, slack_seconds=slack_seconds)

    return len(source_files)

def rebuild_event_audit_map_all_source_files(slack_seconds=30):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            select distinct source_file
            from public.events
            where source_file is not null and btrim(source_file) <> ''
            order by 1;
        """)
        rows = cur.fetchall()
    source_files = [r["source_file"] for r in rows]

    for sf in source_files:
        build_event_audit_map_for_source_file(sf, slack_seconds=slack_seconds)

    return len(source_files)
