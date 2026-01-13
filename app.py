import streamlit as st
import pandas as pd
import html
import math
import streamlit.components.v1 as components
from datetime import datetime, time

from src.ingest import normalize_kiper_csv, insert_events
from src.db import fetch_df, fetch_distinct_values

st.set_page_config(page_title="Hype – Eventos", layout="wide")
st.title("Hype – Eventos (Kiper)")


# ============================================================
# Helpers: opções + UI
# ============================================================

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


# ============================================================
# Estado
# ============================================================

if "page" not in st.session_state:
    st.session_state.page = 1

if "last_filter_key" not in st.session_state:
    st.session_state.last_filter_key = None


# ============================================================
# Sidebar: Navegação (somente menus)
# ============================================================

st.sidebar.title("📌 Navegação")
page = st.sidebar.radio(
    "Ir para:",
    ["Upload", "Relatórios", "Visão geral (em breve)", "Portas (em breve)", "Usuários (em breve)"],
    index=1
)


# ============================================================
# PAGE: Upload
# ============================================================

if page == "Upload":
    st.header("1) Upload de CSV (Kiper)")

    uploaded = st.file_uploader(
        "Envie um ou mais CSVs exportados do Kiper",
        type=["csv"],
        accept_multiple_files=True
    )

    if uploaded:
        st.info("Vou ler, normalizar e preparar os eventos antes de inserir.")
        prepared_all = []

        for f in uploaded:
            df_raw = pd.read_csv(f, sep=",")
            df_events = normalize_kiper_csv(df_raw, source_file=f.name)
            prepared_all.append(df_events)
            st.write(f"Arquivo **{f.name}** → {len(df_events):,} eventos válidos")

        prepared = pd.concat(prepared_all, ignore_index=True) if prepared_all else pd.DataFrame()

        st.subheader("Prévia do que será inserido")
        st.dataframe(prepared.head(50), use_container_width=True)

        if st.button("Incorporar ao banco"):
            attempted = insert_events(prepared)
            st.success(
                f"Ingestão enviada! Linhas tentadas: {attempted:,}. (duplicatas são ignoradas pelo banco)"
            )

    st.info("Depois do upload, vá em **Relatórios** para consultar e filtrar os eventos.")


# ============================================================
# PAGE: Relatórios (filtros em cima + paginação embaixo)
# ============================================================

elif page == "Relatórios":
    st.header("Relatórios • Eventos")

    # ----------------------------
    # A) Filtros no topo (layout tipo Kiper)
    # ----------------------------
    with st.container(border=True):

        # ===== Linha 1: Evento | Período inicial (data+hora) | Período final (data+hora) | Botão
        col_event, col_start, col_end, col_btn = st.columns([2.4, 1.5, 1.5, 1.0], vertical_alignment="bottom")

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
            c_sd, c_st = st.columns([1.2, 0.8])
            with c_sd:
                start_date = st.date_input("Período inicial", key="start_date")
            with c_st:
                start_time = st.time_input("Hora", value=time(0, 0), key="start_time")

        with col_end:
            c_ed, c_et = st.columns([1.2, 0.8])
            with c_ed:
                end_date = st.date_input("Período final", key="end_date")
            with c_et:
                end_time = st.time_input("Hora", value=time(23, 59), key="end_time")

        with col_btn:
            run = st.button("Gerar relatório", type="primary", use_container_width=True)

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


# ============================================================
# PAGES futuras (placeholder)
# ============================================================

else:
    st.info("Essa página está marcada como 'em breve'. A próxima etapa é mover cada análise para um arquivo em /pages/ 🙂")
