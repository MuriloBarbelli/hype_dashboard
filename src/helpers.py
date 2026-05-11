from __future__ import annotations
import streamlit as st
import pandas as pd
import html
import json
import unicodedata
from typing import Optional, Dict, Any
from datetime import datetime, time, timedelta, date
import streamlit.components.v1 as components
import plotly.graph_objects as go

from src.ingest import normalize_kiper_csv, insert_events
from src.db import fetch_df, fetch_distinct_values

# ============================================================
# Helpers: opções + UI
# ============================================================

# === Fonte única de cores (canônicas) ===
KIPER_PROFILE_COLORS = {
    # Moradores
    "Morador": "#2ECC71",
    "Morador/Proprietário": "#2ECC71",
    "Proprietário": "#2ECC71",
    "Familiar": "#2ECC71",

    # Gestão / Staff fixo
    "Síndico/Morador": "#7E57C2",
    "Zelador": "#7E57C2",

    # Operação / Serviços
    "Funcionário": "#FF9800",
    "Prestador de Serviço": "#FF9800",

    # Visitantes
    "Hóspede": "#00BCD4",
    "Convidado": "#00BCD4",

    # Outros
    "Porteiro Monitoramento": "#455A64",
    "Gestor de condomínio": "#546E7A",

    # Fallback explícito (quando quiser usar como rótulo)
    "Sem perfil": "#B0BEC5",
}

# Aliases -> canônico (resolve variações comuns do dado)
_PROFILE_ALIASES = {
    # prestador
    "prestador de servico": "Prestador de Serviço",
    "prestador de serviço": "Prestador de Serviço",
    "prestador de servico ": "Prestador de Serviço",

    # sindico
    "sindico/morador": "Síndico/Morador",
    "síndico/morador": "Síndico/Morador",

    # morador/proprietario
    "morador/proprietario": "Morador/Proprietário",
    "morador/proprietário": "Morador/Proprietário",

    # porteiro monitoramento
    "porteiro monitoramento": "Porteiro Monitoramento",

    # sem perfil
    "": "Sem perfil",
    "sem perfil": "Sem perfil",
    "null": "Sem perfil",
    "none": "Sem perfil",
}

def _norm_key(s: str) -> str:
    """Normaliza string para chave: trim + lower + sem acento."""
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s

def canonical_profile(profile: str | None) -> str:
    """Converte qualquer variação no perfil canônico usado no sistema."""
    key = _norm_key(profile)
    if key in _PROFILE_ALIASES:
        return _PROFILE_ALIASES[key]
    # se não for alias, tenta achar por normalização comparando com as chaves canônicas
    for canonical in KIPER_PROFILE_COLORS.keys():
        if _norm_key(canonical) == key:
            return canonical
    # fallback
    return (profile or "").strip() or "Sem perfil"

def get_profile_color(profile: str | None, default: str = "#B0BEC5") -> str:
    """Retorna cor padronizada para o perfil, com fallback seguro."""
    canon = canonical_profile(profile)
    return KIPER_PROFILE_COLORS.get(canon, default)

