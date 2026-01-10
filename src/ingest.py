import pandas as pd
import hashlib
from psycopg2.extras import execute_values
from src.db import get_conn

CSV_COLUMNS = [
    "Data do evento",
    "Data da finalização do tratamento",
    "Tipo do evento",
    "Descrição do evento",
    "Nome do accesso",
    "Nome do usuário",
    "Perfil do usuário",
    "Grupo de Unidade",
    "Unidade",
    "Perfil do atendente",
    "Nome do atendente",
    "Tratamento",
]

def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()

def read_kiper_csv(uploaded_file) -> pd.DataFrame:
    """
    Lê o CSV exportado do Kiper tentando os formatos mais comuns.
    """
    # uploaded_file é um stream do Streamlit. Vamos tentar algumas combinações.
    for sep in [",", ";"]:
        for enc in ["utf-8", "latin1"]:
            try:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, sep=sep, encoding=enc)
                if "Data do evento" in df.columns:
                    return df
            except Exception:
                pass

    raise ValueError("Não consegui ler o CSV (separador/encoding inesperado).")

def normalize_kiper_csv(df: pd.DataFrame, source_file: str) -> pd.DataFrame:
    missing = [c for c in CSV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV não tem colunas esperadas: {missing}")

    out = pd.DataFrame()

    # Datas
    out["event_timestamp"] = pd.to_datetime(df["Data do evento"], dayfirst=True, errors="coerce")
    out["treatment_finished_at"] = pd.to_datetime(df["Data da finalização do tratamento"], dayfirst=True, errors="coerce")

    # Código numérico (pode vir como texto)
    out["event_type_code"] = pd.to_numeric(df["Tipo do evento"], errors="coerce").astype("Int64")

    # Textos
    out["event_description"] = df["Descrição do evento"].astype("string")
    out["access_name"] = df["Nome do accesso"].astype("string")
    out["user_name"] = df["Nome do usuário"].astype("string")
    out["user_profile"] = df["Perfil do usuário"].astype("string")
    out["unit_group"] = df["Grupo de Unidade"].astype("string")
    out["unit"] = df["Unidade"].astype("string")
    out["handler_profile"] = df["Perfil do atendente"].astype("string")
    out["handler_name"] = df["Nome do atendente"].astype("string")
    out["treatment"] = df["Tratamento"].astype("string")

    # Remove linhas sem timestamp
    out = out.dropna(subset=["event_timestamp"]).copy()

    # event_id (dedup)
    key_cols = ["event_timestamp", "access_name", "event_description", "user_name", "unit", "event_type_code"]
    def make_key(row):
        parts = []
        for c in key_cols:
            v = row[c]
            parts.append("" if pd.isna(v) else str(v))
        return _sha1("|".join(parts))

    out["event_id"] = out.apply(make_key, axis=1)

    # metadados
    out["source_file"] = source_file

    # NA -> None
    out = out.where(pd.notnull(out), None)

    # ordem das colunas para INSERT
    out = out[[
        "event_id",
        "event_timestamp",
        "treatment_finished_at",
        "event_type_code",
        "event_description",
        "access_name",
        "user_name",
        "user_profile",
        "unit_group",
        "unit",
        "handler_profile",
        "handler_name",
        "treatment",
        "source_file",
    ]]

    return out

def insert_events(df_events: pd.DataFrame) -> int:
    if df_events.empty:
        return 0

    # 1) Garantia absoluta: tudo que for NA/NaN vira None
    df_clean = df_events.copy()

    # Converte pd.NA/NaN para None (robusto)
    df_clean = df_clean.astype(object).where(pd.notnull(df_clean), None)

    # 2) Monta rows já limpos (list of tuples)
    rows = [tuple(x) for x in df_clean.to_numpy()]

    sql = """
    INSERT INTO public.events (
        event_id, event_timestamp, treatment_finished_at, event_type_code,
        event_description, access_name, user_name, user_profile,
        unit_group, unit, handler_profile, handler_name, treatment, source_file
    )
    VALUES %s
    ON CONFLICT (event_id) DO NOTHING;
    """

    conn = get_conn()
    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=2000)

    conn.commit()
    return len(rows)