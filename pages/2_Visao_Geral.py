import streamlit as st
import pandas as pd
from datetime import datetime, time

from src.db import fetch_df, fetch_distinct_values
from ui.sidebar import render_sidebar_menu
from src.helpers import init_state, seed_period_widgets_from_shared, sync_shared_period_from_widgets, PERIOD_KEYS

init_state()

seed_period_widgets_from_shared()

st.session_state["current_page"] = "Visão geral"
render_sidebar_menu()

st.set_page_config(page_title="Visão Geral • Hype", layout="wide")
st.title("Visão geral")

shared = st.session_state.get("shared_filters")
if shared:
    start_dt = shared["start_dt"]
    end_dt = shared["end_dt"]

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


with st.container(border=True):
    col_event, col_start, col_end, col_btn = st.columns([2.4, 1.5, 1.5, 1.0], vertical_alignment="bottom")

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
        c_sd, c_st = st.columns([1.2, 0.8])
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
        c_ed, c_et = st.columns([1.2, 0.8])
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

kpi_sql = f"""
select
  count(*)::bigint as total_eventos,
  count(distinct access_name)::bigint as acessos_distintos,
  count(distinct user_name)::bigint as pessoas_distintas,
  count(distinct unit)::bigint as unidades_distintas
from public.events
where {where_sql};
"""
kpi = fetch_df(kpi_sql, params)[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total de eventos", f"{kpi['total_eventos']:,}")
c2.metric("Acessos distintos", f"{kpi['acessos_distintos']:,}")
c3.metric("Pessoas distintas", f"{kpi['pessoas_distintas']:,}")
c4.metric("Unidades distintas", f"{kpi['unidades_distintas']:,}")

st.divider()

day_sql = f"""
select
  date_trunc('day', event_timestamp) as dia,
  count(*)::bigint as eventos
from public.events
where {where_sql}
group by 1
order by 1;
"""
df_day = pd.DataFrame(fetch_df(day_sql, params))
if not df_day.empty:
    df_day["dia"] = pd.to_datetime(df_day["dia"])
    df_day = df_day.set_index("dia")
    st.subheader("Eventos por dia")
    st.line_chart(df_day["eventos"])
else:
    st.info("Sem dados para série por dia no período selecionado.")

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
