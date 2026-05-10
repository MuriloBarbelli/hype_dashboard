import os
import streamlit as st
import pandas as pd
from datetime import datetime, date, time as dtime
import time as pytime

from ui.sidebar import render_sidebar_menu
from src.helpers import init_state, render_kiper_table_audit
from src.db import fetch_df, rebuild_event_audit_map_all_sources

init_state()

st.set_page_config(page_title="Admin • Hype", layout="wide")
st.session_state["current_page"] = "Admin"
render_sidebar_menu()

st.title("Admin")

# ============================================================
# 1) Modo de Dados
# ============================================================

admin_pwd = (os.environ.get("ADMIN_PASSWORD") or st.secrets.get("ADMIN_PASSWORD", "")).strip()

_data_mode = st.session_state.get("data_mode", "anon")

c_pwd, c_btn, c_status, _ = st.columns([2, 1, 1, 6], vertical_alignment="bottom")

with c_pwd:
    entered = st.text_input("senha", type="password", label_visibility="collapsed",
                            placeholder="Senha do Admin").strip()
with c_btn:
    if _data_mode == "anon":
        btn_clicked = st.button("🔓 Ativar REAL", use_container_width=True, type="primary")
    else:
        btn_clicked = st.button("🔒 Voltar ANON", use_container_width=True)

with c_status:
    st.caption(f"**{_data_mode.upper()}**")

if btn_clicked:
    if _data_mode == "real":
        st.session_state["data_mode"] = "anon"
        st.rerun()
    elif not admin_pwd:
        st.error("ADMIN_PASSWORD não configurada.")
    elif entered == admin_pwd:
        st.session_state["data_mode"] = "real"
        st.rerun()
    else:
        st.error("Senha incorreta.")

# ============================================================
# 2) Auditoria de Passagens
# ============================================================

