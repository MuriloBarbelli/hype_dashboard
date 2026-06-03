import html as html_lib
from datetime import date, timedelta
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from src.db import fetch_df
from ui.sidebar import render_sidebar_menu
from src.helpers import init_state, apply_plot_theme, get_profile_color, canonical_profile

# ─── Configuração ─────────────────────────────────────────────────────────────
st.set_page_config(page_title="População • Hype", layout="wide")
init_state()
st.session_state["current_page"] = "População"
render_sidebar_menu()

# ─── Constantes visuais ───────────────────────────────────────────────────────
STATUS_COLORS = {
    "alerta": "#E24B4A",
    "ativo":  "#1D9E75",
    "fraco":  "#EF9F27",
    "vazio":  "#444441",
}
STATUS_LABELS = {
    "alerta": "Alerta aberto",
    "ativo":  "Residente ativo",
    "fraco":  "Em viagem / presença fraca",
    "vazio":  "Sem entradas registradas",
}
MESES_PT = {1:'jan',2:'fev',3:'mar',4:'abr',5:'mai',6:'jun',
            7:'jul',8:'ago',9:'set',10:'out',11:'nov',12:'dez'}

CLASSIFICACOES = [
    "morador", "locatario", "familiar_cohabitante", "namorado",
    "funcionario_fixo", "visitante_frequente", "erro_cadastro", "outro",
]
CONFIDENCIAS = ["confirmado", "suspeito", "perguntar_porteiro"]

# ─── Geração das unidades do condomínio ──────────────────────────────────────
def _build_units() -> tuple[frozenset, frozenset]:
    res: set[str] = set()
    for andar in range(4, 17):
        for f in [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]:
            res.add(f"Apartamento {andar}{f:02d}")
    for f in [1, 11, 12, 13]:
        res.add(f"Apartamento 17{f:02d}")

    nr: set[str] = set()
    nr_tipo_i = {
        1: [3, 4, 7, 10, 11, 12, 13],
        2: [3, 4, 6, 7, 10, 11, 12, 13],
        3: [3, 4, 6, 7, 10, 11, 12, 13],
    }
    for andar, finais in nr_tipo_i.items():
        for f in finais:
            nr.add(f"Apartamento {andar}{f:02d}")
    for andar in [1, 2, 3]:
        for f in [1, 2, 8, 9]:
            nr.add(f"Apartamento {andar}{f:02d}")
    return frozenset(res), frozenset(nr)


UNIDADES_RES, UNIDADES_NR = _build_units()

# ─── Queries cacheadas ────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def load_view() -> pd.DataFrame:
    rows = fetch_df("""
        SELECT
            user_name, user_profile, unit, setor_condominio,
            media_dias_por_mes, meses_residente, meses_residente_recente,
            ultimo_mes_com_entrada, classificacao_auto, classificacao_efetiva,
            alerta, classificacao_manual, confidence, notas_revisao,
            reviewed_at, reviewed_by
        FROM vw_resident_classification
        WHERE unit IS NOT NULL
          AND btrim(unit) != ''
          AND unit NOT LIKE '%%000%%'
    """)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def load_anon_map() -> dict:
    rows = fetch_df("SELECT user_name_real, user_name_anon FROM user_anon_map")
    return {r["user_name_real"]: r["user_name_anon"] for r in rows} if rows else {}


