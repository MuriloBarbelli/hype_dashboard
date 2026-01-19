from dotenv import load_dotenv
load_dotenv()

import os
import random
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ANON_SEED = os.environ["ANON_SEED"]

NR_FLOORS = list(range(1, 4))      # 1..3
RES_FLOORS = list(range(4, 18))    # 4..17

def norm(s: str) -> str:
    return " ".join(s.strip().split())

def only_digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())

def is_nr(unit_group: str) -> bool:
    return "NR" in (unit_group or "").upper()

def parse_unit(unit_text: str):
    """
    Espera algo como 'Apartamento 1305' ou '1305'.
    Retorna (floor:int, suffix:str(2)) ou None.
    """
    digits = only_digits(unit_text)
    if not digits:
        return None

    # regra especial: "000"
    if digits == "000":
        return ("000", "00")

    if len(digits) < 3:
        return None

    floor = int(digits[:-2])
    suffix = digits[-2:]
    return (floor, suffix)

def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1) pega unidades distintas do events
    rows = (
        supabase.table("events")
        .select("unit_group,unit")
        .neq("unit", None)
        .neq("unit_group", None)
        .execute()
        .data
    )

    units = set()
    for r in rows:
        ug = norm(r["unit_group"])
        u = norm(r["unit"])
        if not ug or not u:
            continue
        units.add((ug, u))

    # 2) existing map
    existing = supabase.table("unit_anon_map").select("*").execute().data
    existing_keys = {(norm(r["unit_group"]), norm(r["unit_real"])) for r in existing}
    used_anon = {(norm(r["unit_group"]), norm(r["unit_anon"])) for r in existing}

    # 3) organiza por unit_group
    by_group = {}
    for ug, u in units:
        by_group.setdefault(ug, []).append(u)

    inserts = []

    for ug, unit_list in by_group.items():
        # unidades novas que ainda não estão mapeadas
        new_units = [u for u in sorted(set(unit_list)) if (ug, u) not in existing_keys]
        if not new_units:
            continue

        # separa unidades "000" e unidades reais
        new_000 = [u for u in new_units if only_digits(u) == "000"]
        new_real = [u for u in new_units if only_digits(u) != "000"]

        # 3.1) mapeia "000" -> "Apartamento 000"
        for u in new_000:
            inserts.append({
                "unit_group": ug,
                "unit_real": u,
                "unit_anon": "Apartamento 000",
            })
            used_anon.add((ug, "Apartamento 000"))

        # 3.2) mapeia os andares por permutação dentro da faixa correta
        parsed = []
        floors_set = set()
        for u in new_real:
            pu = parse_unit(u)
            if not pu:
                continue
            floor, suffix = pu
            if floor == "000":
                continue
            parsed.append((u, floor, suffix))
            floors_set.add(floor)

        floors = sorted(floors_set)
        if not floors:
            continue

        allowed = NR_FLOORS if is_nr(ug) else RES_FLOORS

        # embaralha allowed de forma determinística por unit_group
        rng = random.Random(f"{ANON_SEED}::unitfloors::{ug}")
        allowed_shuffled = allowed[:]
        rng.shuffle(allowed_shuffled)

        # cria um mapeamento floor_real -> floor_anon (sem repetir)
        # se tiver menos floors do que allowed, pega um slice do tamanho necessário
        if len(floors) > len(allowed_shuffled):
            # caso extremo (não deveria acontecer em NR/RES), cai em escolha com repetição
            floor_map = {fr: rng.choice(allowed) for fr in floors}
        else:
            floor_map = {fr: allowed_shuffled[i] for i, fr in enumerate(floors)}

        # agora cria unit_anon mantendo suffix
        for u, floor, suffix in parsed:
            floor_anon = floor_map[floor]
            unit_num = f"{floor_anon}{suffix}"
            unit_anon = f"Apartamento {unit_num}"

            # evita colisão dentro do mesmo grupo
            if (ug, unit_anon) in used_anon:
                # tenta outros floors permitidos
                for _ in range(200):
                    fa = rng.choice(allowed)
                    unit_num = f"{fa}{suffix}"
                    unit_anon = f"Apartamento {unit_num}"
                    if (ug, unit_anon) not in used_anon:
                        break

            used_anon.add((ug, unit_anon))
            inserts.append({
                "unit_group": ug,
                "unit_real": u,
                "unit_anon": unit_anon
            })

    if inserts:
        supabase.table("unit_anon_map").insert(inserts).execute()
        print(f"[OK] Inseridos {len(inserts)} novos apartamentos no unit_anon_map.")
    else:
        print("[OK] Nenhum apartamento novo para inserir (já está atualizado).")

if __name__ == "__main__":
    main()
