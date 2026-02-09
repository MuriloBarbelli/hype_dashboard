import os
import streamlit as st
import pandas as pd
from datetime import datetime, date, time as dtime, timedelta
import time as pytime

from src.ingest import normalize_kiper_csv, insert_events
from ui.sidebar import render_sidebar_menu
from src.helpers import init_state, render_kiper_table_audit
from src.db import refresh_materialized_views, fetch_df, ensure_db_objects

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
    st.dataframe(prepared.head(50), use_container_width=True)

    if st.button("Incorporar ao banco"):
        attempted = insert_events(prepared)

        try:
            with st.spinner("Preparando banco (índices) + atualizando visões…"):
                ensure_db_objects()
                refresh_materialized_views()
            # limpa caches de dados (Visão Geral / Relatórios)
            st.cache_data.clear()
            st.success(f"Ingestão concluída! {attempted:,} linhas processadas e visões atualizadas.")
        except Exception as e:
            st.warning("Ingestão feita, mas falhou ao atualizar as visões agregadas.")
            st.exception(e)

st.info("Depois do upload, vá em **Relatórios** para consultar e filtrar os eventos.")


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
    with e as (
      select
        event_id, event_timestamp, event_type_code,
        event_description, access_name,
        user_name, user_profile, unit,
        handler_name, handler_profile, treatment
      from {src}
      where event_timestamp >= %(start)s
        and event_timestamp <= %(end)s
        and (%(door)s = '' or access_name ilike ('%%' || %(door)s || '%%'))
      order by event_timestamp asc
      limit %(limit)s
      offset %(offset)s
    ),
    p as (
      select
        open_event_id,
        open_ts,
        close_ts,
        door_access_name,
        cause_event_id,
        cause_code,
        passage_kind,
        confianca_causa,
        seconds_open,
        has_held_open,
        has_failed_close,
        has_door_alert
      from public.vw_passage_classification_v5
      where open_ts >= %(start)s
        and open_ts <= %(end)s
        and (%(door)s = '' or door_access_name ilike ('%%' || %(door)s || '%%'))
    ),
    joined as (
      select
        e.*,
        m.open_event_id as audit_group,
        case
          when m.open_event_id is null then 'UNGROUPED'
          when e.event_id = m.open_event_id then 'OPEN'
          when e.event_id = m.cause_event_id then 'CAUSE'
          else 'IN_GROUP'
        end as audit_role,
        case
          when m.open_event_id is null then null
          else (
            'Categoria: ' ||
            (case m.passage_kind
              when 'entrada_facial' then 'Entrada (Facial)'
              when 'saida_botoeira' then 'Saída (Botoeira)'
              when 'entrada_botoeira' then 'Entrada (Botoeira)'
              when 'saida_facial' then 'Saída (Facial)'
              when 'entrada_sem_id' then 'Entrada (Sem identificação)'
              when 'saida_sem_id' then 'Saída (Sem identificação)'
              else coalesce(m.passage_kind, '—')
            end)
            || E'\nCausa: ' ||
            (case m.cause_code
              when 701 then 'Reconhecimento facial'
              when 177 then 'Botoeira de saída'
              when 165 then 'Porta abriu'
              when 167 then 'Porta fechou'
              else ('Código ' || coalesce(m.cause_code::text,'—'))
            end)
            || E'\nConfiança: ' ||
            (case lower(coalesce(m.confianca_causa,''))
              when 'alta' then 'Alta'
              when 'media' then 'Média'
              when 'baixa' then 'Baixa'
              else '—'
            end)
          )
        end as audit_interpretation,



        -- score básico (igual sua lógica, mas simplificado no SQL)
        case
          when m.open_event_id is null then null
          else (
            80
            - (case when lower(coalesce(m.confianca_causa,''))='media' then 15 else 0 end)
            - (case when lower(coalesce(m.confianca_causa,''))='baixa' then 30 else 0 end)
            - (case when coalesce(m.has_failed_close,false) then 20 else 0 end)
            - (case when coalesce(m.has_door_alert,false) then 15 else 0 end)
            - (case when coalesce(m.has_held_open,false) then 10 else 0 end)
            - (case when coalesce(m.seconds_open,0) >= 30 then 10 else 0 end)
            - (case when coalesce(m.seconds_open,0) >= 120 then 20 else 0 end)
          )
        end as audit_score
      from e
      left join lateral (
        select *
        from p
        where p.door_access_name = e.access_name
          and e.event_timestamp >= (p.open_ts - ( %(slack)s || ' seconds')::interval)
          and e.event_timestamp <= (p.close_ts + ( %(slack)s || ' seconds')::interval)
        order by p.open_ts desc
        limit 1
      ) m on true
    )
    select *
    from joined
    where
      (%(show_ungrouped)s = true or audit_role <> 'UNGROUPED')
      and (%(only_suspicious)s = false or (audit_score is not null and audit_score <= 60))
    order by event_timestamp asc;
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
                "audit_interpretation",
            ]
        ].copy()

        render_kiper_table_audit(df_view)


        st.caption("Navegue em páginas com **offset**. Ex.: 0, 3000, 6000…")
