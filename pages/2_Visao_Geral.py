import streamlit as st
import pandas as pd
from datetime import datetime, time

from src.db import fetch_df, fetch_distinct_values
from ui.sidebar import render_sidebar_menu
from src.helpers import init_state, apply_shared_period_to_widgets, sync_shared_period_from_widgets, PERIOD_KEYS
from src.helpers import ensure_apply_state, apply_filters_now, mark_dirty, sync_period_and_mark_dirty

st.set_page_config(page_title="Visão Geral • Hype", layout="wide")

init_state()
ensure_apply_state()
apply_shared_period_to_widgets()

# --- defensivo: filtros compartilhados (evita NameError) ---
filtros = st.session_state.get("shared_filters", {}) or {}
rel = (filtros.get("relatorios", {}) or {})

# (opcional) filtros avançados vindos do relatório
rel = st.session_state.get("shared_filters", {}).get("relatorios", {})

st.session_state["current_page"] = "Visão geral"
render_sidebar_menu()

st.title("Visão geral")

@st.cache_data(ttl=60)
def fetch_event_type_options():
    sql = """
    select
      event_type_code,
      max(event_description) as event_description
    from public.events
    where event_type_code is not null
    group by event_type_code
    order by event_type_code;
    """
    rows = fetch_df(sql)
    options = []
    for r in rows:
        code = r["event_type_code"]
        desc = r["event_description"] or ""
        label = f"{code} - {desc}".strip(" -")
        options.append({"code": code, "label": label})
    return options

@st.cache_data(ttl=300)
def fetch_view_columns(view_schema: str, view_name: str) -> list[str]:
    sql = """
    select column_name
    from information_schema.columns
    where table_schema = %(schema)s
      and table_name = %(name)s
    order by ordinal_position;
    """
    rows = fetch_df(sql, {"schema": view_schema, "name": view_name})
    return [r["column_name"] for r in rows]

with st.container(border=True):
    col_event, col_start, col_end, col_btn = st.columns([1.8, 1.5, 1.5, 1.0], vertical_alignment="bottom")

    with col_event:
        event_options = fetch_event_type_options()
        event_labels = [o["label"] for o in event_options]
        selected_event_labels = st.multiselect(
            "Eventos (opcional)",
            event_labels,
            key="vg_event_labels",
            placeholder="Digite o código ou nome"
        )

    with col_start:
        c_sd, c_st = st.columns([1.1, 0.9])
        with c_sd:
            start_date = st.date_input(
                "Período inicial",
                key=PERIOD_KEYS["date_start"],
                on_change=sync_shared_period_from_widgets
            )
        with c_st:
            start_time = st.time_input( 
                "Hora",
                key=PERIOD_KEYS["time_start"],
                on_change=sync_shared_period_from_widgets
            )

    with col_end:
        c_ed, c_et = st.columns([1.1, 0.9])
        with c_ed:
            end_date = st.date_input(
                "Período final",
                key=PERIOD_KEYS["date_end"],
                on_change=sync_shared_period_from_widgets
            )
        with c_et:
            end_time = st.time_input(
                "Hora",
                key=PERIOD_KEYS["time_end"],
                on_change=sync_shared_period_from_widgets
            )


    with col_btn:
        run = st.button("Gerar relatório", type="primary", use_container_width=True, key="vg_run")
        if run:
            apply_filters_now()

    with st.expander("Filtros avançados", expanded=False):
        a1, a2, a3 = st.columns([1.4, 2.0, 1.2])

        with a1:
            search = st.text_input(
                "Texto no log / Morador / Unidade",
                placeholder="Digite um termo",
                key="vg_search"
            ).strip()

        with a2:
            accesses = st.multiselect(
                "Acesso (opcional)",
                fetch_distinct_values("access_name"),
                key="vg_accesses",
                placeholder="Selecione um acesso"
            )

        with a3:
            profiles = st.multiselect(
                "Categoria do usuário (opcional)",
                fetch_distinct_values("user_profile"),
                key="vg_profiles",
                placeholder="Selecione categorias"
            )

if "vg_last_filter_key" not in st.session_state:
    st.session_state.vg_last_filter_key = None
    run = True  # primeira carga

start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)

label_to_code = {o["label"]: o["code"] for o in event_options}
event_types = [label_to_code[lbl] for lbl in selected_event_labels] if selected_event_labels else []

