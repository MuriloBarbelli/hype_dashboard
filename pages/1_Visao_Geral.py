import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, time

from src.db import fetch_df

st.set_page_config(page_title="Visão Geral", layout="wide")
st.title("📊 Visão Geral dos Eventos")

# ----------------------------
# Filtros de período
# ----------------------------
col1, col2, col3, col4 = st.columns(4)

with col1:
    start_date = st.date_input("Data início")
with col2:
    start_time = st.time_input("Hora início", value=time(0, 0))
with col3:
    end_date = st.date_input("Data fim")
with col4:
    end_time = st.time_input("Hora fim", value=time(23, 59))

if not start_date or not end_date:
    st.stop()

start_dt = datetime.combine(start_date, start_time)
end_dt = datetime.combine(end_date, end_time)

# ----------------------------
# KPIs
# ----------------------------
sql_kpis = """
select
  count(*) as total_eventos,
  count(distinct access_name) as total_acessos,
  count(distinct unit) as total_unidades
from public.events
where event_timestamp between %(start)s and %(end)s;
"""

kpis = fetch_df(sql_kpis, {"start": start_dt, "end": end_dt})[0]

c1, c2, c3 = st.columns(3)
c1.metric("Total de eventos", f"{kpis['total_eventos']:,}")
c2.metric("Acessos diferentes", f"{kpis['total_acessos']:,}")
c3.metric("Unidades ativas", f"{kpis['total_unidades']:,}")

st.divider()

# ----------------------------
# Eventos por dia
# ----------------------------
sql_dia = """
select
  date(event_timestamp) as dia,
  count(*) as total
from public.events
where event_timestamp between %(start)s and %(end)s
group by 1
order by 1;
"""

df_dia = pd.DataFrame(fetch_df(sql_dia, {"start": start_dt, "end": end_dt}))

fig_dia = px.line(
    df_dia,
    x="dia",
    y="total",
    title="Eventos por dia",
    markers=True
)

st.plotly_chart(fig_dia, use_container_width=True)

# ----------------------------
# Eventos por hora
# ----------------------------
sql_hora = """
select
  extract(hour from event_timestamp) as hora,
  count(*) as total
from public.events
where event_timestamp between %(start)s and %(end)s
group by 1
order by 1;
"""

df_hora = pd.DataFrame(fetch_df(sql_hora, {"start": start_dt, "end": end_dt}))

fig_hora = px.bar(
    df_hora,
    x="hora",
    y="total",
    title="Eventos por hora do dia"
)

st.plotly_chart(fig_hora, use_container_width=True)

# ----------------------------
# Top acessos
# ----------------------------
sql_acessos = """
select
  access_name,
  count(*) as total
from public.events
where event_timestamp between %(start)s and %(end)s
group by 1
order by total desc
limit 10;
"""

df_acessos = pd.DataFrame(fetch_df(sql_acessos, {"start": start_dt, "end": end_dt}))

fig_acessos = px.bar(
    df_acessos,
    x="total",
    y="access_name",
    orientation="h",
    title="Top 10 acessos"
)

st.plotly_chart(fig_acessos, use_container_width=True)
