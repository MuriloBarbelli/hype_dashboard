"""
tools/update_user_anon_map.py

Popula user_anon_map com todos os usuários de events que ainda não têm
mapeamento de anonimização.

Colunas da tabela:
  user_name_real  — nome original
  user_name_anon  — codinome gerado
  user_name_norm  — replica exata do norm_text() do Postgres (ver função abaixo)

Pode rodar quantas vezes quiser — idempotente.
"""

from dotenv import load_dotenv
load_dotenv()

import os
import random
import re
import psycopg2
from supabase import create_client

SUPABASE_URL              = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ANON_SEED                 = os.environ["ANON_SEED"]

# Perfis que a vw_events_anon mantém com nome real — não precisam de codinome
FUNCTIONAL_PROFILES = {
    'porteiro monitoramento', 'funcionário', 'zelador', 'gestor de condomínio'
}

# Norms bloqueadas pelo trigger trg_block_never_anon_users no banco
BLOCKED_NORMS = {'portaria principal'}

FIRST_NAMES = [
    "Frederico","Bruno","Thiago","Rafael","Eduardo","Gustavo","Henrique","Felipe",
    "Mariana","Fernanda","Camila","Juliana","Patricia","Renata","Leticia","Beatriz",
    "Gabriel","Lucas","Matheus","Diego","Vitor","Andressa","Carolina","Aline",
    "Rodrigo","Ricardo","Daniel","Vinicius","Caio","Isabela","Larissa","Bianca"
]
LAST_NAMES = [
    "Albuquerque","Menezes","Barbosa","Nogueira","Ferraz","Monteiro","Ribeiro","Cardoso",
    "Goncalves","Teixeira","Siqueira","Figueiredo","Andrade","Freitas","Pacheco","Campos",
    "Rocha","Araujo","Oliveira","Silveira","Batista","Machado","Moreira","Queiroz",
    "Miranda","Rezende","Tavares","Vasconcelos","Moura","Cavalcanti"
]

_ACCENT_TABLE = str.maketrans(
    'ÁÀÂÃÄáàâãäÉÈÊËéèêëÍÌÎÏíìîïÓÒÔÕÖóòôõöÚÙÛÜúùûüÇç',
    'AAAAAaaaaaEEEEeeeeIIIIiiiiOOOOOoooooUUUUuuuuCc'
)


def norm_text(s: str) -> str:
    """Replica exata de norm_text() do Postgres: trim + translate (remove acentos) + colapsa espaços + lower."""
    return re.sub(r"\s+", " ", s.strip().translate(_ACCENT_TABLE)).lower()


def make_name(rng: random.Random) -> str:
    first = rng.choice(FIRST_NAMES)
    last1 = rng.choice(LAST_NAMES)
    if rng.random() < 0.35:
        last2 = rng.choice([x for x in LAST_NAMES if x != last1])
        return f"{first} {last1} {last2}"
    return f"{first} {last1}"


def _get_db_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
        connect_timeout=15,
    )


def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1) nomes distintos em events com perfil não-funcional — via psycopg2 (evita timeout do REST)
    functional_list = tuple(FUNCTIONAL_PROFILES)
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT user_name
                FROM public.events
                WHERE user_name IS NOT NULL
                  AND btrim(user_name) <> ''
                  AND lower(coalesce(user_profile, '')) NOT IN %s
            """, (functional_list,))
            real_names = sorted(row[0].strip() for row in cur.fetchall() if row[0].strip())
    finally:
        conn.close()

    print(f"[INFO] {len(real_names)} nomes distintos com perfil não-funcional em events.")

    # 2) mapeamentos já existentes — via psycopg2 (sem limite de 1000 linhas do REST)
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_name_real, user_name_norm, user_name_anon FROM public.user_anon_map")
            rows_map = cur.fetchall()
    finally:
        conn.close()

    mapped_norms = {r[1] for r in rows_map if r[1]}
    mapped_reals = {r[0] for r in rows_map if r[0]}
    used_anons   = {r[2] for r in rows_map if r[2]}

    # norms de TODOS os nomes reais do sistema (inclui perfis funcionais)
    # codinomes não podem colidir com nenhum nome real, independente de perfil
    conn = _get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT user_name FROM public.events
                WHERE user_name IS NOT NULL AND btrim(user_name) != ''
            """)
            all_real_names = {row[0].strip() for row in cur.fetchall() if row[0].strip()}
    finally:
        conn.close()

    real_norms = {norm_text(n) for n in all_real_names} | {norm_text(n) for n in mapped_reals}

    # 3) quais ainda não foram mapeados — pula norms bloqueadas pelo trigger
    missing = [
        (rn, norm_text(rn))
        for rn in real_names
        if norm_text(rn) not in mapped_norms
        and rn not in mapped_reals
        and norm_text(rn) not in BLOCKED_NORMS
    ]
    print(f"[INFO] {len(missing)} usuários sem mapeamento.")

    if not missing:
        print("[OK] user_anon_map já está atualizado.")
        return

    # 4) gera codinomes determinísticos por nome real
    inserts = []
    for real, norm in missing:
        rng = random.Random(f"{ANON_SEED}::{real}")

        for _ in range(300):
            candidate = make_name(rng)
            # rejeita se já é codinome de outra pessoa OU se colide com algum nome real
            if candidate not in used_anons and norm_text(candidate) not in real_norms:
                used_anons.add(candidate)
                inserts.append({
                    "user_name_real": real,
                    "user_name_anon": candidate,
                    "user_name_norm": norm,
                })
                break
        else:
            # fallback: sufixo numérico para garantir unicidade
            for suffix in range(10, 200):
                candidate = make_name(rng) + f" {suffix}"
                if candidate not in used_anons and norm_text(candidate) not in real_norms:
                    break
            used_anons.add(candidate)
            inserts.append({
                "user_name_real": real,
                "user_name_anon": candidate,
                "user_name_norm": norm,
            })

    # 5) insere em lotes — ignore_duplicates como rede de segurança
    BATCH = 500
    for i in range(0, len(inserts), BATCH):
        supabase.table("user_anon_map").upsert(inserts[i : i + BATCH], ignore_duplicates=True).execute()

    print(f"[OK] Inseridos {len(inserts)} novos mapeamentos em user_anon_map.")


if __name__ == "__main__":
    main()