filter_key = (start_dt, end_dt, tuple(event_types), tuple(accesses), tuple(profiles), search)
if st.session_state.vg_last_filter_key != filter_key:
    st.session_state.vg_last_filter_key = filter_key
    run = True

if not run:
    st.info("Ajuste os filtros acima e clique em **Gerar relatório**.")
    st.stop()

if st.session_state["filters_dirty"]:
    st.info("Ajuste os filtros acima e clique em Gerar relatório.")
    st.stop()

where = ["event_timestamp between %(start)s and %(end)s"]
params = {"start": start_dt, "end": end_dt}

if event_types:
    where.append("event_type_code = any(%(event_types)s)")
    params["event_types"] = event_types

if accesses:
    where.append("access_name = any(%(accesses)s)")
    params["accesses"] = accesses

if profiles:
    where.append("user_profile = any(%(profiles)s)")
    params["profiles"] = profiles

if search:
    where.append("""
      (
        event_description ilike %(search)s
        or user_name ilike %(search)s
        or unit ilike %(search)s
        or unit_group ilike %(search)s
      )
    """)
    params["search"] = f"%{search}%"

where_sql = " and ".join(where)

# --- KPIs de PASSAGENS (vw_passage_classification_v5) ---
view_cols = fetch_view_columns("public", "vw_passage_classification_v5")

# Mapeia nomes possíveis (pra você não ficar refém do nome exato da coluna)
def pick_col(*candidates):
    for c in candidates:
        if c in view_cols:
            return c
    return None

col_ts_start = pick_col("open_ts")
col_access   = pick_col("door_access_name")
col_profile  = pick_col("user_profile")
col_search_1 = pick_col("user_name")
col_search_2 = pick_col("unit")
col_cause    = pick_col("cause_code")

# Where da view (defensivo: só aplica cláusulas se a coluna existir)
where_p = []
params_p = {}

if col_ts_start:
    where_p.append(f"{col_ts_start} between %(start)s and %(end)s")
    params_p.update({"start": start_dt, "end": end_dt})
else:
    st.error("Não encontrei coluna de início da passagem na view vw_passage_classification_v5.")
    st.stop()

if accesses and col_access:
    where_p.append(f"{col_access} = any(%(accesses)s)")
    params_p["accesses"] = accesses

if profiles and col_profile:
    where_p.append(f"{col_profile} = any(%(profiles)s)")
    params_p["profiles"] = profiles

if search:
    parts = []
    if col_search_1: parts.append(f"{col_search_1} ilike %(search)s")
    if col_search_2: parts.append(f"{col_search_2} ilike %(search)s")
    if parts:
        where_p.append("(" + " or ".join(parts) + ")")
        params_p["search"] = f"%{search}%"

where_p_sql = " and ".join(where_p) if where_p else "true"

# Expressões dos KPIs com fallback:
facial_expr = f"sum(case when {col_cause} = 701 then 1 else 0 end)"
botoeira_expr = f"sum(case when {col_cause} = 177 then 1 else 0 end)"
sem_causa_expr = f"sum(case when {col_cause} is null then 1 else 0 end)"

st.subheader("Resumo do período")

kpi_sql = """
with base as (
  select
    date(open_ts) as dia,
    nullif(trim(user_name), '') as user_name,
    user_profile
  from public.vw_passage_classification_v5
  where open_ts between %(start)s and %(end)s
),
pessoas as (
  select
    count(distinct user_name) as pessoas_unicas,
    count(distinct case
      when user_profile in ('Morador', 'Morador/Proprietário') then user_name
    end) as pessoas_moradoras
  from base
)
select
  (select count(*) from base) as total_passagens,
  (select count(distinct dia) from base) as dias,
  pessoas_unicas,
  pessoas_moradoras
from pessoas;
"""

kpi = fetch_df(kpi_sql, {"start": start_dt, "end": end_dt})[0]

total_passagens = kpi["total_passagens"] or 0
dias = max(kpi["dias"] or 1, 1)
media_dia = round(total_passagens / dias, 1)

pessoas_unicas = kpi["pessoas_unicas"] or 0
pessoas_moradoras = kpi["pessoas_moradoras"] or 0
pessoas_nao_moradoras = max(pessoas_unicas - pessoas_moradoras, 0)

