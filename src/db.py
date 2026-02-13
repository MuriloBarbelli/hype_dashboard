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
    sql = """
    with bounds as (
      select
        min(event_timestamp) as start_ts,
        max(event_timestamp) as end_ts
      from public.events
      where source_file = %(source_file)s
    ),
    e as (
      select
        event_id,
        event_timestamp,
        access_name
      from public.events
      where source_file = %(source_file)s
    ),
    p as (
      select
        open_event_id,
        open_ts,
        close_ts,
        seconds_open,
        door_access_name,
        cause_event_id,
        cause_code,
        passage_kind,
        confianca_causa,
        has_held_open,
        has_failed_close,
        has_door_alert
      from public.mv_passage_classification_v5
      where open_ts <= ((select end_ts from bounds) + ((%(slack)s || ' seconds')::interval))
        and close_ts >= ((select start_ts from bounds) - ((%(slack)s || ' seconds')::interval))
    ),
    candidates as (
      select
        e.event_id,
        p.open_event_id,
        p.open_ts,
        p.close_ts,
        p.door_access_name,
        p.cause_event_id,
        p.cause_code,
        p.passage_kind,
        p.confianca_causa,
        p.seconds_open,
        p.has_held_open,
        p.has_failed_close,
        p.has_door_alert
      from e
      join p
        on p.door_access_name = e.access_name
       and e.event_timestamp >= (p.open_ts - ((%(slack)s || ' seconds')::interval))
       and e.event_timestamp <= (p.close_ts + ((%(slack)s || ' seconds')::interval))
    ),
    best as (
      select distinct on (event_id)
        *
      from candidates
      order by event_id, open_ts desc
    ),
    final as (
      select
        event_id,

        open_event_id as audit_group,
        open_ts as matched_open_ts,
        close_ts as matched_close_ts,
        door_access_name as matched_door_access_name,

        case
          when open_event_id is null then 'UNGROUPED'
          when event_id = open_event_id then 'OPEN'
          when event_id = cause_event_id then 'CAUSE'
          else 'IN_GROUP'
        end as audit_role,

        passage_kind,
        cause_event_id,
        cause_code,
        confianca_causa,

        seconds_open,
        has_held_open,
        has_failed_close,
        has_door_alert,

        case
          when open_event_id is null then null
          else (
            'Categoria: ' ||
            (case passage_kind
              when 'entrada_facial' then 'Entrada (Facial)'
              when 'saida_botoeira' then 'Saída (Botoeira)'
              when 'entrada_botoeira' then 'Entrada (Botoeira)'
              when 'saida_facial' then 'Saída (Facial)'
              when 'entrada_sem_id' then 'Entrada (Sem identificação)'
              when 'saida_sem_id' then 'Saída (Sem identificação)'
              else coalesce(passage_kind, '—')
            end)
            || E'\nCausa: ' ||
            (case cause_code
              when 701 then 'Reconhecimento facial'
              when 177 then 'Botoeira de saída'
              when 165 then 'Porta abriu'
              when 167 then 'Porta fechou'
              else ('Código ' || coalesce(cause_code::text,'—'))
            end)
            || E'\nConfiança: ' ||
            (case lower(coalesce(confianca_causa,''))
              when 'alta' then 'Alta'
              when 'media' then 'Média'
              when 'baixa' then 'Baixa'
              else '—'
            end)
          )
        end as audit_interpretation,

        case
          when open_event_id is null then null
          else (
            80
            - (case when lower(coalesce(confianca_causa,''))='media' then 15 else 0 end)
            - (case when lower(coalesce(confianca_causa,''))='baixa' then 30 else 0 end)
            - (case when coalesce(has_failed_close,false) then 20 else 0 end)
            - (case when coalesce(has_door_alert,false) then 15 else 0 end)
            - (case when coalesce(has_held_open,false) then 10 else 0 end)
            - (case when coalesce(seconds_open,0) >= 30 then 10 else 0 end)
            - (case when coalesce(seconds_open,0) >= 120 then 20 else 0 end)
          )
        end as audit_score
      from best
    )
    insert into public.event_audit_map (
      event_id,
      audit_group, matched_open_ts, matched_close_ts, matched_door_access_name,
      audit_role,
      passage_kind, cause_event_id, cause_code, confianca_causa,
      seconds_open, has_held_open, has_failed_close, has_door_alert,
      audit_interpretation, audit_score,
      computed_at
    )
    select
      event_id,
      audit_group, matched_open_ts, matched_close_ts, matched_door_access_name,
      audit_role,
      passage_kind, cause_event_id, cause_code, confianca_causa,
      seconds_open, has_held_open, has_failed_close, has_door_alert,
      audit_interpretation, audit_score,
      now()
    from final
    on conflict (event_id) do update set
      audit_group = excluded.audit_group,
      matched_open_ts = excluded.matched_open_ts,
      matched_close_ts = excluded.matched_close_ts,
      matched_door_access_name = excluded.matched_door_access_name,
      audit_role = excluded.audit_role,
      passage_kind = excluded.passage_kind,
      cause_event_id = excluded.cause_event_id,
      cause_code = excluded.cause_code,
      confianca_causa = excluded.confianca_causa,
      seconds_open = excluded.seconds_open,
      has_held_open = excluded.has_held_open,
      has_failed_close = excluded.has_failed_close,
      has_door_alert = excluded.has_door_alert,
      audit_interpretation = excluded.audit_interpretation,
      audit_score = excluded.audit_score,
      computed_at = now();
    """

    conn = get_conn()
    conn = _ensure_conn_alive(conn)
    with conn.cursor() as cur:
        cur.execute(sql, {"source_file": source_file, "slack": int(slack_seconds)})
    try:
        conn.commit()
    except Exception:
        pass
