import os
import streamlit as st
import pandas as pd
from datetime import datetime, date, time as dtime, timedelta
import time as pytime

from src.ingest import read_kiper_csv, normalize_kiper_csv, insert_events
from ui.sidebar import render_sidebar_menu
from src.helpers import init_state, render_kiper_table_audit
from src.db import (
    refresh_materialized_views,
    fetch_df,
    ensure_db_objects,
    build_event_audit_map_for_source_file,
    rebuild_event_audit_map_all_sources,
)




init_state()

# garante que o banco está pronto mesmo antes de ingestir algo
try:
    ensure_db_objects()
except Exception:
    # não quebra a página se estiver offline / sem credencial
    pass

st.session_state["current_page"] = "Admin"
render_sidebar_menu()

# ============================================================
# Upload
# ============================================================

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
    st.dataframe(prepared.head(50))  # sem use_container_width

    if st.button("Incorporar ao banco"):
      attempted = insert_events(prepared)

      try:
          with st.spinner("Preparando banco (índices) + atualizando visões + gerando cache da auditoria…"):
            ensure_db_objects()
            refresh_materialized_views()

            # build incremental por source_file que REALMENTE foi inserido
            source_files = sorted(set(prepared["source_file"].dropna().astype(str).tolist()))

            for sf in source_files:
                try:
                    build_event_audit_map_for_source_file(sf, slack_seconds=30)
                except Exception as e:
                    st.warning(f"Falhou ao gerar auditoria para {sf}")
                    st.exception(e)


          st.cache_data.clear()
          st.success(f"Ingestão concluída! {attempted:,} linhas processadas, visões atualizadas e auditoria pronta.")

      except Exception as e:
          st.warning("Ingestão feita, mas falhou ao atualizar visões / cache.")
          st.exception(e)



st.info("Depois do upload, vá em **Relatórios** para consultar e filtrar os eventos.")

st.divider()
st.header("2) Manutenção")

if st.button("Rebuild auditoria para TODOS os source_files"):
    with st.spinner("Recalculando auditoria para todos os source_files…"):
        rebuild_event_audit_map_all_sources(slack_seconds=30)
    st.cache_data.clear()
    st.success("Rebuild completo finalizado.")

st.header("Modo de Dados")

admin_pwd = (os.environ.get("ADMIN_PASSWORD") or st.secrets.get("ADMIN_PASSWORD", "")).strip()
entered = st.text_input("Senha do Admin", type="password").strip()

col1, col2 = st.columns(2)

with col1:
    if st.button("Ativar DADOS REAIS"):
        if not admin_pwd:
            st.error("ADMIN_PASSWORD não está configurada no ambiente/Secrets.")
        elif entered == admin_pwd:
            st.session_state["data_mode"] = "real"
            st.success("Modo REAL ativado (somente nesta sessão).")
        else:
            st.session_state["data_mode"] = "anon"
            st.error("Senha incorreta. Mantendo modo ANÔNIMO.")

with col2:
    if st.button("Voltar para ANÔNIMO"):
        st.session_state["data_mode"] = "anon"
        st.info("Modo ANÔNIMO ativado.")

st.caption(f"Modo atual: **{st.session_state.get('data_mode','anon').upper()}**")

# ============================================================
# Auditoria de Passagens (v5) — Admin only
# ============================================================

@st.cache_data(ttl=60)
def fetch_audit_events_with_passage(
    src: str,
    start_dt: datetime,
    end_dt: datetime,
    door_contains: str,
    slack_seconds: int,
    limit_events: int,
    offset_events: int,
    only_suspicious_groups: bool,
    show_ungrouped: bool,
):
    params = {
        "start": start_dt,
        "end": end_dt,
        "door": door_contains or "",
        "slack": int(slack_seconds),
        "limit": int(limit_events),
        "offset": int(offset_events),
        "only_suspicious": only_suspicious_groups,
        "show_ungrouped": show_ungrouped,
    }

    # ⚠️ Ajuste importante:
    # - Passagens vêm da view (vw_passage...) mas performance real vem da MV (mv_passage...)
    # - Mantive vw_passage_classification_v5 porque é o que você já está usando.
    #   Se você confirmar que existe mv_passage_classification_v5 com mesmos campos,
    #   trocamos p/ mv para ficar ainda mais rápido.
    #.  SEMPRE USAR vw
    sql = f"""
    select
      e.event_id,
      e.event_timestamp,
      e.event_type_code,
      e.event_description,
      e.access_name,
      e.user_name,
      e.user_profile,
      e.unit,
      e.handler_name,
      e.handler_profile,
      e.treatment,

      a.audit_group,
      coalesce(a.audit_role, 'UNGROUPED') as audit_role,
      a.audit_interpretation,
      a.audit_score

    from public.events e
    left join public.event_audit_map a
      on a.event_id = e.event_id

    where e.event_timestamp >= %(start)s
      and e.event_timestamp <= %(end)s
      and (%(door)s = '' or e.access_name ilike ('%%' || %(door)s || '%%'))

      and (%(show_ungrouped)s = true or coalesce(a.audit_role,'UNGROUPED') <> 'UNGROUPED')
      and (%(only_suspicious)s = false or (a.audit_score is not null and a.audit_score <= 60))

    order by e.event_timestamp asc
    limit %(limit)s
    offset %(offset)s;
    """


    return pd.DataFrame(fetch_df(sql, params))


