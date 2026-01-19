import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, time

from src.db import fetch_df, fetch_distinct_values
from ui.sidebar import render_sidebar_menu
from src.helpers import init_state, apply_shared_period_to_widgets, sync_shared_period_from_widgets, PERIOD_KEYS
from src.helpers import ensure_apply_state, apply_filters_now
from src.helpers import KIPER_PROFILE_COLORS, get_profile_color, canonical_profile, apply_plot_theme

def get_events_source() -> str:
    return "public.events" if st.session_state.get("data_mode") == "real" else "public.vw_events_anon"

@st.cache_data(ttl=120, show_spinner=False)
def q_one(sql: str, params: dict):
    rows = fetch_df(sql, params)
    return rows[0] if rows else None

@st.cache_data(ttl=120, show_spinner=False)
def q_df(sql: str, params: dict):
    return pd.DataFrame(fetch_df(sql, params))

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
    from public.vw_events_anon
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
event_types = tuple(label_to_code[lbl] for lbl in selected_event_labels) if selected_event_labels else tuple()


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
    params["accesses"] = tuple(accesses)

if profiles:
    where.append("user_profile = any(%(profiles)s)")
    params["profiles"] = tuple(profiles)

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
    where_p.append(f"{col_access} = any(%(accesses)s::text[])")
    params_p["accesses"] = list(accesses)

if profiles and col_profile:
    where_p.append(f"{col_profile} = any(%(profiles)s::text[])")
    params_p["profiles"] = list(profiles)

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

kpi_sql = f"""
with base as (
  select
    date({col_ts_start}) as dia,
    nullif(lower(trim({col_search_1})), '') as user_name,
    {col_profile} as user_profile
  from public.mv_passage_classification_v5
  where {where_p_sql}
),
pessoas as (
  select
    count(distinct user_name) as pessoas_unicas,
    count(distinct case
      when user_profile in ('Morador', 'Morador/Proprietário', 'Síndico/Morador') then user_name
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
kpi = q_one(kpi_sql, params_p) or {}

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

def kpi_abs_pct(label: str, abs_value: int, pct_value: float):
    st.markdown(
        f"""
        <div style="padding: 0.25rem 0;">
          <div style="font-size: 0.85rem; color: rgba(49,51,63,0.6);">{label}</div>
          <div style="font-size: 2.2rem; font-weight: 600; line-height: 1.2;">
            {abs_value:,}
            <span style="font-size: 1rem; font-weight: 500; color: rgba(49,51,63,0.6);">
              ({pct_value:.1f}%)
            </span>
          </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with c4:
    kpi_abs_pct("Moradores", pessoas_moradoras, pct_moradores)

with c5:
    kpi_abs_pct("Não-moradores", pessoas_nao_moradoras, pct_nao_moradores)



st.divider()

st.subheader("Fluxo de Pessoas")

day_sql = f"""
select
  date_trunc('day', open_ts) as dia,
  count(*)::bigint as passagens,
  count(distinct nullif(lower(trim(user_name)), ''))::bigint as pessoas_unicas
from public.vw_passage_classification_v5
where {where_p_sql}
group by 1
order by 1;
"""

df_day = pd.DataFrame(fetch_df(day_sql, params_p))

if df_day.empty:
    st.info("Sem dados no período selecionado.")
else:
    df_day["dia"] = pd.to_datetime(df_day["dia"])

    col1, col2 = st.columns(2)

    # -----------------------------
    # Gráfico 1 — Passagens por dia (BARRAS)
    # -----------------------------
    with col1:
        fig_pass = go.Figure()

        fig_pass.add_bar(
            x=df_day["dia"],
            y=df_day["passagens"],
            marker=dict(
                color=df_day["passagens"],
                colorscale="Blues",
                line=dict(width=0)
            ),
            hovertemplate="Dia: %{x|%d/%m}<br>Passagens: %{y}<extra></extra>",
        )

        fig_pass.update_layout(
            title="Passagens por dia",
            height=320,
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis_title=None,
            yaxis_title="Passagens",
            template="simple_white",
            showlegend=False
        )
        fig_pass = apply_plot_theme(fig_pass, x_title="Passagens", y_title=None)
        st.plotly_chart(fig_pass, use_container_width=True)

    # -----------------------------
    # Gráfico 2 — Pessoas únicas por dia (LINHA)
    # -----------------------------
    with col2:
        fig_people = go.Figure()

        fig_people.add_trace(
            go.Scatter(
                x=df_day["dia"],
                y=df_day["pessoas_unicas"],
                mode="lines+markers",
                line=dict(width=3, color="#2E7D32"),
                marker=dict(size=6),
                hovertemplate="Dia: %{x|%d/%m}<br>Pessoas únicas: %{y}<extra></extra>",
            )
        )

        fig_people.update_layout(
            title="Pessoas únicas por dia",
            height=320,
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis_title=None,
            yaxis_title="Pessoas",
            template="simple_white",
            showlegend=False
        )
        fig_people = apply_plot_theme(fig_people, x_title="Pessoas únicas por dia", y_title=None)
        st.plotly_chart(fig_people, use_container_width=True)