@st.cache_data(ttl=60)
def fetch_audit_events_with_passage(
    start_dt: datetime,
    end_dt: datetime,
    door_contains: str,
    slack_seconds: int,
    limit_events: int,
    offset_events: int,
    only_suspicious_groups: bool,
    show_ungrouped: bool,
    order_desc: bool = False,
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

    order_dir = "desc" if order_desc else "asc"

    sql = f"""
    select
    e.event_id,
    e.event_timestamp,
    e.event_type_code,
    e.event_description,
    e.access_name,
    e.user_name,
    e.user_profile,
    e.unit_group,
    e.unit,
    e.handler_name,
    e.handler_profile,
    e.treatment,

    a.audit_group,
    coalesce(a.audit_role, 'UNGROUPED') as audit_role,

    a.passage_kind,
    a.cause_code,
    a.confianca_causa

    from public.events e
    left join public.event_audit_map a
    on a.event_id = e.event_id

    where e.event_timestamp >= %(start)s
    and e.event_timestamp <= %(end)s
    and (%(door)s = '' or e.access_name ilike ('%%' || %(door)s || '%%'))

    and (%(show_ungrouped)s = true or coalesce(a.audit_role,'UNGROUPED') <> 'UNGROUPED')
    and (%(only_suspicious)s = false or (a.audit_score is not null and a.audit_score <= 60))

    order by e.event_timestamp {order_dir}
    limit %(limit)s
    offset %(offset)s;
    """

    return pd.DataFrame(fetch_df(sql, params))


st.divider()
st.header("2) Auditoria de Passagens")
st.caption("Leitura em formato log (Kiper) + anotações do interpretador. Use sempre 1 dia por vez.")

is_admin_ok = bool(admin_pwd) and (st.session_state.get("data_mode") == "real")
if not is_admin_ok:
    st.info("🔒 Para acessar a auditoria, digite a **Senha do Admin** acima.")
else:
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

        c8, c9, c10, c11 = st.columns([1.0, 1.2, 1.0, 1.4], vertical_alignment="bottom")
        with c8:
            slack_seconds = st.slider("Folga p/ match (seg)", 0, 120, 30, 5)
        with c9:
            only_suspicious_groups = st.checkbox("Só grupos suspeitos", value=False)
        with c10:
            run = st.button("Carregar", type="primary")
        with c11:
            sort_order_audit = st.radio(
                "Ordenação", ["↑ Mais antigo", "↓ Mais recente"],
                horizontal=True, label_visibility="collapsed", key="audit_sort_order", index=0
            )

    _order_desc = (sort_order_audit == "↓ Mais recente")

    if run:
        start_dt = datetime.combine(audit_day, audit_start)
        end_dt = datetime.combine(audit_day, audit_end)

        t0 = pytime.time()

        with st.spinner("Carregando auditoria…"):
            dfe = fetch_audit_events_with_passage(
                start_dt=start_dt,
                end_dt=end_dt,
                door_contains=door_contains,
                slack_seconds=slack_seconds,
                limit_events=limit_events,
                offset_events=offset_events,
                only_suspicious_groups=only_suspicious_groups,
                show_ungrouped=show_ungrouped,
                order_desc=_order_desc,
            )

        if dfe.empty:
            st.session_state.pop("audit_df_view", None)
            st.session_state.pop("audit_df_order_desc", None)
            st.warning("Nenhum evento encontrado nesse recorte (ou tudo filtrado).")
            st.stop()

        dfe["event_timestamp"] = pd.to_datetime(dfe["event_timestamp"], utc=True)

        dt = pytime.time() - t0
        st.success(f"OK — {len(dfe):,} eventos exibidos (limit/offset). Tempo: {dt:.1f}s")

        dfe2 = dfe.copy()

        for col in [
            "user_name", "user_profile", "unit_group", "unit", "treatment",
            "audit_group", "audit_role",
            "passage_kind", "cause_code", "confianca_causa",
        ]:
            if col not in dfe2.columns:
                dfe2[col] = None

        dfe2["descricao"] = (
            dfe2["event_type_code"].astype(str)
            + " - "
            + dfe2["event_description"].fillna("").astype(str)
            + "\n"
            + dfe2["access_name"].fillna("").astype(str)
        )

        df_view = dfe2[[
            "event_timestamp", "descricao",
            "user_name", "user_profile", "unit_group", "unit", "treatment",
            "audit_group", "audit_role",
            "passage_kind", "cause_code", "confianca_causa",
        ]].copy()

        st.session_state["audit_df_view"] = df_view
        st.session_state["audit_df_order_desc"] = _order_desc

        render_kiper_table_audit(df_view)
        st.caption("Navegue em páginas com **offset**. Ex.: 0, 3000, 6000…")

    else:
        cached = st.session_state.get("audit_df_view")
        cached_order_desc = st.session_state.get("audit_df_order_desc")

        if cached is not None and not cached.empty:
            if cached_order_desc != _order_desc:
                st.info("Ordenação alterada. Clique em **Carregar** para aplicar.")
            else:
                render_kiper_table_audit(cached)
                st.caption("Navegue em páginas com **offset**. Ex.: 0, 3000, 6000…")
        else:
            st.info("Selecione o dia e o horário e clique em **Carregar**.")

# ============================================================
# 3) Ferramentas de Manutenção
# ============================================================

st.divider()
with st.expander("Ferramentas de Manutenção", expanded=False):
    st.caption(
        "Use **Rebuild auditoria** se a lógica de classificação de passagens foi alterada no código "
        "e você quiser que o histórico inteiro reflita as novas regras — ou se a tabela de auditoria "
        "ficar dessincronizada por algum motivo. "
        "Em operação normal (GitHub Actions funcionando) você nunca precisará clicar aqui."
    )
    if st.button("Rebuild auditoria para TODOS os source_files"):
        with st.spinner("Recalculando auditoria para todos os source_files…"):
            rebuild_event_audit_map_all_sources(slack_seconds=30)
        st.cache_data.clear()
        st.success("Rebuild completo finalizado.")
