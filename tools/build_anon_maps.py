from dotenv import load_dotenv
load_dotenv()

import os
import random
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ANON_SEED = os.environ["ANON_SEED"]

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

def make_name(rng: random.Random) -> str:
    first = rng.choice(FIRST_NAMES)
    last1 = rng.choice(LAST_NAMES)
    if rng.random() < 0.35:
        last2 = rng.choice([x for x in LAST_NAMES if x != last1])
        return f"{first} {last1} {last2}"
    return f"{first} {last1}"

def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1) nomes reais
    rows = supabase.table("events").select("user_name").neq("user_name", None).execute().data
    real_names = sorted({r["user_name"].strip() for r in rows if r.get("user_name")})

    existing = supabase.table("user_pseudonym_map").select("*").execute().data
    real_to_anon = {r["user_name_real"]: r["user_name_anon"] for r in existing}
    used_anons = set(real_to_anon.values())

    inserts = []
    for rn in real_names:
        if rn in real_to_anon:
            continue

        rng = random.Random(f"{ANON_SEED}::{rn}")

        for _ in range(300):
            candidate = make_name(rng)
            if candidate not in used_anons:
                used_anons.add(candidate)
                inserts.append({"user_name_real": rn, "user_name_anon": candidate})
                break
        else:
            candidate = make_name(rng) + f" {rng.randint(10, 99)}"
            inserts.append({"user_name_real": rn, "user_name_anon": candidate})

    if inserts:
        supabase.table("user_pseudonym_map").insert(inserts).execute()
        print(f"[OK] Inseridos {len(inserts)} pseudônimos.")
    else:
        print("[OK] Nenhum pseudônimo novo para inserir.")

    # 2) andares
    units = supabase.table("events").select("unit").neq("unit", None).execute().data
    floors_real = set()
    for r in units:
        u = r.get("unit")
        if not u:
            continue
        digits = "".join(ch for ch in str(u) if ch.isdigit())
        if len(digits) <= 2:
            continue
        floors_real.add(int(digits[:-2]))

    floors_real = sorted(floors_real)

    existing_f = supabase.table("floor_map").select("*").execute().data
    fmap = {r["floor_real"]: r["floor_anon"] for r in existing_f}
    used_floors = set(fmap.values())

    rng = random.Random(f"{ANON_SEED}::floors")
    shuffled = floors_real[:]
    rng.shuffle(shuffled)

    inserts_f = []
    for fr, fa in zip(floors_real, shuffled):
        if fr in fmap:
            continue
        if fa in used_floors:
            continue
        used_floors.add(fa)
        inserts_f.append({"floor_real": fr, "floor_anon": fa})

    if inserts_f:
        supabase.table("floor_map").insert(inserts_f).execute()
        print(f"[OK] Inseridos {len(inserts_f)} andares.")
    else:
        print("[OK] Nenhum andar novo para inserir.")

if __name__ == "__main__":
    main()
