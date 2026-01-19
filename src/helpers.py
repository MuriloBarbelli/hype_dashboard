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

PERIOD_KEYS = {
    "date_start": "period_date_start",
    "time_start": "period_time_start",
    "date_end": "period_date_end",
    "time_end": "period_time_end",
}

def ensure_shared_period():
    st.session_state.setdefault("shared_filters", {})
    st.session_state["shared_filters"].setdefault("period", {
        "date_start": date.today(),
        "time_start": time(0, 0),
        "date_end": date.today(),
        "time_end": time(23, 59),
    })
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
        int(p["hour_start"]),
        p["date_end"],
        int(p["hour_end"]),
    )

    last_applied = st.session_state.get("_period_last_applied")
    if last_applied == desired:
        return  # já está sincronizado

    st.session_state[PERIOD_KEYS["date_start"]] = p["date_start"]
    st.session_state[PERIOD_KEYS["time_start"]] = time(int(p["hour_start"]), 0)
    st.session_state[PERIOD_KEYS["date_end"]] = p["date_end"]
    st.session_state[PERIOD_KEYS["time_end"]] = time(int(p["hour_end"]), 0)

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
    </style>
    """

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
        user_name = str(r.get("user_name") or "").strip()
        user_profile = str(r.get("user_profile") or "").strip()

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
        ug = str(r.get("unit_group") or "").strip()
        un = str(r.get("unit") or "").strip()

        gu_html = "<div class='cell-stack'>"
        if ug:
            gu_html += f"<p class='kiper-line'>{html.escape(ug)}</p>"
        if un:
            gu_html += f"<p class='kiper-line'>{html.escape(un)}</p>"
        gu_html += "</div>"

        # Registro do evento
        treatment = str(r.get("treatment") or "").strip()
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