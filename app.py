import streamlit as st
import pandas as pd
from datetime import datetime, time

from src.ingest import normalize_kiper_csv, insert_events
from src.db import fetch_df, fetch_distinct_values

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
st.header("Relatórios • Eventos")

# ----------------------------
# Sidebar: filtros
# ----------------------------
st.sidebar.header("Filtros")

# --- período ---
start_date = st.sidebar.date_input("Período inicial (data)")
start_time = st.sidebar.time_input("Hora inicial", value=time(0, 0))
end_date = st.sidebar.date_input("Período final (data)")
end_time = st.sidebar.time_input("Hora final", value=time(23, 59))

start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)


# --- filtros ---
event_types = st.sidebar.multiselect(
    "Eventos (opcional)",
    fetch_distinct_values("event_type_code")
)

accesses = st.sidebar.multiselect(
    "Acesso (opcional)",
    fetch_distinct_values("access_name")
)

search = st.sidebar.text_input(
    "Texto no log / Morador / Unidade",
    placeholder="Digite um termo"
).strip()

limit = st.sidebar.selectbox(
    "Resultados por página",
    [100, 250, 500, 1000],
    index=1
)

# --- SQL ---
where = ["event_timestamp between %(start)s and %(end)s"]
params = {"start": start_dt, "end": end_dt, "limit": limit}

if event_types:
    where.append("event_type_code = any(%(event_types)s)")
    params["event_types"] = event_types

if accesses:
    where.append("access_name = any(%(accesses)s)")
    params["accesses"] = accesses

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

sql = f"""
select
  event_timestamp,

  concat_ws(' - ',
    event_type_code::text,
    event_description
  ) || chr(10) || access_name as descricao,

  user_name,
  user_profile,

  concat_ws(' ',
    unit_group,
    unit
  ) as gu_unidade,

  null::text as fechado,

  treatment
from public.events
where {' and '.join(where)}
order by event_timestamp desc, event_id desc
limit %(limit)s;
"""

df = pd.DataFrame(fetch_df(sql, params))

# --- render ---
if df.empty:
    st.info("Nenhum evento encontrado para os filtros.")
else:
    # monta colunas finais
    df_view = pd.DataFrame({
        "Data da ocorrência": df["event_timestamp"],
        "Descrição": df["descricao"],
        "Disparado por": df.apply(
            lambda r: f"{r['user_name']} ({r['user_profile']})" if r["user_name"] else "",
            axis=1
        ),
        "GU + Unidade": df["gu_unidade"],
        "Fechado": df["fechado"],
        "Registro do evento": df["treatment"],
    })

    # estilo do chip (Morador verde)
    def chip(val):
        if "(Morador)" in val:
            return "background-color:#2ecc71;color:white;font-weight:600;"
        return ""

    st.dataframe(
        df_view.style.applymap(chip, subset=["Disparado por"]),
        use_container_width=True
    )

    st.caption(f"{len(df_view):,} registros exibidos")