def apply_plot_theme(
    fig: go.Figure,
    *,
    height: Optional[int] = None,
    margin: Optional[Dict[str, int]] = None,
    legend: Optional[Dict[str, Any]] = None,
    x_title: Optional[str] = None,
    y_title: Optional[str] = None,
    tickfont_size: int = 12,
    titlefont_size: int = 13,
) -> go.Figure:
    """
    Aplica um tema padrão (clean) nos gráficos Plotly do app.
    Evita props antigas (ex: titlefont) e funciona bem no Plotly atual.
    """

    # Defaults
    if margin is None:
        margin = dict(l=30, r=30, t=30, b=30)

    base_legend = dict(
        bgcolor="rgba(255,255,255,0.75)",
        bordercolor="rgba(0,0,0,0.08)",
        borderwidth=1,
        font=dict(size=11),
        title_text=None,
    )
    if legend:
        base_legend.update(legend)

    fig.update_layout(
        template="simple_white",
        margin=margin,
        font=dict(
            family="Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial",
            size=12,
            color="#223",
        ),
        legend=base_legend,
    )

    if height is not None:
        fig.update_layout(height=height)

    # Eixos: use update_xaxes/update_yaxes (evita keys antigas tipo titlefont)
    fig.update_xaxes(
        title_text=x_title if x_title is not None else fig.layout.xaxis.title.text,
        title_font=dict(size=titlefont_size),
        tickfont=dict(size=tickfont_size),
        showgrid=True,
        gridcolor="rgba(0,0,0,0.06)",
        zeroline=False,
        ticks="outside",
    )

    fig.update_yaxes(
        title_text=y_title if y_title is not None else fig.layout.yaxis.title.text,
        title_font=dict(size=titlefont_size),
        tickfont=dict(size=tickfont_size),
        showgrid=True,
        gridcolor="rgba(0,0,0,0.06)",
        zeroline=False,
        ticks="outside",
    )

    return fig

def snapshot_filters() -> dict:
    """Retorna um dict com tudo que define a consulta atual (period + filtros avançados)."""
    shared = st.session_state.get("shared_filters", {})
    period = shared.get("period", {})
    rel = shared.get("relatorios", {})  # onde você salva event_types/accesses/profiles/search/limit etc

    return {
        "period": {
            "date_start": str(period.get("date_start")),
            "time_start": str(period.get("time_start")),
            "date_end": str(period.get("date_end")),
            "time_end": str(period.get("time_end")),
        },
        "relatorios": {
            "event_types": rel.get("event_types") or [],
            "accesses": rel.get("accesses") or [],
            "profiles": rel.get("profiles") or [],
            "search": rel.get("search") or "",
            "limit": int(rel.get("limit") or 250),
        }
    }

def filters_hash(filters: dict) -> str:
    return json.dumps(filters, sort_keys=True, ensure_ascii=False)

def mark_dirty():
    st.session_state["filters_dirty"] = True

def ensure_apply_state():
    st.session_state.setdefault("filters_dirty", True)
    st.session_state.setdefault("applied_filters_hash", None)
    st.session_state.setdefault("last_apply_ts", None)

def apply_filters_now():
    """Chama quando o usuário clica em Gerar relatório."""
    f = snapshot_filters()
    st.session_state["applied_filters_hash"] = filters_hash(f)
    st.session_state["filters_dirty"] = False
    st.session_state["last_apply_ts"] = datetime.now().isoformat()

def sync_period_and_mark_dirty():
    sync_shared_period_from_widgets()
    mark_dirty()

@st.cache_data(ttl=300, show_spinner=False)
def fetch_max_event_timestamp() -> datetime | None:
    sql = "select max(event_timestamp) as max_ts from public.events;"
    rows = fetch_df(sql)
    if not rows:
        return None
    return rows[0].get("max_ts")

PERIOD_KEYS = {
    "date_start": "period_date_start",
    "time_start": "period_time_start",
    "date_end": "period_date_end",
    "time_end": "period_time_end",
}

def ensure_shared_period():
    st.session_state.setdefault("shared_filters", {})

    # Se ainda não existe period, vamos definir o default baseado no DB
    if "period" not in st.session_state["shared_filters"]:
        max_ts = fetch_max_event_timestamp()

        # Fallback se a base estiver vazia (ou max_ts vier None)
        if max_ts is None:
            end_date = date.today()
        else:
            end_date = max_ts.date()

        # Janela "inclusiva" de 30 dias: (fim - 29) ... fim
        start_date = end_date - timedelta(days=29)

        st.session_state["shared_filters"]["period"] = {
            "date_start": start_date,
            "time_start": time(0, 0),
            "date_end": end_date,
            "time_end": time(23, 59),
        }

    return st.session_state["shared_filters"]["period"]

