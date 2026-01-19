import streamlit as st
import pandas as pd

from src.ingest import normalize_kiper_csv, insert_events
from ui.sidebar import render_sidebar_menu
from src.helpers import init_state
from src.db import refresh_materialized_views


init_state()

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
            with st.spinner("Atualizando visões agregadas…"):
                refresh_materialized_views()
            # limpa caches de dados (Visão Geral / Relatórios)
            st.cache_data.clear()
            st.success(f"Ingestão concluída! {attempted:,} linhas processadas e visões atualizadas.")
        except Exception as e:
            st.warning("Ingestão feita, mas falhou ao atualizar as visões agregadas.")
            st.exception(e)

st.info("Depois do upload, vá em **Relatórios** para consultar e filtrar os eventos.")