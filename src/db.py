import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import OperationalError, InterfaceError, DatabaseError
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

def build_event_audit_map_for_source_file(source_file: str, slack_seconds: int = 30):
    """
    Calcula/atualiza o cache de auditoria SOMENTE para os eventos de um source_file.
    Roda depois do INSERT + refresh das MVs.
    """
    sql = f"""
    with e as (
    select
        event_id,
        event_timestamp,
        event_type_code,
        event_description,
        access_name,
        user_name,
        user_profile,
        unit,
        unit_group,
        handler_name,
        handler_profile,
        treatment,
        source_file
    from public.events
    where source_file = %(source_file)s
    ),
    bounds as (
    select
        min(event_timestamp) as min_ts,
        max(event_timestamp) as max_ts
    from e
    ),
    p as (
    -- base de passagens (com close_event_id) + classificação (passage_kind/cause)
    select
        p0.open_event_id,
        p0.open_ts,
        p0.close_event_id,
        p0.close_ts,
        p0.seconds_open,
        p0.door_access_name,

        c.cause_event_id,
        c.cause_code,
        c.passage_kind,
        c.confianca_causa
    from public.mv_passages_v5 p0
    left join public.vw_passage_classification_v5 c
        on c.open_event_id = p0.open_event_id

    where
        -- limita passagens ao intervalo do arquivo pra performance
        p0.open_ts >= (select min_ts from bounds) - interval '2 hours'
        and p0.open_ts <= (select max_ts from bounds) + interval '2 hours'
        and p0.door_access_name in (select distinct access_name from e)
    ),
    matched as (
    select
        e.event_id,

        m.open_event_id as audit_group,
        m.open_ts      as matched_open_ts,
        m.close_ts     as matched_close_ts,
        m.door_access_name as matched_door_access_name,

        case
        when m.open_event_id is null then 'UNGROUPED'
        when e.event_id = m.open_event_id then 'OPEN'
        when e.event_id = m.cause_event_id then 'CAUSE'
        when e.event_id = m.close_event_id then 'CLOSE'
        else 'IN_GROUP'
        end as audit_role,

        case
        when m.open_event_id is null then null
        else (
            'Categoria: ' ||
            (case m.passage_kind
            when 'entrada_facial' then 'Entrada (Facial)'
            when 'saida_botoeira' then 'Saída (Botoeira)'
            when 'entrada_botoeira' then 'Entrada (Botoeira)'
            when 'saida_facial' then 'Saída (Facial)'
            when 'entrada_sem_id' then 'Entrada (Sem identificação)'
            when 'saida_sem_id' then 'Saída (Sem identificação)'
            else coalesce(m.passage_kind, '—')
            end)
            || E'\\nCausa: ' ||
            (case m.cause_code
            when 701 then 'Reconhecimento facial'
            when 177 then 'Botoeira de saída'
            when 370 then 'Abrir porta (base)'
            when 165 then 'Porta abriu'
            when 167 then 'Porta fechou'
            else ('Código ' || coalesce(m.cause_code::text,'—'))
            end)
            || E'\\nConfiança: ' ||
            (case lower(coalesce(m.confianca_causa,''))
            when 'alta' then 'Alta'
            when 'media' then 'Média'
            when 'baixa' then 'Baixa'
            else '—'
            end)
        )
        end as audit_interpretation,

        case
        when m.open_event_id is null then null
        else (
            80
            - (case when lower(coalesce(m.confianca_causa,''))='media' then 15 else 0 end)
            - (case when lower(coalesce(m.confianca_causa,''))='baixa' then 30 else 0 end)
            - (case when coalesce(m.close_ts, m.open_ts) is null then 10 else 0 end)
            - (case when coalesce(m.seconds_open,0) >= 30 then 10 else 0 end)
            - (case when coalesce(m.seconds_open,0) >= 120 then 20 else 0 end)
        )
        end as audit_score
    from e
    left join lateral (
        select *
        from p
        where p.door_access_name = e.access_name
        and e.event_timestamp >= (p.open_ts  - ( %(slack)s || ' seconds')::interval)
        and e.event_timestamp <= (p.close_ts + ( %(slack)s || ' seconds')::interval)
        -- REGRA CERTA: pega a passagem mais recente cujo intervalo contém o evento
        order by p.open_ts desc
        limit 1
    ) m on true
    )
    insert into public.event_audit_map (
    event_id,
    audit_group,
    matched_open_ts,
    matched_close_ts,
    matched_door_access_name,
    audit_role,
    audit_interpretation,
    audit_score,
    computed_at
    )
    select
    event_id,
    audit_group,
    matched_open_ts,
    matched_close_ts,
    matched_door_access_name,
    audit_role,
    audit_interpretation,
    audit_score,
    now()
    from matched
    where audit_group is not null
    on conflict (event_id) do update set
    audit_group = excluded.audit_group,
    matched_open_ts = excluded.matched_open_ts,
    matched_close_ts = excluded.matched_close_ts,
    matched_door_access_name = excluded.matched_door_access_name,
    audit_role = excluded.audit_role,
    audit_interpretation = excluded.audit_interpretation,
    audit_score = excluded.audit_score,
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
