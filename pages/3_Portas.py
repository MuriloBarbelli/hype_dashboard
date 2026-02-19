import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

from ui.sidebar import render_sidebar_menu
from src.db import fetch_df, fetch_distinct_values
from src.helpers import (
    init_state,
    apply_shared_period_to_widgets,
    sync_shared_period_from_widgets,
    PERIOD_KEYS,
    ensure_apply_state,
    apply_filters_now,
    sync_period_and_mark_dirty,
    apply_plot_theme,
)

st.set_page_config(page_title="Portas • Hype", layout="wide")

init_state()
ensure_apply_state()
apply_shared_period_to_widgets()

st.session_state["current_page"] = "Portas"
render_sidebar_menu()

# ============================================================
# Helpers
# ============================================================

@st.cache_data(ttl=120, show_spinner=False)
def q_df(sql: str, params: dict):
    return pd.DataFrame(fetch_df(sql, params))

def fmt_seconds(s):
    if s is None or pd.isna(s):
        return "—"
    s = float(s)
    if s < 60:
        return f"{s:.1f}s"
    m = int(s // 60)
    r = s - m * 60
    if m < 60:
        return f"{m}m {r:.0f}s"
    h = m // 60
    mm = m - h * 60
    return f"{h}h {mm:02d}m"

def fmt_pct(n, d):
    if not d:
        return "0.0%"
    return f"{(n / d) * 100:.1f}%"

# ============================================================
# Header
# ============================================================

st.title("Portas")
st.caption(
    "Aqui eu meço **quanto tempo cada porta permanece aberta** e destaco **casos extremos** (ex.: horas/dias aberta), "
    "além de excedências configuráveis (Alerta / Risco / Calço). "
    "O objetivo é transformar logs em decisões: identificar portas problemáticas, validar intervenções (sirene, orientação) "
    "e apoiar auditoria de ocorrências."
)

# ============================================================
# Filtros
# ============================================================

with st.container(border=True):
    col_doors, col_start, col_end, col_btn = st.columns([1.8, 1.5, 1.5, 1.0], vertical_alignment="bottom")

    with col_doors:
        all_doors = fetch_distinct_values("access_name")
        selected_doors = st.multiselect(
            "Portas (opcional)",
            all_doors,
            key="doors_selected",
            placeholder="Selecione uma ou mais portas"
        )

    with col_start:
        c_sd, c_st = st.columns([1.1, 0.9])
        with c_sd:
            start_date = st.date_input("Período inicial", key=PERIOD_KEYS["date_start"], on_change=sync_period_and_mark_dirty)
        with c_st:
            start_time = st.time_input("Hora", key=PERIOD_KEYS["time_start"], on_change=sync_period_and_mark_dirty)

    with col_end:
        c_ed, c_et = st.columns([1.1, 0.9])
        with c_ed:
            end_date = st.date_input("Período final", key=PERIOD_KEYS["date_end"], on_change=sync_period_and_mark_dirty)
        with c_et:
            end_time = st.time_input("Hora", key=PERIOD_KEYS["time_end"], on_change=sync_period_and_mark_dirty)

    with col_btn:
        run_clicked = st.button("Gerar relatório", type="primary", use_container_width=True, key="doors_run")
        if run_clicked:
            sync_shared_period_from_widgets()
            apply_filters_now()

    with st.expander("Parâmetros de risco (thresholds)", expanded=True):
        st.caption("Ative apenas os thresholds que você quer analisar/filtrar.")
        c1, c2, c3 = st.columns(3)

        with c1:
            use_warn = st.checkbox("Usar Alerta", value=True)
            th_warn = st.number_input("Alerta (seg)", min_value=1, max_value=600, value=15, step=1, disabled=not use_warn)

        with c2:
            use_risk = st.checkbox("Usar Risco", value=True)
            th_risk_min = st.number_input("Risco (min)", min_value=1, max_value=600, value=1, step=1, disabled=not use_risk)
            th_risk = th_risk_min * 60


        with c3:
            use_hold = st.checkbox("Usar Calço", value=True)
            th_hold_min = st.number_input("Calço (min)", min_value=1, max_value=1440, value=10, step=1, disabled=not use_hold)
            th_hold = th_hold_min * 60

        th_30m = 30 * 60
        th_1h = 60 * 60


# Gate padrão do app
if st.session_state.get("applied_filters_hash") is None:
    st.info("Ajuste os filtros acima e clique em **Gerar relatório**.")
    st.stop()

if st.session_state.get("filters_dirty", True):
    st.info("Filtros alterados. Clique em **Gerar relatório** para atualizar.")
    st.stop()

start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)

# ============================================================
# WHERE / Params
# ============================================================

where = ["open_ts between %(start)s and %(end)s"]
params = {"start": start_dt, "end": end_dt}

if selected_doors:
    where.append("door_access_name = any(%(doors)s::text[])")
    params["doors"] = list(selected_doors)

# excedências
exceed = []
if use_warn:
    exceed.append("seconds_open > %(th_warn)s")
    params["th_warn"] = int(th_warn)
else:
    params["th_warn"] = 0