st.divider()

st.subheader("Horários de pico (movimento real)")

# Perfis que você NÃO quer considerar para pico (ajuste conforme seus valores reais)
EXCLUIR_PARA_PICO = {
    "Funcionário",
    "Zelador",
    "Porteiro Monitoramento",
}

# Perfis considerados "do prédio"
PERFIS_MORADOR = {
    "Morador",
    "Morador/Proprietário",
    "Síndico/Morador",
}

# Monta where específico do bloco 3 (reaproveita o where_p_sql mas adiciona exclusão)
where_peak = [where_p_sql]

# Só aplica a exclusão se a coluna existir (sua view tem user_profile, então ok)
where_peak.append("user_profile is not null")
where_peak.append("user_profile <> all(%(excluir_perfis)s::text[])")

params_peak = dict(params_p)
params_peak["excluir_perfis"] = list(EXCLUIR_PARA_PICO)

peak_sql = f"""
select
  extract(hour from open_ts)::int as hora,
  sum(case when user_profile = any(%(perfis_morador)s::text[]) then 1 else 0 end)::bigint as moradores,
  sum(case when user_profile <> any(%(perfis_morador)s::text[]) then 1 else 0 end)::bigint as nao_moradores
from public.vw_passage_classification_v5
where {' and '.join(where_peak)}
group by 1
order by 1;
"""

params_peak["perfis_morador"] = list(PERFIS_MORADOR)

df_peak = pd.DataFrame(fetch_df(peak_sql, params_peak))

check_sql = f"""
select
  count(*)::bigint as passagens_proprietario
from public.vw_passage_classification_v5
where {where_p_sql}
  and user_profile = 'Proprietário';
"""
n_prop = (q_one(check_sql, params_p) or {}).get("passagens_proprietario", 0) or 0
if n_prop > 0:
    st.warning(f"Atenção: encontrei {n_prop:,} passagens com perfil 'Proprietário' no período. (vale checar cadastro/regras)")


if df_peak.empty:
    st.info("Sem dados suficientes para montar o gráfico de pico com os filtros atuais.")
else:
    # Garante todas as horas 0..23 para o gráfico ficar estável/bonito
    all_hours = pd.DataFrame({"hora": list(range(24))})
    df_peak = all_hours.merge(df_peak, on="hora", how="left").fillna(0)

    fig_peak = go.Figure()

    fig_peak.add_bar(
        x=df_peak["hora"],
        y=df_peak["moradores"],
        name="Moradores",
        hovertemplate="Hora: %{x}h<br>Moradores: %{y}<extra></extra>",
    )
    fig_peak.add_bar(
        x=df_peak["hora"],
        y=df_peak["nao_moradores"],
        name="Não-moradores",
        hovertemplate="Hora: %{x}h<br>Não-moradores: %{y}<extra></extra>",
    )

    fig_peak.update_layout(
        barmode="stack",
        height=380,
        title=dict(
            text="Passagens por hora (excluindo funcionários fixos)",
            x=0,
            xanchor="left",
            font=dict(size=14, color="rgba(49,51,63,0.75)"),
        ),
        template="simple_white",
        xaxis=dict(title=None, tickmode="linear", dtick=1),
        yaxis=dict(title="Passagens"),
        margin=dict(l=20, r=20, t=45, b=30),
        legend=dict(
            orientation="v",
            yanchor="top",
            y=0.98,
            xanchor="right",
            x=0.98,
            bgcolor="rgba(255, 255, 255, 0.8)"
        ),
    )

    fig_peak = apply_plot_theme(fig_peak, x_title="Passagens", y_title=None)
    st.plotly_chart(fig_peak, use_container_width=True)

st.divider()

st.subheader("Uso do prédio (Residencial × Não-Residencial)")

uso_sql = f"""
select
  unit_group,
  count(*)::bigint as passagens
from public.vw_passage_classification_v5
where {where_p_sql}
group by 1;
"""

df_uso = pd.DataFrame(fetch_df(uso_sql, params_p))

if df_uso.empty:
    st.info("Sem dados suficientes para análise de uso do prédio.")
