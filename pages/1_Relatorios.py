import streamlit as st
import pandas as pd
import math
from datetime import datetime, time

from src.helpers import fetch_event_type_options, fetch_distinct_values, fetch_df, render_kiper_table
from src.helpers import init_state, apply_shared_period_to_widgets, sync_shared_period_from_widgets, PERIOD_KEYS
from src.helpers import ensure_apply_state, apply_filters_now, mark_dirty, sync_period_and_mark_dirty
from ui.sidebar import render_sidebar_menu

st.set_page_config(page_title="Relatórios • Hype", layout="wide")

init_state()
ensure_apply_state()
apply_shared_period_to_widgets()

st.session_state["current_page"] = "Relatórios"
render_sidebar_menu()

# ============================================================
# PAGE: Relatórios (filtros em cima + paginação embaixo)
# ============================================================

st.header("Relatórios • Eventos")

# ----------------------------
# A) Filtros no topo (layout tipo Kiper)
# ----------------------------
with st.container(border=True):

    # ===== Linha 1: Evento | Período inicial (data+hora) | Período final (data+hora) | Botão
    col_event, col_start, col_end, col_btn = st.columns([1.8, 1.5, 1.5, 1.0], vertical_alignment="bottom")

    with col_event:
        event_options = fetch_event_type_options()
        event_labels = [o["label"] for o in event_options]
        selected_event_labels = st.multiselect(
            "Eventos (opcional)",
            event_labels,
            key="event_labels",
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
        run = st.button("Gerar relatório", type="primary", use_container_width=True, key="rel_run")
        if run:
            apply_filters_now()

    if run:
        sync_shared_period_from_widgets()

    # ===== Filtros avançados (recolhível)
    with st.expander("Filtros avançados", expanded=False):
        a1, a2, a3 = st.columns([1.4, 2.0, 1.2])

        with a1:
            search = st.text_input(
                "Texto no log / Morador / Unidade",
                placeholder="Digite um termo",
                key="search"
            ).strip()

        with a2:
            accesses = st.multiselect(
                "Acesso (opcional)",
                fetch_distinct_values("access_name"),
                key="accesses",
                placeholder="Selecione um acesso"
            )

        with a3:
            profiles = st.multiselect(
                "Categoria do usuário (opcional)",
                fetch_distinct_values("user_profile"),
                key="profiles",
                placeholder="Selecione categorias"
            )

        b1, b2, b3 = st.columns([1.2, 1.2, 1.6], vertical_alignment="bottom")

        with b1:
            limit = st.selectbox(
                "Resultados por página",
                [100, 250, 500, 1000],
                index=1,
                key="limit"
            )

        with b2:
            # botão de limpar filtros (opcional)
            if st.button("Limpar filtros", use_container_width=True):
                for k in ["event_labels", "accesses", "profiles", "search"]:
                    if k in st.session_state:
                        del st.session_state[k]
                st.session_state.page = 1
                st.rerun()

        with b3:
            st.caption("Dica: use os filtros avançados para refinar (ex.: só Moradores, só Prestadores, etc.).")


start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)

label_to_code = {o["label"]: o["code"] for o in event_options}
event_types = [label_to_code[lbl] for lbl in selected_event_labels] if selected_event_labels else []

st.session_state["shared_filters"].setdefault("relatorios", {})
st.session_state["shared_filters"]["relatorios"].update({
    "start_dt": start_dt,
    "end_dt": end_dt,
    "event_types": event_types,
    "accesses": accesses,
    "profiles": profiles,
    "search": search,
    "limit": int(limit),
})

# Se quiser atualizar sempre, basta forçar run=True.
# Aqui a gente respeita o botão pra ficar estilo Kiper.
if st.session_state.last_filter_key is None:
    run = True  # primeira carga

start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)

# label -> code
label_to_code = {o["label"]: o["code"] for o in event_options}
event_types = [label_to_code[lbl] for lbl in selected_event_labels] if selected_event_labels else []

# Se mudou filtro, reseta página
filter_key = (
    start_dt, end_dt,
    tuple(event_types),
    tuple(accesses),
    tuple(profiles),
    search,
    int(limit),
)
if st.session_state.last_filter_key != filter_key:
    st.session_state.page = 1
    st.session_state.last_filter_key = filter_key

# Só executa query quando clicar "Gerar relatório" (ou primeira carga)
if not run:
    st.info("Ajuste os filtros acima e clique em **Gerar relatório**.")
    st.stop()

# ----------------------------
# B) WHERE + params
# ----------------------------
where = ["event_timestamp between %(start)s and %(end)s"]
params = {"start": start_dt, "end": end_dt, "limit": limit}

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

# ----------------------------
# C) COUNT total (para paginação)
# ----------------------------
count_sql = f"""
select count(*) as total
from public.events
where {' and '.join(where)};
"""
total = fetch_df(count_sql, params)[0]["total"]
pages = max(1, math.ceil(total / limit))

if st.session_state.page > pages:
    st.session_state.page = pages

# ----------------------------
# D) Query principal (LIMIT/OFFSET)
# ----------------------------
params["offset"] = (st.session_state.page - 1) * limit

sql = f"""
select
    event_timestamp,

    concat_ws(' - ',
    event_type_code::text,
    event_description
    ) || chr(10) || access_name as descricao,

    user_name,
    user_profile,

    unit_group,
    unit,

    treatment
from public.events
where {' and '.join(where)}
order by event_timestamp desc, event_id desc
limit %(limit)s
offset %(offset)s;
"""

df = pd.DataFrame(fetch_df(sql, params))

# ----------------------------
# E) Render tabela
# ----------------------------
if total == 0:
    st.info("Nenhum evento encontrado para os filtros.")
else:
    render_kiper_table(df)

    # ----------------------------
    # F) Paginação embaixo da tabela
    # ----------------------------
    st.divider()
    p1, p2, p3 = st.columns([1, 2, 1])

    with p1:
        if st.button("⬅️ Anterior", use_container_width=True, disabled=(st.session_state.page <= 1)):
            st.session_state.page -= 1
            st.rerun()

    with p2:
        st.markdown(
            f"<div style='text-align:center;'>Página <b>{st.session_state.page}</b> de <b>{pages}</b> • Total: <b>{total:,}</b></div>",
            unsafe_allow_html=True
        )

    with p3:
        if st.button("Próxima ➡️", use_container_width=True, disabled=(st.session_state.page >= pages)):
            st.session_state.page += 1
            st.rerun()

    st.caption(f"Mostrando {len(df):,} de {total:,} registros (página {st.session_state.page}/{pages})")