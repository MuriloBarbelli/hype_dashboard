import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import numpy as np

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

def build_where_with_doors(where_base: str, params_base: dict, doors: list[str]):
    where_ = where_base
    params_ = dict(params_base)

    if doors:
        where_ += " and door_access_name = any(%(doors)s)"
        params_["doors"] = doors

    return where_, params_

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
    "Esta página analisa **quanto tempo cada porta permanece aberta** e destaca "
    "**casos extremos** (ex.: horas ou dias aberta), além de excedências configuráveis "
    "(Alerta / Risco / Retenção). "
    "O objetivo é transformar logs em decisões: identificar portas problemáticas, "
    "avaliar intervenções (sirene, orientação) e apoiar auditorias."
)

# ============================================================
# Filtros
# ============================================================

with st.container(border=True):
    # mantém o all_doors porque vamos usar nas abas
    all_doors = fetch_distinct_values("access_name")

    CRITICAL_MATCH = [
        "Portão Pedestre Externo",
        "Portão Pedestre Interno",
        "Hall Residencial",
        "Hall NR",
    ]

    doors_critical = [d for d in all_doors if any(k in (d or "") for k in CRITICAL_MATCH)]
    doors_other = [d for d in all_doors if d not in set(doors_critical)]

    col_start, col_end, col_btn = st.columns([1.6, 1.6, 1.0], vertical_alignment="bottom")

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
            use_hold = st.checkbox("Usar Retenção", value=True)
            th_hold_min = st.number_input("Retenção (min)", min_value=1, max_value=1440, value=10, step=1, disabled=not use_hold)
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

# ==============================
# Tetos efetivos das faixas
# (evita faixas impossíveis quando thresholds são desligados)
# ==============================
BIG_S = 10**9  # "infinito" em segundos

# teto do alerta:
# - se Risco está ligado: alerta vai até Risco
# - senão, se Retenção está ligada: alerta vai até Retenção
# - senão: alerta vai até "infinito"
th_alert_upper = (params["th_risk"] if params.get("th_risk", 0) > 0
                  else (params["th_hold"] if params.get("th_hold", 0) > 0 else BIG_S))

# teto do risco:
# - se Retenção está ligada: risco vai até Retenção
# - senão: risco vai até "infinito"
th_risk_upper = (params["th_hold"] if params.get("th_hold", 0) > 0 else BIG_S)

params["th_alert_upper"] = int(th_alert_upper)
params["th_risk_upper"] = int(th_risk_upper)

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

st.subheader("Resumo do período")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Aberturas (no período)", f"{total_aberturas:,}")
c2.metric("Pareadas (com fechamento)", f"{total_pareadas:,}")
c3.metric("Sem fechamento pareado (log)", f"{total_sem_close:,} ({fmt_pct(total_sem_close, total_aberturas)})")
c4.metric("Maior tempo aberto", fmt_seconds(max_seconds_open))

cols = st.columns(4)
cols[0].metric(f"> Retenção ({int(th_hold/60)}m)", f"{n_hold:,} ({fmt_pct(n_hold, total_pareadas)})")
cols[1].metric("> 30 min", f"{n_30m:,} ({fmt_pct(n_30m, total_pareadas)})")
cols[2].metric(f">= {th_1h//3600}h", f"{n_1h:,} ({fmt_pct(n_1h, total_pareadas)})")

st.divider()