if use_risk:
    exceed.append("seconds_open > %(th_risk)s")
    params["th_risk"] = int(th_risk)
else:
    params["th_risk"] = 0

if use_hold:
    exceed.append("seconds_open >= %(th_hold)s")
    params["th_hold"] = int(th_hold)
else:
    params["th_hold"] = 0

params["th_30m"] = int(th_30m)
params["th_1h"] = int(th_1h)

where_sql = " and ".join(where)

# ============================================================
# KPIs (separando dados limpos x qualidade do log)
# ============================================================

kpi_sql = f"""
select
  count(*)::bigint as total_aberturas,
  count(*) filter (where has_close = true)::bigint as total_pareadas,
  count(*) filter (where has_close = false)::bigint as total_sem_close,

  max(seconds_open) filter (where has_close = true) as max_seconds_open,

  count(*) filter (where has_close = true and seconds_open >= %(th_hold)s)::bigint as n_hold,
  count(*) filter (where has_close = true and seconds_open >= %(th_30m)s)::bigint as n_30m,
  count(*) filter (where has_close = true and seconds_open >= %(th_1h)s)::bigint as n_1h
from public.mv_passages_v6
where {where_sql};
"""
k = (fetch_df(kpi_sql, params) or [{}])[0]

params["th_30m"] = th_30m
params["th_1h"] = th_1h

total_aberturas = int(k.get("total_aberturas") or 0)
total_pareadas = int(k.get("total_pareadas") or 0)
total_sem_close = int(k.get("total_sem_close") or 0)

max_seconds_open = k.get("max_seconds_open")

n_hold = int(k.get("n_hold") or 0)
n_30m = int(k.get("n_30m") or 0)
n_1h = int(k.get("n_1h") or 0)

if total_aberturas == 0:
    st.warning("Sem aberturas no período selecionado.")
    st.stop()

st.subheader("Resumo do período (foco em casos extremos)")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Aberturas (no período)", f"{total_aberturas:,}")
c2.metric("Pareadas (com fechamento)", f"{total_pareadas:,}")
c3.metric("Sem fechamento pareado (log)", f"{total_sem_close:,} ({fmt_pct(total_sem_close, total_aberturas)})")
c4.metric("Maior tempo aberto", fmt_seconds(max_seconds_open))

cols = st.columns(4)
cols[0].metric(f">> Calço ({int(th_hold/60)}m)", f"{n_hold:,} ({fmt_pct(n_hold, total_pareadas)})")
cols[1].metric(">> 30 min", f"{n_30m:,} ({fmt_pct(n_30m, total_pareadas)})")
cols[2].metric(f">= {th_1h//3600}h", f"{n_1h:,} ({fmt_pct(n_1h, total_pareadas)})")

st.divider()

# ============================================================
# Ranking simples por porta (somente dados limpos)
# ============================================================

rank_sql = f"""
select
  door_access_name,
  count(*) filter (where has_close = true)::bigint as passagens_ok,
  count(*) filter (where has_close = false)::bigint as passagens_sem_close,

  max(seconds_open) filter (where has_close = true) as max_s,

  count(*) filter (where has_close = true and seconds_open >= %(th_hold)s)::bigint as n_hold,
  count(*) filter (where has_close = true and seconds_open >= %(th_30m)s)::bigint as n_30m,
  count(*) filter (where has_close = true and seconds_open >= %(th_1h)s)::bigint as n_1h
from public.mv_passages_v6
where {where_sql}
group by 1
order by max_s desc nulls last, passagens_ok desc;
"""
df_rank = q_df(rank_sql, params)

st.subheader("Ranking de portas (simples e acionável)")
st.caption("Aqui eu considero **somente passagens pareadas (has_close = true)** para não contaminar os números.")

df_show = df_rank.copy()
df_show["Maior abertura"] = df_show["max_s"].apply(fmt_seconds)

# percentuais sobre passagens_ok (dados limpos)
df_show["% >= Calço"] = df_show.apply(lambda r: fmt_pct(int(r["n_hold"]), int(r["passagens_ok"])), axis=1)
df_show["% >= 30 min"] = df_show.apply(lambda r: fmt_pct(int(r["n_30m"]), int(r["passagens_ok"])), axis=1)
df_show["% >= 1h"] = df_show.apply(lambda r: fmt_pct(int(r["n_1h"]), int(r["passagens_ok"])), axis=1)

# qualidade do log (separado, mas visível)
df_show["Sem fechamento (log)"] = df_show["passagens_sem_close"].astype(int)

df_show = df_show.rename(columns={
    "door_access_name": "Porta",
    "passagens_ok": "Passagens (ok)",
})

st.dataframe(
    df_show[["Porta", "Passagens (ok)", "Maior abertura", "% >= Calço", "% >= 30 min", "% >= 1h", "Sem fechamento (log)"]],

    hide_index=True
)

# ============================================================
# Gráfico: % >= Calço (ou 5m/1h) por porta
# ============================================================

st.subheader("Excedência por porta (visão rápida)")

