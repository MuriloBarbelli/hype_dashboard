import streamlit as st

PAGES = {
    "Contexto do Projeto": "pages/0_Contexto_do_Projeto.py",
    "Relatórios": "pages/1_Relatorios.py",
    "Visão geral": "pages/2_Visao_Geral.py",
    "Portas": "pages/3_Portas.py",
    "População": "pages/4_Populacao.py",
    "Usuários (Em Breve)": "pages/4_Usuarios.py",
    "Admin": "pages/99_Admin.py",
}

def render_sidebar_menu():
    with st.sidebar:
        options = list(PAGES.keys())

        current = st.session_state.get("current_page", "Contexto do Projeto")
        if current not in options:
            current = "Contexto do Projeto"

        st.sidebar.title("📌 Navegação")

        selected = st.radio(
            "Ir para:",
            options,
            index=options.index(current),
            key="nav_selected",
        )

    if selected != current:
        st.session_state["current_page"] = selected
        st.switch_page(PAGES[selected])