@st.cache_data(ttl=3600, show_spinner=False)
def load_evolucao(unidades: frozenset) -> pd.DataFrame:
    if not unidades:
        return pd.DataFrame()
    rows = fetch_df("""
        WITH presenca_mensal AS (
            SELECT
                user_name,
                unit,
                DATE_TRUNC('month', event_timestamp)::date AS mes,
                COUNT(DISTINCT DATE(event_timestamp))      AS dias_no_mes
            FROM public.events
            WHERE event_type_code IN (701, 708, 311, 183)
              AND access_name IN (
                  '6062 Portão Pedestre Interno',
                  '6064 Hall Residencial',
                  '6061 Portão Pedestre Externo',
                  '6063 Hall NR'
              )
              AND user_name IS NOT NULL AND btrim(user_name) != ''
              AND unit     IS NOT NULL AND btrim(unit)      != ''
              AND unit NOT LIKE '%%999%%'
              AND unit = ANY(%(unidades)s)
              AND DATE_TRUNC('month', event_timestamp) < DATE_TRUNC('month', NOW())
            GROUP BY user_name, unit, mes
        )
        SELECT
            mes,
            COUNT(DISTINCT user_name) FILTER (WHERE dias_no_mes >= 13) AS residentes_ativos,
            COUNT(DISTINCT unit)      FILTER (WHERE dias_no_mes >= 13) AS aptos_ocupados
        FROM presenca_mensal
        GROUP BY mes
        ORDER BY mes
    """, {"unidades": list(unidades)})
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_detalhes_apto(unit_str: str) -> pd.DataFrame:
    # CTE `pessoas`: uma linha por (user_name, user_profile) — preserva duplicatas de perfil
    # CTE `acessos`: agrega events por user_name apenas — ao fazer LEFT JOIN,
    #   as duas linhas de Vanessa (Morador e Convidado) recebem os mesmos valores
    rows = fetch_df("""
        WITH pessoas AS (
            SELECT
                user_name, user_profile, classificacao_auto, classificacao_efetiva,
                alerta, media_dias_por_mes, meses_residente_recente,
                ultimo_mes_com_entrada, classificacao_manual, confidence,
                notas_revisao, total_meses
            FROM vw_resident_classification
            WHERE unit = %(unit)s
        ),
        acessos AS (
            SELECT
                user_name,
                COUNT(DISTINCT DATE(event_timestamp)) FILTER (
                    WHERE DATE_TRUNC('month', event_timestamp) =
                          DATE_TRUNC('month', NOW() - INTERVAL '1 month')
                ) AS dias_ultimo_mes,
                MAX(DATE(event_timestamp)) AS data_ultimo_acesso
            FROM public.events
            WHERE unit = %(unit)s
              AND event_type_code IN (701, 708, 311, 183)
              AND access_name IN (
                  '6062 Portão Pedestre Interno',
                  '6064 Hall Residencial',
                  '6061 Portão Pedestre Externo',
                  '6063 Hall NR'
              )
            GROUP BY user_name
        )
        SELECT
            p.*,
            COALESCE(a.dias_ultimo_mes, 0) AS dias_ultimo_mes,
            a.data_ultimo_acesso
        FROM pessoas p
        LEFT JOIN acessos a ON a.user_name = p.user_name
        ORDER BY p.media_dias_por_mes DESC NULLS LAST
    """, {"unit": unit_str})
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ─── Helpers internos ─────────────────────────────────────────────────────────
def _unit_status(df_u: pd.DataFrame) -> str:
    if df_u.empty:
        return "vazio"
    if (df_u["alerta"].notna() & df_u["classificacao_manual"].isna()).any():
        return "alerta"
    if df_u["classificacao_efetiva"].isin(["residente_ativo", "novo_morador"]).any():
        return "ativo"
    if df_u["classificacao_efetiva"].isin(["em_viagem", "presenca_regular"]).any():
        return "fraco"
    return "vazio"


def _apply_anon(df: pd.DataFrame, anon_map: dict) -> pd.DataFrame:
    if df.empty or "user_name" not in df.columns:
        return df
    df = df.copy()
    df["user_name"] = df["user_name"].map(lambda n: anon_map.get(n, n) if pd.notna(n) else n)
    return df


def _cls_badge(c: str | None) -> str:
    return {
        "residente_ativo":  "🟢 Residente ativo",
        "novo_morador":     "🟢 Novo morador",
        "em_viagem":        "🟡 Em viagem",
        "presenca_regular": "🟡 Presença regular",
        "visitante":        "⚪ Visitante",
    }.get(c or "", c or "—")