metric = st.selectbox(
    "Métrica do gráfico",
    options=[
        ("% >= Calço", "pct_hold"),
        ("% >= 30 min", "pct_30m"),
        ("% >= 1h", "pct_1h"),
        ("Sem fechamento (log) %", "pct_sem_close"),
    ],
    index=0
)

df_plot = df_rank.copy()
df_plot["pct_hold"] = (df_plot["n_hold"] / df_plot["passagens_ok"] * 100).fillna(0)
df_plot["pct_30m"] = (df_plot["n_30m"] / df_plot["passagens_ok"] * 100).fillna(0)
df_plot["pct_1h"] = (df_plot["n_1h"] / df_plot["passagens_ok"] * 100).fillna(0)

df_plot["total"] = df_plot["passagens_ok"] + df_plot["passagens_sem_close"]
df_plot["pct_sem_close"] = (df_plot["passagens_sem_close"] / df_plot["total"] * 100).fillna(0)

label, key = metric
df_plot = df_plot.sort_values(key, ascending=False)

fig = go.Figure()
fig.add_bar(
    y=df_plot["door_access_name"],
    x=df_plot[key],
    orientation="h",
    hovertemplate="%{y}<br>%{x:.1f}%<extra></extra>",
)
fig.update_layout(
    height=max(420, 28 * len(df_plot) + 140),
    margin=dict(l=20, r=20, t=20, b=20),
    showlegend=False,
)
fig = apply_plot_theme(fig, x_title=label, y_title=None)
fig.update_yaxes(autorange="reversed")
st.plotly_chart(fig, use_container_width=True)

st.divider()

# ============================================================
# Top maiores aberturas (dados limpos)
# ============================================================

st.subheader("Casos extremos (Top maiores aberturas)")
st.caption("Lista para auditoria rápida. Aqui é onde aparecem as portas abertas por horas/dias.")

min_filter = st.selectbox(
    "Filtrar casos por duração mínima",
    [
        ("Sem filtro", 0),
        (f">= Calço ({th_hold_min} min)", th_hold),
        (">= 30 min", th_30m),
        (">= 1 hora", th_1h),
    ],
    index=1
)

top_where = where_sql + " and has_close = true"
if min_filter[1] > 0:
    top_where += " and seconds_open >= %(min_s)s"
    top_params = dict(params)
    top_params["min_s"] = int(min_filter[1])
else:
    top_params = dict(params)

top_sql = f"""
select
  door_access_name,
  open_ts,
  close_ts,
  seconds_open,
  open_event_id
from public.mv_passages_v6
where {top_where}
order by seconds_open desc nulls last
limit 200;
"""
df_top = q_df(top_sql, top_params)
if df_top.empty:
    st.info("Nenhum caso extremo encontrado com esse filtro.")
else:
    df_top["Tempo aberta"] = df_top["seconds_open"].apply(fmt_seconds)
    df_top = df_top.rename(columns={
        "door_access_name": "Porta",
        "open_ts": "Abriu em",
        "close_ts": "Fechou em",
        "open_event_id": "Grupo (open_event_id)",
    })
    st.dataframe(df_top[["Porta", "Tempo aberta", "Abriu em", "Fechou em", "Grupo (open_event_id)"]], hide_index=True)

# ============================================================
# Qualidade do log (não contamina estatísticas)
# ============================================================

with st.expander("Qualidade do log (diagnóstico — não entra nas estatísticas acima)", expanded=False):
    st.caption(
        "Aqui eu mostro **aberturas sem fechamento pareado**. Isso pode indicar falha de sensor/export ou ruído de evento. "
        "As estatísticas de segurança acima usam somente `has_close = true`."
    )

    qlog_sql = f"""
    select
      door_access_name,
      count(*)::bigint as aberturas_no_periodo,
      count(*) filter (where has_close = false)::bigint as sem_close,
      round(100.0 * count(*) filter (where has_close = false) / nullif(count(*),0), 2) as pct_sem_close
    from public.mv_passages_v6
    where {where_sql}
    group by 1
    order by sem_close desc, pct_sem_close desc;
    """
    df_log = q_df(qlog_sql, params)

    if df_log.empty:
        st.info("Sem dados para diagnóstico.")
    else:
        df_log = df_log.rename(columns={
            "door_access_name": "Porta",
            "aberturas_no_periodo": "Aberturas",
            "sem_close": "Sem fechamento",
            "pct_sem_close": "% sem fechamento"
        })
        st.dataframe(df_log, hide_index=True)

    st.divider()
    st.caption("Exemplos recentes (sem fechamento pareado) para investigação:")
    ex_sql = f"""
    select
      door_access_name,
      open_ts,
      next_open_ts,
      open_event_id
    from public.mv_passages_v6
    where {where_sql}
      and has_close = false
    order by open_ts desc
    limit 200;
    """
    df_ex = q_df(ex_sql, params)
    if df_ex.empty:
        st.info("Nenhum caso sem fechamento pareado no recorte.")
    else:
        df_ex = df_ex.rename(columns={
            "door_access_name": "Porta",
            "open_ts": "Abriu em",
            "next_open_ts": "Próxima abertura (se houver)",
            "open_event_id": "Grupo (open_event_id)",
        })
        st.dataframe(df_ex, hide_index=True)
