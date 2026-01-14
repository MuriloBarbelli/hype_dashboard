import streamlit as st
import pandas as pd
import html
from datetime import date
import streamlit.components.v1 as components

from src.ingest import normalize_kiper_csv, insert_events
from src.db import fetch_df, fetch_distinct_values

# ============================================================
# Helpers: opções + UI
# ============================================================


def init_state():
    st.session_state.setdefault("page", 1)
    st.session_state.setdefault("last_filter_key", None)
    st.session_state.setdefault("shared_filters", {})
    st.session_state["shared_filters"].setdefault("period", {
        "date_start": date.today(),
        "hour_start": 0,
        "date_end": date.today(),
        "hour_end": 23,
    })

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
    if not profile:
        return ""
    p = str(profile).strip()

    color_map = {
        "Morador": "#2ecc71",
        "Proprietário": "#2ecc71",
        "Morador/Proprietário": "#2ecc71",
        "Familiar": "#2ecc71",

        "Zelador": "#7e57c2",
        "Síndico/Morador": "#7e57c2",

        "Prestador de serviço": "#ff9800",
        "Funcionário": "#ff9800",

        "Hóspede": "#00bcd4",
        "Convidado": "#00bcd4",
    }
    bg = color_map.get(p, "#607d8b")  # fallback
    return f"<span class='badge' style='background:{bg};'>{html.escape(p)}</span>"

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