def apply_shared_period_to_widgets():
    """SEMPRE aplica shared->widgets antes de criar os inputs."""
    p = ensure_shared_period()
    st.session_state[PERIOD_KEYS["date_start"]] = p["date_start"]
    st.session_state[PERIOD_KEYS["time_start"]] = p["time_start"]
    st.session_state[PERIOD_KEYS["date_end"]] = p["date_end"]
    st.session_state[PERIOD_KEYS["time_end"]] = p["time_end"]

def seed_period_widgets_from_shared():
    """
    Aplica o shared period nos widgets quando:
    - os widgets ainda não existem, OU
    - o valor aplicado anteriormente é diferente do shared atual
    (assim não sobrescreve o usuário enquanto ele está mexendo).
    """
    p = ensure_shared_period()

    desired = (
        p["date_start"],
        p["time_start"].hour,
        p["date_end"],
        p["time_end"].hour,
    )

    last_applied = st.session_state.get("_period_last_applied")
    if last_applied == desired:
        return  # já está sincronizado

    st.session_state[PERIOD_KEYS["date_start"]] = p["date_start"]
    st.session_state[PERIOD_KEYS["time_start"]] = p["time_start"]
    st.session_state[PERIOD_KEYS["date_end"]] = p["date_end"]
    st.session_state[PERIOD_KEYS["time_end"]] = p["time_end"]

    st.session_state["_period_last_applied"] = desired

def sync_shared_period_from_widgets():
    """Callback: widgets -> shared."""
    ensure_shared_period()
    st.session_state["shared_filters"]["period"] = {
        "date_start": st.session_state[PERIOD_KEYS["date_start"]],
        "time_start": st.session_state[PERIOD_KEYS["time_start"]],
        "date_end": st.session_state[PERIOD_KEYS["date_end"]],
        "time_end": st.session_state[PERIOD_KEYS["time_end"]],
    }

def init_state():
    st.session_state.setdefault("page", 1)
    st.session_state.setdefault("last_filter_key", None)
    st.session_state.setdefault("shared_filters", {})

    # ✅ garante que a página Admin nunca quebra
    st.session_state.setdefault("data_mode", "anon")

    ensure_shared_period()

@st.cache_data(ttl=60)

def fetch_event_type_options():
    """
    Retorna opções de evento como label 'CODIGO - DESCRICAO',
    mas mantendo o filtro real por event_type_code.
    """
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

def kiper_badge(profile: str) -> str:
    """Badge (cápsula) com cor por perfil."""
    canon = canonical_profile(profile)
    bg = get_profile_color(canon, "#607d8b")
    return f"<span class='badge' style='background:{bg};'>{html.escape(canon)}</span>"