# % sempre fecha 100% (quando há pessoas)
pct_moradores = round((pessoas_moradoras / pessoas_unicas) * 100, 1) if pessoas_unicas else 0.0
pct_nao_moradores = round(100.0 - pct_moradores, 1) if pessoas_unicas else 0.0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Passagens no período", f"{total_passagens:,}")
c2.metric("Média diária", media_dia)
c3.metric("Pessoas únicas", f"{pessoas_unicas:,}")
c4.metric("Moradores (%)", f"{pct_moradores}%")
c5.metric("Não-moradores (%)", f"{pct_nao_moradores}%")


st.divider()

# --- Série diária (passagens por dia) ---
day_pass_sql = f"""
select
  date_trunc('day', {col_ts_start}) as dia,
  count(*)::bigint as passagens
from public.vw_passage_classification_v5
where {where_p_sql}
group by 1
order by 1;
"""
df_day_p = pd.DataFrame(fetch_df(day_pass_sql, params_p))

st.subheader("Passagens por dia")
if df_day_p.empty:
    st.info("Sem passagens no período selecionado.")
else:
    df_day_p["dia"] = pd.to_datetime(df_day_p["dia"])
    df_day_p = df_day_p.set_index("dia")
    st.line_chart(df_day_p["passagens"])

st.subheader("Fila humana — passagens sem causa (para auditoria)")

if not col_cause:
    st.warning("A view não tem coluna de causa (cause_code). Não dá pra montar a fila humana ainda.")
else:
    fila_sql = f"""
    select *
    from public.vw_passage_classification_v5
    where {where_p_sql}
      and {col_cause} is null
    order by {col_ts_start} desc
    limit 200;
    """
    df_fila = pd.DataFrame(fetch_df(fila_sql, params_p))

    if df_fila.empty:
        st.success("Boa: nenhuma passagem sem causa no período.")
    else:
        st.dataframe(df_fila, hide_index=True)
        st.caption("Dica: use essa lista para escolher casos e inspecionar na vw_passage_audit_v5 (contexto ±10s).")

hour_sql = f"""
select
  extract(hour from event_timestamp)::int as hora,
  count(*)::bigint as eventos
from public.events
where {where_sql}
group by 1
order by 1;
"""
df_hour = pd.DataFrame(fetch_df(hour_sql, params))
if not df_hour.empty:
    df_hour = df_hour.set_index("hora")
    st.subheader("Eventos por hora do dia")
    st.bar_chart(df_hour["eventos"])
else:
    st.info("Sem dados por hora no período selecionado.")

st.divider()

colA, colB = st.columns(2)

top_access_sql = f"""
select
  access_name,
  count(*)::bigint as eventos
from public.events
where {where_sql}
  and access_name is not null
  and access_name <> ''
group by 1
order by 2 desc
limit 15;
"""
df_top_access = pd.DataFrame(fetch_df(top_access_sql, params))

top_types_sql = f"""
select
  concat_ws(' - ', event_type_code::text, max(event_description)) as evento,
  count(*)::bigint as eventos
from public.events
where {where_sql}
  and event_type_code is not null
group by event_type_code
order by 2 desc
limit 15;
"""
df_top_types = pd.DataFrame(fetch_df(top_types_sql, params))

with colA:
    st.subheader("Top 15 Acessos")
    if df_top_access.empty:
        st.info("Sem dados para Top Acessos.")
    else:
        st.dataframe(df_top_access, use_container_width=True, hide_index=True)

with colB:
    st.subheader("Top 15 Tipos de Evento")
    if df_top_types.empty:
        st.info("Sem dados para Top Tipos.")
    else:
        st.dataframe(df_top_types, use_container_width=True, hide_index=True)

prof_sql = f"""
select
  coalesce(nullif(user_profile,''), 'Sem categoria') as categoria,
  count(*)::bigint as eventos
from public.events
where {where_sql}
group by 1
order by 2 desc;
"""
df_prof = pd.DataFrame(fetch_df(prof_sql, params))
st.subheader("Eventos por categoria de usuário")
if df_prof.empty:
    st.info("Sem dados para categorias.")
else:
    df_prof = df_prof.set_index("categoria")
    st.bar_chart(df_prof["eventos"])