"""
tools/update_unit_anon_map.py

Atualiza a tabela public.unit_anon_map com mapeamento:
(unit_group, unit_real) -> unit_anon

✅ Regras:
- Só cria mapeamento para eventos que REALMENTE têm unit (não nulo e não vazio)
- "Apartamento 000" permanece "Apartamento 000"
- Mantém o bloco/setor:
    - NR: andares 1 a 3
    - RES: andares 4 a 17
- Mantém a prumada (últimos 2 dígitos) -> ex: 1702 mantém final 02
- Evita colisão: não deixa dois aptos reais virarem o mesmo apto anon no mesmo unit_group
- Pode rodar quantas vezes quiser (não duplica; só insere faltantes)
- Determinístico via ANON_SEED (mas colisões podem forçar tentativas extras)
"""

from dotenv import load_dotenv
load_dotenv()

import os
import re
import random
from supabase import create_client


SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ANON_SEED = os.environ.get("ANON_SEED", "seed_default_dev")


def clean_text(s: str) -> str:
    if s is None:
        return ""
    return " ".join(str(s).strip().split())


def extract_unit_num(unit_str: str) -> int | None:
    """
    Extrai número do texto tipo 'Apartamento 1305' -> 1305
    Se não achar dígitos, retorna None.
    """
    if not unit_str:
        return None
    digits = re.sub(r"\D", "", unit_str)
    if digits == "":
        return None
    return int(digits)


def unit_group_floor_range(unit_group: str) -> tuple[int, int]:
    """
    Define range (min_floor, max_floor) inclusive.
    """
    ug = (unit_group or "").upper()
    if "HYPE" in ug and "NR" in ug:
        return (1, 3)
    if "HYPE" in ug and "RES" in ug:
        return (4, 17)
    # qualquer outro grupo: mantém dentro de 1..17 (fallback)
    return (1, 17)


def build_anon_unit(unit_group: str, unit_real: str, used_anon_nums: set[int]) -> tuple[str, int]:
    """
    Gera unit_anon e unit_anon_num garantindo que unit_anon_num não colida
    dentro do mesmo unit_group.
    """
    unit_num = extract_unit_num(unit_real)

    # Se não deu pra extrair, devolve algo fixo sem expor (mas raro)
    if unit_num is None:
        # tenta gerar um "Apartamento 000" para não vazar nada
        return ("Apartamento 000", 0)

    # Apartamento 000 fica 000
    if unit_num == 0:
        return ("Apartamento 000", 0)

    prumada = unit_num % 100
    prumada_str = f"{prumada:02d}"

    min_floor, max_floor = unit_group_floor_range(unit_group)

    # RNG determinístico por (seed, group, unit_real)
    key = f"{ANON_SEED}::unit::{unit_group}::{unit_num}"
    rng = random.Random(key)

    # tenta várias vezes até achar um floor que não colida com outro apto anon
    for _ in range(500):
        floor_anon = rng.randint(min_floor, max_floor)
        anon_num = floor_anon * 100 + prumada
        if anon_num not in used_anon_nums:
            used_anon_nums.add(anon_num)
            return (f"Apartamento {anon_num}", anon_num)

    # fallback extremo: se colidiu demais, força um número com sufixo diferente (quase nunca)
    # (isso muda a prumada, mas só como último recurso)
    for _ in range(500):
        floor_anon = rng.randint(min_floor, max_floor)
        prumada_alt = rng.randint(1, 99)
        anon_num = floor_anon * 100 + prumada_alt
        if anon_num not in used_anon_nums:
            used_anon_nums.add(anon_num)
            return (f"Apartamento {anon_num}", anon_num)

    # último fallback absoluto
    return ("Apartamento 000", 0)


def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # -----------------------------
    # 1) Carrega mapeamentos existentes
    # -----------------------------
    existing_rows = supabase.table("unit_anon_map").select("*").execute().data or []
    existing_keys = set()  # (unit_group, unit_real_clean)
    used_by_group: dict[str, set[int]] = {}  # unit_group -> set(anon_num)

    for r in existing_rows:
        ug = clean_text(r.get("unit_group"))
        ur = clean_text(r.get("unit_real"))
        ua = clean_text(r.get("unit_anon"))
        if ug and ur:
            existing_keys.add((ug, ur))

        # registra anon_nums já usados por group, para evitar colisão
        anon_num = extract_unit_num(ua)
        if ug:
            used_by_group.setdefault(ug, set())
            if anon_num is not None:
                used_by_group[ug].add(anon_num)

    print(f"[INFO] unit_anon_map já tem {len(existing_rows)} linhas.")
    print("[INFO] Varredura em public.events para descobrir (unit_group, unit) únicos (com paginação)...")

    # -----------------------------
    # 2) Varre events paginando e junta units únicas
    # -----------------------------
    page_size = 1000
    offset = 0

    found = set()  # (unit_group_clean, unit_clean)

    while True:
        batch = (
            supabase.table("events")
            .select("unit_group,unit")
            .range(offset, offset + page_size - 1)
            .execute()
            .data
        ) or []

        if not batch:
            break

        for row in batch:
            ug = clean_text(row.get("unit_group"))
            unit = clean_text(row.get("unit"))
            if unit == "":
                continue
            if ug == "":
                # se não tiver grupo, ainda assim guarda (vai cair no fallback 1..17)
                ug = "SEM_GRUPO"
            found.add((ug, unit))

        offset += page_size
        if (offset // page_size) % 10 == 0:
            print(f"[INFO] ...páginas lidas: {offset//page_size} | units únicas coletadas: {len(found)}")

    print(f"[INFO] Total de (unit_group, unit) únicos encontrados em events: {len(found)}")

    # -----------------------------
    # 3) Monta inserts apenas do que está faltando
    # -----------------------------
    inserts = []
    for ug, unit_real in sorted(found):
        if (ug, unit_real) in existing_keys:
            continue

        used_anon_nums = used_by_group.setdefault(ug, set())
        unit_anon, anon_num = build_anon_unit(ug, unit_real, used_anon_nums)

        inserts.append(
            {
                "unit_group": ug,
                "unit_real": unit_real,
                "unit_anon": unit_anon,
            }
        )

    if not inserts:
        print("[OK] Nenhum apartamento novo para inserir (já está atualizado).")
        return

    supabase.table("unit_anon_map").insert(inserts).execute()
    print(f"[OK] Inseridos {len(inserts)} apartamentos no unit_anon_map.")


if __name__ == "__main__":
    main()