def render_kiper_table(df_raw: pd.DataFrame) -> None:

    """Tabela estilo Kiper via components.html (iframe)."""
    css = """
    <style>
      body { font-family: Inter, system-ui, Arial; margin: 0; }
      .kiper-wrap { width: 100%; }

      .kiper-table { width: 100%; border-collapse: collapse; font-family: Inter, system-ui, Arial; }
      .kiper-table th {
        text-align: left;
        font-size: 13px;
        color:#444;
        padding: 10px 12px;
        border-bottom:1px solid #eee;
        position: sticky;
        top: 0;
        background: white;
        z-index: 1;
      }

      .kiper-table td {
        vertical-align: top;
        padding: 12px;
        border-bottom:1px solid #f0f0f0;
        font-size: 14px;
        color:#222;
      }

      .kiper-muted { color:#666; font-size: 12px; margin-top: 2px; }
      .kiper-line { margin: 0; line-height: 1.2; }

      .kiper-name {
        color:#1a73e8;
        text-decoration: underline;
        font-weight: 500;
        display:inline-block;
      }

      .badge {
        display:inline-block;
        padding: 4px 10px;
        border-radius: 999px;
        color:#fff;
        font-size: 12px;
        font-weight: 600;
        margin-top: 6px;
        width: fit-content;
      }

      .cell-stack { display:flex; flex-direction:column; gap:4px; }
      .row-hover:hover td { background: #fafafa; }

      .col-date { width: 160px; }
      .col-desc { width: 460px; }
      .col-user { width: 250px; }
      .col-gu { width: 220px; }
      .col-reg { width: auto; }

    .dot {
    display: inline-block;
    width: 10px;
    height: 10px;
    border-radius: 999px;
    margin: 0 6px 0 0;
    vertical-align: middle;
    background: #B0BEC5;
    }
    .dot.high { background: #43A047; } /* verde */
    .dot.med  { background: #F9A825; } /* amarelo */
    .dot.low  { background: #E53935; } /* vermelho */


    </style>
    """

    def _s(v) -> str:
        """Converte valor para string limpa, retornando '' para None/NaN."""
        try:
            if pd.isna(v):
                return ""
        except Exception:
            pass
        s = str(v).strip()
        return "" if s.lower() == "nan" else s

    rows_html = []
    for _, r in df_raw.iterrows():
        dt = r.get("event_timestamp")
        if pd.notnull(dt):
            date_str = dt.strftime("%d/%m/%Y")
            time_str = dt.strftime("%H:%M:%S")
        else:
            date_str, time_str = "", ""

        # Descrição: linhas separadas por \n
        desc_lines = str(r.get("descricao") or "").split("\n")
        desc_html = "".join(
            [f"<p class='kiper-line'>{html.escape(line)}</p>" for line in desc_lines if line]
        )

        # Disparado por: nome + badge
        user_name = _s(r.get("user_name"))
        user_profile = _s(r.get("user_profile"))

        user_html_parts = []
        if user_name:
            user_html_parts.append(f"<span class='kiper-name'>{html.escape(user_name)}</span>")
        if user_profile:
            user_html_parts.append(kiper_badge(user_profile))
        user_html = (
            "<div class='cell-stack'>" + "".join(user_html_parts) + "</div>"
            if user_html_parts else ""
        )

        # GU + Unidade (2 linhas)
        ug = _s(r.get("unit_group"))
        un = _s(r.get("unit"))

        gu_html = "<div class='cell-stack'>"
        if ug:
            gu_html += f"<p class='kiper-line'>{html.escape(ug)}</p>"
        if un:
            gu_html += f"<p class='kiper-line'>{html.escape(un)}</p>"
        gu_html += "</div>"

        # Registro do evento
        treatment = _s(r.get("treatment"))
        reg_html = f"<p class='kiper-line'>{html.escape(treatment)}</p>"

        rows_html.append(
            f"""
            <tr class="row-hover">
              <td class="col-date">
                <div class="cell-stack">
                  <div>{html.escape(date_str)}</div>
                  <div class="kiper-muted">{html.escape(time_str)}</div>
                </div>
              </td>
              <td class="col-desc">{desc_html}</td>
              <td class="col-user">{user_html}</td>
              <td class="col-gu">{gu_html}</td>
              <td class="col-reg">{reg_html}</td>
            </tr>
            """
        )

    table_html = f"""
    <html>
      <head>{css}</head>
      <body>
        <div class="kiper-wrap">
          <table class="kiper-table">
            <thead>
              <tr>
                <th class="col-date">Data da ocorrência</th>
                <th class="col-desc">Descrição</th>
                <th class="col-user">Disparado por</th>
                <th class="col-gu">GU + Unidade</th>
                <th class="col-reg">Registro do evento</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows_html)}
            </tbody>
          </table>
        </div>
      </body>
    </html>
    """
    components.html(table_html, height=750, scrolling=True)

def audit_role_badge(role: str) -> str:
    role = (role or "").upper()
    colors = {
        "OPEN":  ("#1E88E5", "OPEN"),
        "CAUSE": ("#43A047", "CAUSE"),
        "IN_GROUP": ("#F9A825", "NO GRUPO"),
        "UNGROUPED": ("#B0BEC5", "NÃO AGRUPADO"),
    }
    bg, label = colors.get(role, ("#B0BEC5", role or ""))
    if not label:
        return ""
    return f"<span class='audit-badge' style='background:{bg};'>{html.escape(label)}</span>"

