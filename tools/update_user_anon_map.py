"""
Anonimização de nomes de usuários.

- Nomes são gerados aleatoriamente (nome + sobrenome brasileiros)
- Não há inferência de gênero por enquanto
- A geração é determinística via ANON_SEED
- O mesmo user_name_real sempre gera o mesmo codinome
"""

from dotenv import load_dotenv
load_dotenv()

import os
import random
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
ANON_SEED = os.environ["ANON_SEED"]

# Lista simples e bem "Brasil"
FIRST_NAMES = [
    "Adriana","Aline","Amanda","Ana","Andressa","Beatriz","Bianca","Bruna","Camila","Carolina",
    "Catarina","Claudia","Daniela","Eduarda","Elaine","Emanuelle","Fabiana","Fernanda","Flavia","Gabriela",
    "Giovana","Helena","Isabela","Janaína","Jaqueline","Jessica","Julia","Juliana","Karen","Larissa",
    "Leticia","Livia","Luana","Luciana","Mariana","Marina","Mayara","Monique","Natalia","Paula",
    "Priscila","Rafaela","Raquel","Renata","Sabrina","Tatiana","Vanessa","Vitoria","Yasmin",

    "Alexandre","Anderson","Andre","Antonio","Arthur","Bernardo","Bruno","Caio","Carlos","Daniel",
    "David","Diego","Eduardo","Felipe","Fernando","Francisco","Gabriel","Guilherme","Gustavo","Henrique",
    "Igor","Joao","Jorge","Jose","Leonardo","Lucas","Marcelo","Marcos","Matheus","Murilo",
    "Nicolas","Paulo","Pedro","Rafael","Ricardo","Rodrigo","Samuel","Thiago","Vitor","Vinicius"
]

LAST_NAMES = [
    "Albuquerque","Almeida","Andrade","Araujo","Barbosa","Barros","Batista","Cardoso","Carvalho","Castro",
    "Cavalcanti","Coelho","Correia","Costa","Cruz","Dias","Duarte","Ferraz","Fernandes","Ferreira",
    "Figueiredo","Freitas","Gomes","Goncalves","Lima","Machado","Marques","Martins","Medeiros","Menezes",
    "Miranda","Monteiro","Moura","Nogueira","Oliveira","Pacheco","Pereira","Ramos","Rezende","Ribeiro",
    "Rocha","Santana","Santos","Silva","Siqueira","Teixeira","Tavares","Vasconcelos","Vieira"
]

def make_fake_name(rng: random.Random) -> str:
    first = rng.choice(FIRST_NAMES)
    last1 = rng.choice(LAST_NAMES)
    # 40% de chance de ter 2 sobrenomes
    if rng.random() < 0.40:
        last2 = rng.choice([x for x in LAST_NAMES if x != last1])
        return f"{first} {last1} {last2}"
    return f"{first} {last1}"

def norm(s: str) -> str:
    return " ".join(s.strip().split())

def main():
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # 1) pega nomes distintos do events (sem vazios)
    rows = (
        supabase.table("events")
        .select("user_name")
        .neq("user_name", None)
        .execute()
        .data
    )
    real_names = sorted({norm(r["user_name"]) for r in rows if r.get("user_name") and norm(r["user_name"]) != ""})

    # 2) pega mapping já existente
    existing = supabase.table("user_anon_map").select("*").execute().data
    real_to_anon = {norm(r["user_name_real"]): r["user_name_anon"] for r in existing}
    used_anon = set(real_to_anon.values())

    inserts = []
    for rn in real_names:
        if rn in real_to_anon:
            continue

        rng = random.Random(f"{ANON_SEED}::user::{rn}")

        # tenta achar um nome que ainda não foi usado
        anon_name = None
        for _ in range(600):
            candidate = make_fake_name(rng)
            if candidate not in used_anon:
                anon_name = candidate
                used_anon.add(candidate)
                break

        # fallback raríssimo: adiciona sufixo
        if anon_name is None:
            anon_name = make_fake_name(rng) + f" {rng.randint(10, 99)}"
            used_anon.add(anon_name)

        inserts.append({"user_name_real": rn, "user_name_anon": anon_name})

    if inserts:
        supabase.table("user_anon_map").insert(inserts).execute()
        print(f"[OK] Inseridos {len(inserts)} novos nomes no user_anon_map.")
    else:
        print("[OK] Nenhum nome novo para inserir (já está atualizado).")

if __name__ == "__main__":
    main()
