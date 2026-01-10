import streamlit as st
import pandas as pd
from datetime import datetime, time

from src.ingest import normalize_kiper_csv, insert_events
from src.db import fetch_df

st.set_page_config(page_title="Hype – Eventos", layout="wide")
st.title("Hype – Eventos (Kiper)")

# ----------------------------
# A) Upload e ingestão
# ----------------------------
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
        # Seu CSV é separado por vírgula
        df_raw = pd.read_csv(f, sep=",")
        df_events = normalize_kiper_csv(df_raw, source_file=f.name)
        prepared_all.append(df_events)

        st.write(f"Arquivo **{f.name}** → {len(df_events):,} eventos válidos")

    prepared = pd.concat(prepared_all, ignore_index=True) if prepared_all else pd.DataFrame()

    st.subheader("Prévia do que será inserido")
    st.dataframe(prepared.head(50), use_container_width=True)

    if st.button("Incorporar ao banco"):
        attempted = insert_events(prepared)
        st.success(f"Ingestão enviada! Linhas tentadas: {attempted:,}. (duplicatas são ignoradas pelo banco)")

st.divider()

# ----------------------------
# B) Filtro por data/hora (consulta no banco)
# ----------------------------
st.header("2) Explorar eventos (filtro por período)")

col1, col2, col3, col4 = st.columns(4)

with col1:
    start_date = st.date_input("Data início", value=None)
with col2:
    start_time = st.time_input("Hora início", value=time(0, 0))
with col3:
    end_date = st.date_input("Data fim", value=None)
with col4:
    end_time = st.time_input("Hora fim", value=time(23, 59))

limit = st.slider("Limite de linhas", 50, 5000, 500, step=50)

if start_date and end_date:
    start_dt = datetime.combine(start_date, start_time)
    end_dt = datetime.combine(end_date, end_time)

    sql = """
    SELECT
      event_timestamp,
      access_name,
      event_description,
      user_name,
      user_profile,
      unit_group,
      unit,
      treatment,
      source_file
    FROM public.events
    WHERE event_timestamp BETWEEN %(start)s AND %(end)s
    ORDER BY event_timestamp DESC
    LIMIT %(limit)s;
    """

    rows = fetch_df(sql, {"start": start_dt, "end": end_dt, "limit": limit})
    df_view = pd.DataFrame(rows)

    st.subheader(f"Amostra ({len(df_view):,} linhas)")
    st.dataframe(df_view, use_container_width=True)
else:
    st.warning("Escolha data início e data fim para consultar.")
