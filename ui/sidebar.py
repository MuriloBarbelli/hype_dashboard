import streamlit as st

PAGES = {
    "Admin": "pages/0_Admin.py",
    "Relatórios": "pages/1_Relatorios.py",
    "Visão geral": "pages/2_Visao_Geral.py",
    "Portas": "pages/3_Portas.py",
    "Usuários": "pages/4_Usuarios.py",
}

def render_sidebar_menu():
    with st.sidebar:
        options = list(PAGES.keys())

        current = st.session_state.get("current_page", "Relatórios")
        if current not in options:
            current = "Relatórios"

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