st.divider()
st.header("2) Auditoria de Passagens")
st.caption("Leitura em formato log (Kiper) + anotações do interpretador. Use sempre 1 dia por vez.")

# ---- gate admin (se já tiver no seu arquivo, mantém; se não, usa isso)
is_admin_ok = bool(admin_pwd) and (entered == admin_pwd)
if not is_admin_ok:
    st.info("🔒 Para acessar a auditoria, digite a **Senha do Admin** acima.")
else:
    # -----------------------------
    # Filtro próprio (1 dia)
    # -----------------------------
    with st.expander("Filtros (Auditoria)", expanded=True):
        c1, c2, c3, c4 = st.columns([1.2, 1.0, 1.0, 1.2], vertical_alignment="bottom")
        with c1:
            audit_day = st.date_input("Dia", value=date.today())
        with c2:
            audit_start = st.time_input("Hora inicial", value=dtime(0, 0))
        with c3:
            audit_end = st.time_input("Hora final", value=dtime(23, 59))
        with c4:
            door_contains = st.text_input("Porta (contém)", value="").strip()

        c5, c6, c7 = st.columns([1.0, 1.0, 1.4], vertical_alignment="bottom")
        with c5:
            limit_events = st.number_input("Limite eventos", min_value=200, max_value=20000, value=3000, step=200)
        with c6:
            offset_events = st.number_input("Offset", min_value=0, max_value=500000, value=0, step=1000)
        with c7:
            show_ungrouped = st.checkbox("Mostrar eventos NÃO agrupados", value=True)

        c8, c9, c10 = st.columns([1.0, 1.2, 1.4], vertical_alignment="bottom")
        with c8:
            slack_seconds = st.slider("Folga p/ match (seg)", 0, 120, 30, 5)
        with c9:
            only_suspicious_groups = st.checkbox("Só grupos suspeitos", value=False)
        with c10:
            
            run = st.button("Carregar", type="primary")

    if not run:
        st.info("Selecione o dia e o horário e clique em **Carregar**.")
    else:
        # janela datetime
        start_dt = datetime.combine(audit_day, audit_start)
        end_dt = datetime.combine(audit_day, audit_end)

        # fonte de eventos conforme modo
        src = "public.events" if st.session_state.get("data_mode") == "real" else "public.vw_events_anon"

        t0 = pytime.time()

        # -----------------------------
        # 1) Busca eventos brutos (paginado)
        # -----------------------------
        with st.spinner("Carregando auditoria (1 query, sem loops)…"):
            dfe = fetch_audit_events_with_passage(
                src=src,
                start_dt=start_dt,
                end_dt=end_dt,
                door_contains=door_contains,
                slack_seconds=slack_seconds,
                limit_events=limit_events,
                offset_events=offset_events,
                only_suspicious_groups=only_suspicious_groups,
                show_ungrouped=show_ungrouped,
            )

        if dfe.empty:
            st.warning("Nenhum evento encontrado nesse recorte (ou tudo filtrado).")
            st.stop()

        dfe["event_timestamp"] = pd.to_datetime(dfe["event_timestamp"], utc=True)

        dt = pytime.time() - t0
        st.success(f"OK — {len(dfe):,} eventos exibidos (limit/offset). Tempo: {dt:.1f}s")

        # --- monta a coluna "descricao" no mesmo padrão da página Relatórios
        dfe2 = dfe.copy()

        # garante colunas que podem não existir dependendo da fonte (events vs vw_events_anon)
        for col in ["user_name", "user_profile", "unit_group", "unit", "treatment"]:
            if col not in dfe2.columns:
                dfe2[col] = ""

        dfe2["descricao"] = (
            dfe2["event_type_code"].astype(str)
            + " - "
            + dfe2["event_description"].fillna("").astype(str)
            + "\n"
            + dfe2["access_name"].fillna("").astype(str)
        )
        
        # df no formato que o renderer Kiper espera (+ audit cols)
        df_view = dfe2[
            [
                "event_timestamp",
                "descricao",
                "user_name",
                "user_profile",
                "unit_group",
                "unit",
                "treatment",
                "audit_group",
                "audit_role",
                "passage_kind",
                "cause_code",
                "confianca_causa",
            ]
        ].copy()

        render_kiper_table_audit(df_view)

        st.caption("Navegue em páginas com **offset**. Ex.: 0, 3000, 6000…")
