import streamlit as st
from ui.sidebar import render_sidebar_menu

st.set_page_config(page_title="Contexto do Projeto", page_icon="📌", layout="wide")

st.session_state["current_page"] = "Contexto do Projeto"
render_sidebar_menu()

st.title("Contexto do Projeto")
st.caption("Dashboard de eventos operacionais do controle de acesso (condomínio) — visão executiva para tomada de decisão.")

st.set_page_config(
    page_title="Contexto do Projeto",
    page_icon="📌",
    layout="wide",
)
st.divider()

st.subheader("Problema de negócio")
st.write(
    "Este dashboard foi criado para apoiar a **gestão operacional e administrativa de um condomínio residencial**, "
    "a partir dos eventos do sistema de controle de acesso (entradas, saídas, liberações e ocorrências)."
)
st.write(
    "Na rotina do condomínio, decisões importantes tendem a ser tomadas de forma **manual, reativa ou baseada em percepções isoladas**. "
    "O objetivo aqui é transformar registros operacionais brutos em **informação gerencial clara, confiável e acionável**."
)

st.divider()

st.subheader("Quem utiliza")
st.write("Pensado para uso direto por decisores e operação do condomínio:")
st.markdown(
    "- Síndico e subsíndico\n"
    "- Administração / zeladoria\n"
    "- Gestão de portaria e segurança\n"
)
st.caption("Não é uma ferramenta técnica, é um instrumento de decisão para usuários de negócio.")

st.divider()

st.subheader("Decisões que apoia")
st.markdown(
    "- Entender **volume e padrões de acessos** ao longo do tempo\n"
    "- Identificar **exceções operacionais** e comportamentos atípicos\n"
    "- Avaliar **uso real** de portaria e pontos de acesso\n"
    "- Apoiar ajustes de **processos, regras e dimensionamento de equipe**\n"
    "- Sustentar discussões e prestação de contas com **evidências objetivas**\n"
)

st.divider()

st.subheader("Origem e realidade dos dados")
st.write(
    "Os dados analisados são **reais**, extraídos do banco operacional do condomínio (Supabase), "
    "e representam eventos do sistema de controle de acesso."
)
st.markdown(
    "- Fonte: sistema de controle de acesso\n"
    "- Natureza: registros operacionais de eventos\n"
    "- Atualização: contínua\n"
    "- Privacidade: **anonimização aplicada** para demonstração e portfólio\n"
)
st.info("Este projeto não usa dados simulados nem dataset tutorial. É um caso aplicado a um cenário real.", icon="✅")

st.divider()

st.subheader("Valor analítico do projeto")
st.write(
    "Mais do que um painel visual, este projeto organiza perguntas gerenciais e traduz dados operacionais em "
    "**insights compreensíveis para tomada de decisão**, com rastreabilidade e consistência."
)
st.caption("Foco: apoiar decisões melhores — não apenas exibir métricas.")