def render_tab(tab_label: str, doors_group: list[str], key_prefix: str):
    st.markdown(f"#### {tab_label}")

    selected = st.multiselect(
        "Filtrar portas (opcional)",
        options=doors_group,
        default=[],
        key=f"{key_prefix}_doors",
        placeholder="Selecione uma ou mais portas"
    )

    doors_in_scope = selected if selected else doors_group
    where_tab, params_tab = build_where_with_doors(where_sql, params, doors_in_scope)

    # ============================================================
    # Distribuição por porta (empilhado absoluto)
    # ============================================================

    dist_sql = f"""
    select
      door_access_name,
      count(*) filter (where has_close = true)::bigint as total_ok,

      count(*) filter (
        where has_close = true
          and seconds_open < %(th_warn)s
      )::bigint as n_normal,

      count(*) filter (
        where has_close = true
          and seconds_open >= %(th_warn)s
          and seconds_open < %(th_alert_upper)s
      )::bigint as n_alerta,

      count(*) filter (
        where has_close = true
          and seconds_open >= %(th_risk)s
          and seconds_open < %(th_risk_upper)s
      )::bigint as n_risco,

      count(*) filter (
        where has_close = true
          and seconds_open >= %(th_hold)s
      )::bigint as n_retencao

    from public.mv_passages_v6
    where {where_tab}
    group by 1
    order by total_ok desc;
    """
    df_dist = q_df(dist_sql, params_tab)

    if df_dist.empty:
        st.info("Sem dados suficientes para este recorte.")
    else:
        df_dist = df_dist.sort_values("total_ok", ascending=False)

        # percentuais para texto/hover
        for col in ["n_normal", "n_alerta", "n_risco", "n_retencao"]:
            df_dist[f"pct_{col}"] = (df_dist[col] / df_dist["total_ok"] * 100).fillna(0)

            # Faixas (sem sobreposição)
            df_dist["n_retencao_band"] = df_dist["n_retencao"]
            df_dist["n_risco_band"] = (df_dist["n_risco"] - df_dist["n_retencao"]).clip(lower=0)
            df_dist["n_alerta_band"] = (df_dist["n_alerta"] - df_dist["n_risco"]).clip(lower=0)

            df_dist["n_anormais_total"] = (
                df_dist["n_alerta_band"] + df_dist["n_risco_band"] + df_dist["n_retencao_band"]
            )

        st.markdown("#### Aberturas totais (uso)")
        fig_total = go.Figure()
        fig_total.add_bar(
            x=df_dist["door_access_name"],
            y=df_dist["total_ok"],
            marker_color="#2E7D32",  # verde
            name="Total",
            hovertemplate="%{x}<br>Total: %{y:,.0f}<extra></extra>",
        )
        fig_total.update_layout(height=420, margin=dict(l=20, r=20, t=10, b=20))
        fig_total.update_xaxes(tickangle=-35)
        fig_total = apply_plot_theme(fig_total, x_title=None, y_title="Aberturas (pareadas)")
        st.plotly_chart(fig_total, use_container_width=True)

    st.divider()

    time_sql = f"""
    select
    door_access_name,

    -- ALERTA
    count(*) filter (
        where has_close = true
        and seconds_open >= %(th_warn)s
        and seconds_open <  %(th_alert_upper)s
    )::bigint as alert_n,
    sum(seconds_open) filter (
        where has_close = true
        and seconds_open >= %(th_warn)s
        and seconds_open <  %(th_alert_upper)s
    )::bigint as alert_seconds,

    -- RISCO
    count(*) filter (
        where has_close = true
        and seconds_open >= %(th_alert_upper)s
        and seconds_open <  %(th_risk_upper)s
    )::bigint as risk_n,
    
    sum(seconds_open) filter (
        where has_close = true
        and seconds_open >= %(th_alert_upper)s
        and seconds_open <  %(th_risk_upper)s
    )::bigint as risk_seconds,

    -- RETENÇÃO
    count(*) filter (
        where has_close = true
        and seconds_open >= %(th_risk_upper)s
    )::bigint as hold_n,
    sum(seconds_open) filter (
        where has_close = true
        and seconds_open >= %(th_risk_upper)s
    )::bigint as hold_seconds,

    max(seconds_open) filter (
    where has_close = true
        and seconds_open >= %(th_warn)s
        and seconds_open <  %(th_risk)s
    )::bigint as alert_max_seconds,

    max(seconds_open) filter (
    where has_close = true
        and seconds_open >= %(th_risk)s
        and seconds_open <  %(th_hold)s
    )::bigint as risk_max_seconds,

    max(seconds_open) filter (
    where has_close = true
        and seconds_open >= %(th_hold)s
    )::bigint as hold_max_seconds

    from public.mv_passages_v6
    where {where_tab}
    group by 1;
    """
    df_time = q_df(time_sql, params_tab)

    def fmt_duration(seconds: float | int | None) -> str:
        if seconds is None or pd.isna(seconds):
            return "—"
        try:
            s = int(round(float(seconds)))
        except Exception:
            return "—"

        if s < 60:
            return f"{s}s"
        if s < 3600:
            m = s // 60
            ss = s % 60
            return f"{m}m {ss:02d}s" if ss else f"{m}m"
        h = s // 3600
        rem = s % 3600
        m = rem // 60
        return f"{h}h {m:02d}m" if m else f"{h}h"

    if not df_time.empty:
        for c in ["alert_n", "risk_n", "hold_n", "alert_seconds", "risk_seconds", "hold_seconds"]:
            df_time[c] = df_time[c].fillna(0)

        # garantir colunas de max existam e não quebrem
        for c in ["alert_max_seconds", "risk_max_seconds", "hold_max_seconds"]:
            if c not in df_time.columns:
                df_time[c] = 0
            df_time[c] = df_time[c].fillna(0)

        # médias em segundos (para formatar como tempo)
        df_time["alert_mean_seconds"] = (df_time["alert_seconds"] / df_time["alert_n"]).where(df_time["alert_n"] > 0, 0)
        df_time["risk_mean_seconds"]  = (df_time["risk_seconds"]  / df_time["risk_n"]).where(df_time["risk_n"] > 0, 0)
        df_time["hold_mean_seconds"]  = (df_time["hold_seconds"]  / df_time["hold_n"]).where(df_time["hold_n"] > 0, 0)

        # strings prontas para o hover
        df_time["alert_total_str"] = df_time["alert_seconds"].apply(fmt_duration)
        df_time["risk_total_str"]  = df_time["risk_seconds"].apply(fmt_duration)
        df_time["hold_total_str"]  = df_time["hold_seconds"].apply(fmt_duration)

        df_time["alert_mean_str"] = df_time["alert_mean_seconds"].apply(fmt_duration)
        df_time["risk_mean_str"]  = df_time["risk_mean_seconds"].apply(fmt_duration)
        df_time["hold_mean_str"]  = df_time["hold_mean_seconds"].apply(fmt_duration)

        df_time["alert_max_str"] = df_time["alert_max_seconds"].apply(fmt_duration)
        df_time["risk_max_str"]  = df_time["risk_max_seconds"].apply(fmt_duration)
        df_time["hold_max_str"]  = df_time["hold_max_seconds"].apply(fmt_duration)

        # minutos totais
        df_time["alert_min"] = df_time["alert_seconds"] / 60.0
        df_time["risk_min"]  = df_time["risk_seconds"] / 60.0
        df_time["hold_min"]  = df_time["hold_seconds"] / 60.0

        # duração média por ocorrência (típico)
        df_time["alert_mean_min"] = (df_time["alert_min"] / df_time["alert_n"]).where(df_time["alert_n"] > 0, 0.0)
        df_time["risk_mean_min"]  = (df_time["risk_min"]  / df_time["risk_n"]).where(df_time["risk_n"] > 0, 0.0)
        df_time["hold_mean_min"]  = (df_time["hold_min"]  / df_time["hold_n"]).where(df_time["hold_n"] > 0, 0.0)

        # Opção 2B: score log no valor (tempo total)
        df_time["alert_logscore"] = np.log10(1 + df_time["alert_min"])
        df_time["risk_logscore"]  = np.log10(1 + df_time["risk_min"])
        df_time["hold_logscore"]  = np.log10(1 + df_time["hold_min"])

        # Opção 3: score híbrido (freq × duração típica comprimida)
        # score = n * ln(1 + média_min)
        df_time["alert_hybrid"] = df_time["alert_n"] * np.log1p(df_time["alert_mean_min"])
        df_time["risk_hybrid"]  = df_time["risk_n"]  * np.log1p(df_time["risk_mean_min"])
        df_time["hold_hybrid"]  = df_time["hold_n"]  * np.log1p(df_time["hold_mean_min"])                                                                  

    st.markdown("#### Impacto de anomalias por categoria")
    st.caption(
        "Fator de risco*: maior valor indica maior impacto acumulado da anomalia."
    )
    st.caption(
        "*Cálculo baseado no tempo total das ocorrências com compressão logarítmica "
        "para evitar distorções por eventos extremos."
    )
    if df_time.empty:
        st.info("Sem dados de tempo para este recorte.")
    else:
        # ordena por impacto (retenção + risco) primeiro
        df_time["impact_min"] = df_time["hold_min"] + df_time["risk_min"]
        df_time = df_time.sort_values("impact_min", ascending=False)

        # Score log10(1 + minutos)
        y_alert = df_time["alert_logscore"]
        y_risk  = df_time["risk_logscore"]
        y_hold  = df_time["hold_logscore"]

        y_title = "Impacto (fator de risco*)"
        hover_suffix = "score"

        def fmt_duration(seconds: float | int | None) -> str:
            if seconds is None:
                return "—"
            try:
                s = int(round(float(seconds)))
            except Exception:
                return "—"

            if s < 60:
                return f"{s}s"
            if s < 3600:
                m = s // 60
                ss = s % 60
                return f"{m}m {ss:02d}s" if ss else f"{m}m"
            h = s // 3600
            rem = s % 3600
            m = rem // 60
            return f"{h}h {m:02d}m" if m else f"{h}h"

        fig_abn = go.Figure()

        if use_warn:
            fig_abn.add_bar(
                x=df_time["door_access_name"],
                y=df_time["alert_logscore"],
                name="Alerta",
                marker_color="#FBC02D",
                customdata=list(zip(
                    df_time["alert_total_str"],
                    df_time["alert_n"],
                    df_time["alert_mean_str"],
                    df_time["alert_max_str"],
                )),
                hovertemplate=(
                    f"Alerta (≥ {int(th_warn)}s)<br>"
                    "Fator de risco: %{y:.2f}<br>"
                    "Tempo total: %{customdata[0]}<br>"
                    "Ocorrências: %{customdata[1]:,.0f}<br>"
                    "Máximo: %{customdata[3]}<br>"
                    "Média: %{customdata[2]}"
                    "<extra></extra>"
                ),
            )

        risk_min_label = int(th_risk / 60)

        if use_risk:
            fig_abn.add_bar(
                x=df_time["door_access_name"],
                y=df_time["risk_logscore"],
                name="Risco",
                marker_color="#FB8C00",
                customdata=list(zip(
                    df_time["risk_total_str"],
                    df_time["risk_n"],
                    df_time["risk_mean_str"],
                    df_time["risk_max_str"],
                )),
                hovertemplate=(
                    f"Risco (≥ {risk_min_label}min)<br>"
                    "Fator de risco: %{y:.2f}<br>"
                    "Tempo total: %{customdata[0]}<br>"
                    "Ocorrências: %{customdata[1]:,.0f}<br>"
                    "Máximo: %{customdata[3]}<br>"
                    "Média: %{customdata[2]}"
                    "<extra></extra>"
                ),
            )

        hold_min_label = int(th_hold / 60)

        if use_hold:
            fig_abn.add_bar(
                x=df_time["door_access_name"],
                y=df_time["hold_logscore"],
                name="Retenção",
                marker_color="#E53935",
                customdata=list(zip(
                    df_time["hold_total_str"],
                    df_time["hold_n"],
                    df_time["hold_mean_str"],
                    df_time["hold_max_str"],
                )),
                hovertemplate=(
                    f"Retenção (≥ {hold_min_label}min)<br>"
                    "Fator de risco: %{y:.2f}<br>"
                    "Tempo total: %{customdata[0]}<br>"
                    "Ocorrências: %{customdata[1]:,.0f}<br>"
                    "Máximo: %{customdata[3]}<br>"
                    "Média: %{customdata[2]}"
                    "<extra></extra>"
                ),
            )

        fig_abn.update_layout(
            barmode="group",
            bargap=0.25,
            height=420,
            margin=dict(l=20, r=20, t=10, b=20),
        )
        fig_abn.update_xaxes(tickangle=-35)
        fig_abn = apply_plot_theme(fig_abn, x_title=None, y_title=y_title)
        st.plotly_chart(fig_abn, use_container_width=True)



st.subheader("Volume de aberturas por porta (com destaque por categoria)")
st.caption(
    "A altura da barra representa o total de aberturas pareadas no período. "
    "As cores mostram quantas aberturas caem em Normal / Alerta / Risco / Retenção."
)

col1, col2 = st.columns(2, gap="large")

with col1:
    render_tab("Portas críticas (acessos do condomínio)", doors_critical, "crit")

with col2:
    render_tab("Outras portas (áreas comuns / apoio)", doors_other, "other")

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
        (f">= Retenção ({th_hold_min} min)", th_hold),
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
        "Esta seção apresenta **aberturas sem fechamento pareado**. Isso pode indicar falha de sensor/export ou ruído de evento. "
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