def group_tint(group_id: str | None) -> str:
    """Cor pastel determinística por grupo (bem leve)."""
    if not group_id:
        return ""
    h = abs(hash(str(group_id))) % 360
    # hsl com alpha baixo (pastel)
    return f"hsla({h}, 70%, 95%, 1.0)"

def render_kiper_table_audit(df_raw: pd.DataFrame) -> None:
    """Tabela estilo Kiper + destaque de grupos + etiquetas de auditoria."""
    css = """
    <style>
      body { font-family: Inter, system-ui, Arial; margin: 0; }
      .kiper-wrap { width: 100%; }

      .kiper-table { width: 100%; border-collapse: collapse; font-family: Inter, system-ui, Arial; }
      .kiper-table th {
        text-align: left;
        font-size: 13px;
        color:#444;
        padding: 10px 12px;
        border-bottom:1px solid #eee;
        position: sticky;
        top: 0;
        background: white;
        z-index: 1;
      }

      .kiper-table td {
        vertical-align: top;
        padding: 12px;
        border-bottom:1px solid #f0f0f0;
        font-size: 14px;
        color:#222;
      }

      .kiper-muted { color:#666; font-size: 12px; margin-top: 2px; }
      .kiper-line { margin: 0; line-height: 1.2; }

      .kiper-name {
        color:#1a73e8;
        text-decoration: underline;
        font-weight: 500;
        display:inline-block;
      }

      .badge {
        display:inline-block;
        padding: 4px 10px;
        border-radius: 999px;
        color:#fff;
        font-size: 12px;
        font-weight: 600;
        margin-top: 6px;
        width: fit-content;
      }

      .cell-stack { display:flex; flex-direction:column; gap:4px; }
      .row-hover:hover td { background: rgba(0,0,0,0.02); }

      .audit-badge{
        display:inline-block;
        padding: 4px 10px;
        border-radius: 999px;
        color:#fff;
        font-size: 11px;
        font-weight: 700;
        width: fit-content;
      }

      .audit-note{
        margin-top: 6px;
        font-size: 12px;
        color:#222;
        padding: 8px 10px;
        border-radius: 10px;
        background: rgba(0,0,0,0.04);
        border: 1px solid rgba(0,0,0,0.06);
        }
        .audit-note div { margin: 2px 0; }

      .group-band{
        border-left: 6px solid rgba(0,0,0,0.06);
      }

      .group-start td {
        border-top: 6px solid rgba(0,0,0,0.06);
      }


      .col-date { width: 160px; }
      .col-desc { width: 460px; }
      .col-user { width: 250px; }
      .col-gu { width: 220px; }
      .col-reg { width: auto; }

        .dot {
        display: inline-block;
        width: 10px;
        height: 10px;
        border-radius: 999px;
        margin: 0 6px 0 0;
        vertical-align: middle;
        background: #B0BEC5;
      }
      .dot.high { background: #43A047; } /* verde */
      .dot.med  { background: #F9A825; } /* amarelo */
      .dot.low  { background: #E53935; } /* vermelho */

      .audit-banner{
        background: rgba(0,0,0,0.03);
        border: 1px solid rgba(0,0,0,0.08);
        border-left: 8px solid rgba(0,0,0,0.18);
        border-radius: 10px;
        padding: 10px 12px;
        display: inline-block;
        max-width: 100%;
        }
        .audit-banner div{
        line-height: 1.25;
        }
      
    </style>
    """

    rows_html = []
    shown_label_groups = set()

    # Paleta fixa (borda bem visível) — não depende de hash/HSL
    GROUP_PALETTE = [
        "#1E88E5",  # azul
        "#8E24AA",  # roxo
        "#43A047",  # verde
        "#F4511E",  # laranja
        "#3949AB",  # índigo
        "#00897B",  # teal
        "#6D4C41",  # marrom
        "#D81B60",  # magenta
        "#5E35B1",  # roxo escuro
        "#039BE5",  # azul claro
        "#7CB342",  # verde limão
        "#FB8C00",  # laranja claro
    ]

    group_color_map = {}
    group_order = []
    def color_for_group(gid: str | None) -> str:
        if not gid:
            return "#B0BEC5"
        if gid not in group_color_map:
            group_order.append(gid)
            group_color_map[gid] = GROUP_PALETTE[(len(group_order) - 1) % len(GROUP_PALETTE)]
        return group_color_map[gid]
    
    group_bg_parity = {}
    def bg_for_group(gid: str | None) -> str:
        if not gid:
            return "rgba(0,0,0,0.00)"
        if gid not in group_bg_parity:
            group_bg_parity[gid] = len(group_bg_parity) % 2
        # alternância bem perceptível, mas suave
        return "rgba(0,0,0,0.02)" if group_bg_parity[gid] == 0 else "rgba(0,0,0,0.06)"

    def _sa(v) -> str:
        """Converte valor para string limpa, retornando '' para None/NaN."""
        try:
            if pd.isna(v):
                return ""
        except Exception:
            pass
        s = str(v).strip()
        return "" if s.lower() == "nan" else s

    for _, r in df_raw.iterrows():
        dt = r.get("event_timestamp")
        if pd.notnull(dt):
            date_str = dt.strftime("%d/%m/%Y")
            time_str = dt.strftime("%H:%M:%S")
        else:
            date_str, time_str = "", ""

        desc_lines = str(r.get("descricao") or "").split("\n")
        desc_html = "".join([f"<p class='kiper-line'>{html.escape(line)}</p>" for line in desc_lines if line])

        user_name = _sa(r.get("user_name"))
        user_profile = _sa(r.get("user_profile"))

        user_html_parts = []
        if user_name:
            user_html_parts.append(f"<span class='kiper-name'>{html.escape(user_name)}</span>")
        if user_profile:
            user_html_parts.append(kiper_badge(user_profile))
        user_html = "<div class='cell-stack'>" + "".join(user_html_parts) + "</div>" if user_html_parts else ""

        ug = _sa(r.get("unit_group"))
        un = _sa(r.get("unit"))
        gu_html = "<div class='cell-stack'>"
        if ug:
            gu_html += f"<p class='kiper-line'>{html.escape(ug)}</p>"
        if un:
            gu_html += f"<p class='kiper-line'>{html.escape(un)}</p>"
        gu_html += "</div>"

        treatment = _sa(r.get("treatment"))
        audit_html = ""

        # --- auditoria
        role = _sa(r.get("audit_role")) or "UNGROUPED"
        role = role.upper()
        group_id = _sa(r.get("audit_group")) or None

        # detecta começo de grupo (para separar visualmente)
        is_group_start = False
        if group_id:
            if "_prev_group_id" not in locals():
                _prev_group_id = None
            is_group_start = (group_id != _prev_group_id)
            _prev_group_id = group_id

        border = color_for_group(group_id)

        # fundo bem leve, mas sempre consistente (não “some”)
        bg = bg_for_group(group_id)


        tr_style = f"background:{bg}; border-left: 8px solid {border};"
        tr_class = "row-hover"
        if is_group_start:
            tr_class += " group-start"

        # --- Card da passagem (somente 1x por grupo, no CAUSE) ---
        show_card = bool(group_id) and (group_id not in shown_label_groups) and (role == "CAUSE")
        if show_card:
            shown_label_groups.add(group_id)

            categoria_label, categoria_color, causa_label, confianca_label = format_passage_card(
                r.get("passage_kind"),
                r.get("cause_code"),
                r.get("confianca_causa"),
            )

            _dot_cls = {"alta": "high", "media": "med", "baixa": "low"}.get(str(confianca_label).lower(), "")
            items = (
                f"<div><b>Categoria:</b> {html.escape(categoria_label)}</div>"
                f"<div><b>Causa:</b> {html.escape(causa_label)}</div>"
                f"<div><b>Confiança:</b> <span class='dot {_dot_cls}'></span>{html.escape(str(confianca_label))}</div>"
            )

            rows_html.append(
                f"<tr style='{tr_style}'>"
                f"<td colspan='5' style='padding: 0 14px 10px 14px;'>"
                f"<div class='audit-banner' style='background:{categoria_color};'>{items}</div>"
                f"</td>"
                f"</tr>"
            )



        # Se não agrupado, coloca nota curta pra facilitar caça de gaps
        if role == "UNGROUPED":
            audit_html += "<div class='audit-note'>Evento fora de grupos</div>"

        reg_html = f"<p class='kiper-line'>{html.escape(treatment)}</p>"
        if audit_html:
            reg_html += f"<div style='margin-top:8px;'>{audit_html}</div>"

        rows_html.append(
            f"""
            <tr class="{tr_class}" style="{tr_style}">
              <td class="col-date">
                <div class="cell-stack">
                  <div>{html.escape(date_str)}</div>
                  <div class="kiper-muted">{html.escape(time_str)}</div>
                </div>
              </td>
              <td class="col-desc">{desc_html}</td>
              <td class="col-user">{user_html}</td>
              <td class="col-gu">{gu_html}</td>
              <td class="col-reg">{reg_html}</td>
            </tr>
            """
        )

    table_html = f"""
    <html>
      <head>{css}</head>
      <body>
        <div class="kiper-wrap">
          <table class="kiper-table">
            <thead>
              <tr>
                <th class="col-date">Data da ocorrência</th>
                <th class="col-desc">Descrição</th>
                <th class="col-user">Disparado por</th>
                <th class="col-gu">GU + Unidade</th>
                <th class="col-reg">Registro do evento</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows_html)}
            </tbody>
          </table>
        </div>
      </body>
    </html>
    """
    components.html(table_html, height=800, scrolling=True)