def _profile_badge(profile: str | None) -> str:
    """Badge HTML com cor canônica do perfil, idêntica ao kiper_badge do projeto."""
    canon = canonical_profile(profile or "")
    bg = get_profile_color(canon, "#607d8b")
    label = html_lib.escape(canon if canon != "Sem perfil" else (profile or "—"))
    return (
        f"<span style='background:{bg};color:#fff;padding:2px 9px;"
        f"border-radius:12px;font-size:11px;font-weight:500;white-space:nowrap;'>"
        f"{label}</span>"
    )


def _detectar_duplicatas(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Colapsa linhas que provavelmente são a mesma pessoa física."""
    if df.empty:
        return df, []

    df = df.copy().reset_index(drop=True)
    df["_norm"] = df["user_name"].str.strip().str.lower()

    avisos: list[str] = []
    grupos: list[list[int]] = []
    usado: set[int] = set()

    for i in range(len(df)):
        if i in usado:
            continue
        grupo = [i]
        ni = df.at[i, "_norm"]
        for j in range(i + 1, len(df)):
            if j in usado:
                continue
            nj = df.at[j, "_norm"]
            # mesmo nome exato com perfil diferente → duplicata certa
            if ni == nj and df.at[i, "user_profile"] != df.at[j, "user_profile"]:
                grupo.append(j)
        grupos.append(grupo)
        usado.update(grupo)

    linhas: list[dict] = []
    for grupo in grupos:
        rows = df.iloc[grupo]
        if len(grupo) == 1:
            r = rows.iloc[0].to_dict()
            r["_is_dup"] = False
            linhas.append(r)
            continue

        perfis = ", ".join(p for p in rows["user_profile"].dropna().unique() if p)
        nome_principal = rows["user_name"].iloc[0]
        media = float(rows["media_dias_por_mes"].fillna(0).sum())
        alerta    = next((v for v in rows["alerta"] if pd.notna(v)), None)
        cls_ef    = next((v for v in rows["classificacao_efetiva"] if pd.notna(v)), "")
        cls_man   = next((v for v in rows["classificacao_manual"] if pd.notna(v)), None)
        meses_rec = rows["meses_residente_recente"].max()
        nome_real = rows["_nome_real"].iloc[0] if "_nome_real" in rows.columns else nome_principal

        avisos.append(
            f"⚠️ Possível cadastro duplicado: **{nome_principal}** aparece com "
            f"{len(grupo)} perfis diferentes ({perfis}). Os registros foram unificados."
        )

        r = rows.iloc[0].to_dict()
        r["user_profile"]            = perfis
        r["media_dias_por_mes"]      = media if media > 0 else None
        r["alerta"]                  = alerta
        r["classificacao_efetiva"]   = cls_ef
        r["classificacao_manual"]    = cls_man
        r["meses_residente_recente"] = meses_rec
        r["_nome_real"]              = nome_real
        r["_is_dup"]                 = True
        linhas.append(r)

    return pd.DataFrame(linhas).drop(columns=["_norm"]), avisos


def _is_valid(v) -> bool:
    """True se o valor não é None, NaN, ou string vazia/nan."""
    if v is None:
        return False
    try:
        if pd.isna(v):
            return False
    except (TypeError, ValueError):
        pass
    return str(v).strip().lower() not in ("", "nan", "none")


def _fmt_mes(d) -> str:
    try:
        return f"{MESES_PT[d.month]}/{str(d.year)[2:]}"
    except (AttributeError, KeyError, TypeError):
        return str(d) if d else "?"


def _fmt_data(d) -> str:
    """Formata date como DD/MM/AA."""
    try:
        return d.strftime("%d/%m/%y")
    except (AttributeError, TypeError):
        return str(d) if d else ""


def _freq_html(row: pd.Series, mes_label: str) -> str:
    total_meses   = int(row.get("total_meses") or 0)
    media         = row.get("media_dias_por_mes")
    dias_ultimo   = int(row.get("dias_ultimo_mes") or 0)
    data_acesso   = row.get("data_ultimo_acesso")
    acesso_str    = f"último acesso: {_fmt_data(data_acesso)}" if data_acesso else ""

    if total_meses == 1 and not dias_ultimo:
        parts = ["Visita única"]
        if acesso_str:
            parts.append(acesso_str)
        return " &nbsp;·&nbsp; ".join(parts)

    parts: list[str] = []
    if _is_valid(media):
        sufixo = " (visita única)" if total_meses == 1 else " (média)"
        parts.append(f"{float(media):.1f} dias/mês{sufixo}")
    if dias_ultimo:
        parts.append(f"{dias_ultimo} dias em {mes_label}")
    if acesso_str:
        parts.append(acesso_str)
    return " &nbsp;·&nbsp; ".join(parts) if parts else "sem dados"


def _build_card_html(row: pd.Series, mes_label: str) -> str:
    nome       = html_lib.escape(str(row["user_name"]))
    perfis_str = row.get("user_profile") or ""
    cls_ef     = row.get("classificacao_efetiva") or ""
    cls_man    = row.get("classificacao_manual")
    alerta     = row.get("alerta")
    is_dup     = row.get("_is_dup", False)

    # badges de perfil
    badges = " ".join(
        _profile_badge(p.strip()) for p in perfis_str.split(",") if p.strip()
    ) or _profile_badge(None)
    if is_dup:
        badges += (
            " <span style='background:#E24B4A;color:#fff;padding:2px 8px;"
            "border-radius:12px;font-size:11px;font-weight:500;'>duplicata</span>"
        )

    # linha de frequência
    freq = _freq_html(row, mes_label)
    cls_text = html_lib.escape(_cls_badge(cls_ef))
    info_line = f"{cls_text} &nbsp;·&nbsp; {freq}"

    # blocos opcionais — só renderiza se valor real (não nan)
    alerta_block = ""
    if _is_valid(alerta):
        alerta_text = html_lib.escape(str(alerta))
        alerta_block = (
            f"<div style='margin-top:6px;padding:4px 10px;background:#FFF3CD;"
            f"border-left:3px solid #EF9F27;border-radius:4px;"
            f"font-size:12px;color:#7a5200;'>⚠ {alerta_text}</div>"
        )
    cls_man_block = ""
    if _is_valid(cls_man):
        cls_man_text = html_lib.escape(str(cls_man))
        cls_man_block = (
            f"<div style='margin-top:6px;padding:4px 10px;background:#E8F5E9;"
            f"border-left:3px solid #1D9E75;border-radius:4px;"
            f"font-size:12px;color:#1a5c38;'>✔ {cls_man_text}</div>"
        )

    return (
        f"<div style='border:1px solid #e0e0e0;border-radius:8px;padding:12px 14px;"
        f"margin-bottom:8px;background:#fff;'>"
        f"<div style='display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:5px;'>"
        f"<span style='font-weight:600;font-size:14px;color:#111;'>{nome}</span>"
        f"{badges}"
        f"</div>"
        f"<div style='font-size:12px;color:#555;'>{info_line}</div>"
        f"{alerta_block}{cls_man_block}"
        f"</div>"
    )


def _alerta_prio(txt: str | None) -> int:
    t = (txt or "").lower()
    if "proprietário" in t or "infração" in t:
        return 0
    if "comportamento" in t or "suspeito" in t:
        return 1
    return 2


# ─── Cabeçalho e filtro de setor ─────────────────────────────────────────────
st.title("População")

data_mode = st.session_state.get("data_mode", "anon")
is_real = data_mode == "real"

setor_op = st.radio(
    "Setor",
    ["Residencial", "NR (Serviços de Moradia)", "Todos"],
    horizontal=True,
    label_visibility="collapsed",
)

unidades_setor: frozenset
if setor_op == "Residencial":
    unidades_setor = UNIDADES_RES
elif setor_op == "NR (Serviços de Moradia)":
    unidades_setor = UNIDADES_NR
else:
    unidades_setor = UNIDADES_RES | UNIDADES_NR

# ─── Carregamento de dados ────────────────────────────────────────────────────
df_view = load_view()
anon_map = {} if is_real else load_anon_map()

if df_view.empty:
    st.warning("Sem dados na view vw_resident_classification.")
    st.stop()

df_setor = df_view[df_view["unit"].isin(unidades_setor)].copy()
if not is_real:
    df_setor = _apply_anon(df_setor, anon_map)

# ─── KPIs ────────────────────────────────────────────────────────────────────
ativos_mask  = df_setor["classificacao_efetiva"].isin(["residente_ativo", "novo_morador"])
alertas_mask = df_setor["alerta"].notna() & df_setor["classificacao_manual"].isna()

n_residentes  = int(df_setor.loc[ativos_mask, "user_name"].nunique())
n_ocupados    = int(df_setor.loc[ativos_mask, "unit"].nunique())
n_vazios      = len(unidades_setor) - n_ocupados
n_alertas     = int(df_setor.loc[alertas_mask, "unit"].nunique())

k1, k2, k3, k4 = st.columns(4)
k1.metric("Residentes ativos",      n_residentes)
k2.metric("Apartamentos ocupados",  n_ocupados)
k3.metric("Apartamentos vazios",    n_vazios)
k4.metric("Alertas abertos",        n_alertas)

# ─── Evolução mensal ─────────────────────────────────────────────────────────
st.subheader("Evolução mensal")
df_evo = load_evolucao(unidades_setor)

if not df_evo.empty:
    df_evo["mes"] = pd.to_datetime(df_evo["mes"])
    fig_evo = go.Figure()
    fig_evo.add_trace(go.Scatter(
        x=df_evo["mes"], y=df_evo["residentes_ativos"],
        name="Residentes ativos",
        mode="lines+markers",
        line=dict(color="#1D9E75", width=2),
        marker=dict(size=6),
    ))
    fig_evo.add_trace(go.Scatter(
        x=df_evo["mes"], y=df_evo["aptos_ocupados"],
        name="Aptos ocupados",
        mode="lines+markers",
        line=dict(color="#5B9BD5", width=2, dash="dot"),
        marker=dict(size=6),
    ))
    fig_evo = apply_plot_theme(
        fig_evo,
        height=260,
        margin=dict(l=20, r=20, t=30, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        x_title=None,
        y_title="Pessoas / Aptos",
    )
    st.plotly_chart(fig_evo, use_container_width=True)
else:
    st.info("Sem dados históricos para o setor selecionado.")

# ─── Mapa do prédio ──────────────────────────────────────────────────────────
st.subheader("Mapa do prédio")

if setor_op == "NR (Serviços de Moradia)":
    st.caption("ℹ️ Setor NR: hóspedes frequentes são esperados (operação tipo hospedagem). "
               "Alertas de 'convidado com comportamento de morador' não se aplicam aqui.")

# Agrega status por unidade
unit_status_cache: dict[str, str] = {}
for u in unidades_setor:
    unit_status_cache[u] = _unit_status(df_setor[df_setor["unit"] == u])

# Monta linhas para o scatter
map_rows = []
for u in unidades_setor:
    num_str = u.replace("Apartamento", "").strip()
    try:
        num = int(num_str)
    except ValueError:
        continue
    andar = num // 100
    final = num % 100
    status = unit_status_cache[u]
    map_rows.append({
        "unit": u, "num": num, "andar": andar, "final": final,
        "status": status, "color": STATUS_COLORS[status], "label": num_str,
    })

df_map = pd.DataFrame(map_rows)

fig_map = go.Figure()

# Traces de legenda (invisíveis, apenas para o legend box)
for st_key, color in STATUS_COLORS.items():
    fig_map.add_trace(go.Scatter(
        x=[None], y=[None], mode="markers",
        marker=dict(symbol="square", size=12, color=color),
        name=STATUS_LABELS[st_key],
    ))

# Trace principal com todos os apartamentos
fig_map.add_trace(go.Scatter(
    x=df_map["final"].tolist(),
    y=df_map["andar"].tolist(),
    mode="markers+text",
    marker=dict(
        symbol="square",
        size=38,
        color=df_map["color"].tolist(),
        line=dict(width=1, color="rgba(255,255,255,0.3)"),
    ),
    text=df_map["label"].tolist(),
    textfont=dict(size=11, color="white"),
    textposition="middle center",
    customdata=df_map["unit"].tolist(),
    hovertemplate="<b>%{customdata}</b><extra></extra>",
    showlegend=False,
))

finais_unicos  = sorted(df_map["final"].unique())
andares_unicos = sorted(df_map["andar"].unique())  # ascendente: andar 1 na base, 17 no topo

fig_map.update_layout(
    width=680,
    height=max(380, len(andares_unicos) * 38),
    margin=dict(l=40, r=10, t=40, b=40),
    template="simple_white",
    plot_bgcolor="rgba(248,248,248,1)",
    xaxis=dict(
        tickvals=finais_unicos,
        ticktext=[f"{f:02d}" for f in finais_unicos],
        dtick=1,
        title=None,
        showgrid=False,
        zeroline=False,
        side="top",
        range=[0.4, 13.6],
    ),
    yaxis=dict(
        tickvals=andares_unicos,
        ticktext=[str(a) for a in andares_unicos],
        title="Andar",
        showgrid=False,
        zeroline=False,
        range=[min(andares_unicos) - 0.4, max(andares_unicos) + 0.4],  # proporcional ao x, marcadores quadrados
    ),
    legend=dict(
        orientation="h",
        yanchor="bottom", y=1.06,
        xanchor="left", x=0,
        font=dict(size=10),
    ),
    clickmode="event+select",
)

st.markdown("""
<style>
div[data-testid="column"]:nth-child(1) {
    flex: 0 0 auto !important;
    width: fit-content !important;
    min-width: 0 !important;
}
div[data-testid="column"]:nth-child(2) {
    flex: 1 1 auto !important;
    min-width: 360px !important;
}

</style>
""", unsafe_allow_html=True)

col_mapa, col_detalhe = st.columns([1, 1])

with col_mapa:
    map_event = st.plotly_chart(
        fig_map,
        use_container_width=False,
        on_select="rerun",
        selection_mode="points",
        key="mapa_predio",
    )

# ─── Captura de clique no mapa ────────────────────────────────────────────────
if map_event and getattr(map_event, "selection", None):
    pts = map_event.selection.points
    if pts:
        cd = pts[0].get("customdata")
        if cd:
            st.session_state["apto_selecionado"] = cd

apto_sel: str | None = st.session_state.get("apto_selecionado")

# ─── Painel de detalhes do apartamento ───────────────────────────────────────
# label do mês anterior para exibição (ex: "mai/26")
_mes_ant = (date.today().replace(day=1) - timedelta(days=1))
MES_LABEL = f"{MESES_PT[_mes_ant.month]}/{str(_mes_ant.year)[2:]}"

with col_detalhe:
    if not apto_sel:
        st.markdown(
            "<div style='color:#aaa;padding:2rem 0;text-align:center;'>"
            "Clique em um apartamento<br>para ver os detalhes."
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        num_sel = apto_sel.replace("Apartamento", "").strip()
        st.markdown(f"**Apartamento {num_sel}**")
        df_det = load_detalhes_apto(apto_sel)

        if df_det.empty:
            st.info("Nenhum registro encontrado para este apartamento.")
        else:
            # preserva nomes reais antes do anon
            df_det["_nome_real"] = df_det["user_name"]
            if not is_real:
                df_det = _apply_anon(df_det, anon_map)

            # detecta e colapsa duplicatas (avisos visíveis nos próprios cards)
            df_det, _ = _detectar_duplicatas(df_det)

            # ── cards como HTML puro dentro de div scrollável ──────────────
            cards_html = "".join(
                _build_card_html(row, MES_LABEL)
                for _, row in df_det.iterrows()
            )
            st.markdown(
                f"<div style='height:620px;overflow-y:auto;padding-right:8px;'>"
                f"{cards_html}</div>",
                unsafe_allow_html=True,
            )

            # ── formulários de revisão abaixo do scroll (só modo real) ─────
            if is_real:
                for _, row in df_det.iterrows():
                    if not (_is_valid(row.get("alerta")) and not _is_valid(row.get("classificacao_manual"))):
                        continue
                    nome_exib = row["user_name"]
                    nome_real = row.get("_nome_real") or nome_exib
                    form_key  = f"rev_{apto_sel}_{nome_real}"
                    st.markdown(f"**Revisão — {nome_exib}**")
                    with st.form(key=form_key, border=True):
                        col_cls, col_conf = st.columns(2)
                        nova_cls  = col_cls.selectbox(
                            "Classificação", CLASSIFICACOES, key=f"cls_{form_key}"
                        )
                        nova_conf = col_conf.radio(
                            "Confiança", CONFIDENCIAS, horizontal=True, key=f"conf_{form_key}"
                        )
                        notas = st.text_input("Observações (opcional)", key=f"notas_{form_key}")
                        if st.form_submit_button("Salvar revisão", type="primary"):
                            fetch_df("""
                                INSERT INTO resident_review
                                    (user_name, unit, classification, confidence,
                                     notes, reviewed_at, reviewed_by)
                                VALUES
                                    (%(user_name)s, %(unit)s, %(classification)s,
                                     %(confidence)s, %(notes)s, NOW(), 'sindico')
                                ON CONFLICT DO NOTHING
                            """, {
                                "user_name":      nome_real,
                                "unit":           apto_sel,
                                "classification": nova_cls,
                                "confidence":     nova_conf,
                                "notes":          notas or None,
                            })
                            st.success("Revisão salva!")
                            st.cache_data.clear()
                            st.rerun()

# ─── Fila de alertas ─────────────────────────────────────────────────────────
st.divider()
st.subheader("Fila de alertas")

df_alertas = df_setor[
    df_setor["alerta"].notna() & df_setor["classificacao_manual"].isna()
].copy()

if df_alertas.empty:
    st.success("Nenhum alerta aberto no setor selecionado.", icon="✅")
else:
    df_alertas["_prio"] = df_alertas["alerta"].apply(_alerta_prio)
    df_alertas = df_alertas.sort_values(["_prio", "unit"]).reset_index(drop=True)

    # Cabeçalho
    h1, h2, h3, h4, h5, h6 = st.columns([1, 2, 1.5, 1.8, 3, 0.8])
    h1.caption("**Apto**")
    h2.caption("**Nome**")
    h3.caption("**Perfil**")
    h4.caption("**Classificação auto**")
    h5.caption("**Alerta**")
    h6.caption("")

    for _, row in df_alertas.iterrows():
        num = row["unit"].replace("Apartamento", "").strip()
        c1, c2, c3, c4, c5, c6 = st.columns([1, 2, 1.5, 1.8, 3, 0.8])
        c1.markdown(f"**{num}**")
        c2.markdown(row["user_name"])
        c3.markdown(row.get("user_profile") or "—")
        c4.markdown(_cls_badge(row.get("classificacao_auto") or ""))
        c5.markdown(f":orange[⚠ {row['alerta']}]")
        if c6.button("Ver", key=f"btn_{row['unit']}_{row['user_name']}"):
            st.session_state["apto_selecionado"] = row["unit"]
            st.rerun()
