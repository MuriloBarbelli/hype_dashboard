"""
tools/update_user_anon_map.py

Popula user_anon_map com todos os usuários de events que ainda não têm
mapeamento de anonimização.

Colunas da tabela:
  user_name_real  — nome original
  user_name_anon  — codinome gerado
  user_name_norm  — replica norm_text() do Postgres: lower + trim + espaços colapsados
                    (é esse campo que vw_events_anon usa no JOIN)

Pode rodar quantas vezes quiser — idempotente.
"""

from dotenv import load_dotenv
load_dotenv()

import os
import random
import re
from supabase import create_client

SUPABASE_URL              = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ANON_SEED                 = os.environ["ANON_SEED"]

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


def norm_text(s: str) -> str:
    """Replica norm_text() do Postgres: lower + trim + espaços colapsados."""
    return re.sub(r"\s+", " ", s.strip()).lower()


def make_name(rng: random.Random) -> str:
    first = rng.choice(FIRST_NAMES)
    last1 = rng.choice(LAST_NAMES)
    if rng.random() < 0.35:
        last2 = rng.choice([x for x in LAST_NAMES if x != last1])
        return f"{first} {last1} {last2}"
    return f"{first} {last1}"


def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1) nomes distintos em events (não nulos, não vazios)
    rows = supabase.table("events").select("user_name").neq("user_name", None).execute().data
    real_names = sorted({
        r["user_name"].strip()
        for r in rows
        if r.get("user_name") and r["user_name"].strip()
    })
    print(f"[INFO] {len(real_names)} nomes distintos em events.")

    # 2) mapeamentos já existentes indexados por user_name_norm
    existing = supabase.table("user_anon_map").select("user_name_norm,user_name_anon").execute().data
    mapped_norms = {r["user_name_norm"] for r in existing}
    used_anons   = {r["user_name_anon"] for r in existing}

    # 3) quais ainda não foram mapeados
    missing = [(rn, norm_text(rn)) for rn in real_names if norm_text(rn) not in mapped_norms]
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
            if candidate not in used_anons:
                used_anons.add(candidate)
                inserts.append({
                    "user_name_real": real,
                    "user_name_anon": candidate,
                    "user_name_norm": norm,
                })
                break
        else:
            # fallback: sufixo numérico para garantir unicidade
            candidate = make_name(rng) + f" {rng.randint(10, 99)}"
            used_anons.add(candidate)
            inserts.append({
                "user_name_real": real,
                "user_name_anon": candidate,
                "user_name_norm": norm,
            })

    # 5) insere em lotes
    BATCH = 500
    for i in range(0, len(inserts), BATCH):
        supabase.table("user_anon_map").insert(inserts[i : i + BATCH]).execute()

    print(f"[OK] Inseridos {len(inserts)} novos mapeamentos em user_anon_map.")


if __name__ == "__main__":
    main()