def format_passage_card(passage_kind, cause_code, confianca=None):
    """
    Formata strings e cor do card da auditoria.
    Mantém passage_kind técnico no banco, mas exibe texto amigável.
    """

    # Normaliza code
    try:
        code = int(cause_code) if cause_code is not None and str(cause_code).strip() != "" else None
    except Exception:
        code = None

    # ✅ fallback inteligente (quando passage_kind vier vazio/nulo)
    # - 311 (app) = entrada identificada (responsabiliza morador)
    # - 177 (botoeira) = saída sem identificação
    # - 370 (base/portaria) = entrada sem identificação
    # - facial/biometria = entrada identificada
    if not passage_kind:
        if code in (177,):
            passage_kind = "saida_sem_id"
        elif code in (701, 708, 703, 257, 311):
            passage_kind = "entrada_id"
        elif code in (370,):
            passage_kind = "intervencao_portaria"

    # Categoria amigável + cor
    if passage_kind == "entrada_id":
        categoria_label = "Entrada (identificada)"
        categoria_color = "#E6F4EA"  # verde claro
    elif passage_kind == "entrada_sem_id":
        categoria_label = "Entrada (sem identificação)"
        categoria_color = "#FFF4E5"  # laranja claro
    elif passage_kind == "saida_sem_id":
        categoria_label = "Saída (sem identificação)"
        categoria_color = "#FDECEC"  # vermelho claro
    elif passage_kind == "intervencao_portaria":
        categoria_label = "Intervenção da portaria"
        categoria_color = "#EDE7F6"  # roxo claro
    else:
        categoria_label = "Passagem"
        categoria_color = "#F0F2F6"  # neutro

    # Causa amigável
    cause_map = {
        701: "Reconhecimento facial (morador)",
        708: "Reconhecimento facial (convidado)",
        703: "Reconhecimento facial (não cadastrado)",
        257: "Biometria (não cadastrada)",
        177: "Botoeira de saída",
        311: "Abertura via aplicativo",
        370: "Abertura pela portaria (base)",
    }
    causa_label = cause_map.get(code, f"Código {code}" if code is not None else "—")

    confianca_label = confianca if confianca not in (None, "", "NULL") else "—"

    return categoria_label, categoria_color, causa_label, confianca_label