else:
    # Mapeamento explícito
    MAP_GRUPO = {
        "Bloco HYPE RES": "Residencial",
        "Bloco HYPE NR": "Não-Residencial",
    }

    df_uso["categoria"] = df_uso["unit_group"].map(MAP_GRUPO)
    df_main = df_uso[df_uso["categoria"].notna()].copy()

    total_main = df_main["passagens"].sum()
    total_all = df_uso["passagens"].sum()
    fora = total_all - total_main

    col1, col2 = st.columns([2, 1])

    # -----------------------------
    # Donut — RES x NR
    # -----------------------------
    with col1:
        fig_uso = go.Figure(
            data=[
                go.Pie(
                    labels=df_main["categoria"],
                    values=df_main["passagens"],
                    hole=0.55,
                    textinfo="label+percent",
                    hovertemplate="%{label}<br>Passagens: %{value:,}<extra></extra>",
                )
            ]
        )

        fig_uso.update_layout(
            height=320,
            template="simple_white",
            margin=dict(l=20, r=20, t=20, b=20),
            showlegend=False,
        )
        fig_uso = apply_plot_theme(fig_uso, x_title="Passagens", y_title=None)
        st.plotly_chart(fig_uso, use_container_width=True)

    # -----------------------------
    # Texto de apoio / alerta
    # -----------------------------
    with col2:
        st.markdown("**Resumo**")
        for _, r in df_main.iterrows():
            pct = (r["passagens"] / total_main * 100) if total_main else 0
            st.write(f"- **{r['categoria']}**: {r['passagens']:,} passagens ({pct:.1f}%)")

        if fora > 0:
            pct_fora = fora / total_all * 100 if total_all else 0
            st.warning(
                f"{fora:,} passagens ({pct_fora:.1f}%) não estão associadas a "
                f"Residencial ou NR (ADM ou sem vínculo)."
            )

st.divider()

st.subheader("Acessos mais utilizados (entrada por facial)")
st.caption("Este gráfico considera apenas passagens de ENTRADA via FACIAL.")

# 1) Query: total por acesso + perfil
acessos_sql = f"""
select
  door_access_name,
  coalesce(user_profile, 'Sem perfil') as user_profile,
  count(*)::bigint as passagens
from public.vw_passage_classification_v5
where {where_p_sql}
  and cause_code in (701, 708)
group by 1, 2;
"""

df_acc = pd.DataFrame(fetch_df(acessos_sql, params_p))
df_acc["user_profile"] = df_acc["user_profile"].apply(canonical_profile)

if df_acc.empty:
    st.info("Sem dados de acessos no período.")
else:
    # 2) Ordem dos acessos: total desc (campeão em cima)
    totals = (
        df_acc.groupby("door_access_name", as_index=False)["passagens"]
        .sum()
        .rename(columns={"passagens": "total"})
        .sort_values(["total", "door_access_name"], ascending=[False, True])
    )
    access_order = totals["door_access_name"].tolist()

    # Ordem estável dos perfis (opcional): melhora leitura
    profile_order = [p for p in KIPER_PROFILE_COLORS.keys() if p in df_acc["user_profile"].unique()]
    # inclui quaisquer perfis novos que apareçam no dado
    extras = [p for p in sorted(df_acc["user_profile"].unique()) if p not in profile_order]
    profile_order = profile_order + extras

    # 4) Pivot para empilhar barras
    df_pivot = (
        df_acc.pivot_table(
            index="door_access_name",
            columns="user_profile",
            values="passagens",
            aggfunc="sum",
            fill_value=0,
        )
        .reindex(access_order)              # ordena acessos
        .reindex(columns=profile_order, fill_value=0)  # ordena perfis
    )

    # 5) Plotly stacked horizontal bar
    import plotly.graph_objects as go

    fig = go.Figure()

    # total por acesso (para % no hover)
    total_by_access = df_pivot.sum(axis=1)

    for prof in df_pivot.columns:
        vals = df_pivot[prof].values
        if vals.sum() == 0:
            continue

        color = get_profile_color(prof, "#B0BEC5")

        # customdata: total do acesso (para calcular %)
        customdata = total_by_access.values

        fig.add_bar(
            y=df_pivot.index,
            x=vals,
            name=prof,
            orientation="h",
            marker=dict(color=color),
            customdata=customdata,
            hovertemplate=(
            "%{y}<br>"
            "<b>" + prof + "</b>: %{x:,}<br>"
            "Total do acesso: %{customdata:,}<extra></extra>"
            ),

        )

    # Ajuste de layout “bonito”
    n_barras = len(df_pivot.index)

    fig.update_layout(
        barmode="stack",
        template="simple_white",

        # Altura proporcional, sem exagerar
        height=max(420, 36 * n_barras + 120),

        margin=dict(l=20, r=20, t=20, b=20),

        # Eixo X
        xaxis=dict(
            title="Passagens",
            showgrid=True,
            gridcolor="rgba(0,0,0,0.06)",
            zeroline=False,
            tickfont=dict(size=12),
        ),

        # Eixo Y
        yaxis=dict(
            title=None,
            autorange="reversed",   # campeão em cima
            ticks="",
            tickfont=dict(size=12),
        ),

        # Espaçamento entre barras
        bargap=0.24,

        # Legenda: dentro do gráfico, canto inferior direito
        legend=dict(
            orientation="v",
            x=0.99,
            xanchor="right",
            y=0.02,
            yanchor="bottom",
            bgcolor="rgba(255,255,255,0.70)",
            bordercolor="rgba(0,0,0,0.10)",
            borderwidth=1,
            font=dict(size=18),
            title_text=None,
        ),

        legend_traceorder="normal",
    )
    

    st.plotly_chart(fig, use_container_width=True)
