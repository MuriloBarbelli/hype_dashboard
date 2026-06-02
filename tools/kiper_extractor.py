#!/usr/bin/env python3
"""
tools/kiper_extractor.py

Baixa relatórios diários do Kiper (GraphQL) e ingere no banco.

Variáveis de ambiente (ou .env na raiz do projeto):
    KIPER_AUTHORIZATION         - Bearer token (sem o prefixo "Bearer ")
    KIPER_REFRESH               - Cookie kiper-refresh
    KIPER_TOPCONTEXTID          - Header topcontextid
    KIPER_TOPNODEID             - Header topnodeid (também usado como filterId 7)
    KIPER_USERCONTEXTID         - Header usercontextid
    KIPER_USERPARTNERCONTEXTID  - Header userpartnercontextid
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, DB_SSLMODE

Uso:
    python -m tools.kiper_extractor --start 2026-01-01 --end 2026-04-30
    python -m tools.kiper_extractor --days 30   # últimos 30 dias completos (exclui hoje)
"""

import argparse
import io
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import boto3
import psycopg2
import requests
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from psycopg2.extras import execute_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from src.ingest import normalize_kiper_csv, read_kiper_csv  # noqa: E402
from src.db import build_event_audit_map_for_source_file   # noqa: E402

# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

KIPER_URL     = "https://server-monitoring.cloud.kiper.com.br/graphql"
KIPER_APP_KEY = "91530d8e55134111b7c422e495d199bc"
REPORT_ID     = 2       # relatório de eventos
IS_CUSTOM     = False   # flag exigida pela API

COGNITO_CLIENT_ID = "6rqnt74rprdf0qeuvcsf1rh4f0"
COGNITO_REGION    = "us-west-2"

# filterId 9 = data inicial | filterId 10 = data final | filterId 7 = topnodeid
FILTER_ID_START   = "9"
FILTER_ID_END     = "10"
FILTER_ID_TOPNODE = "7"

DATE_FMT = "%Y-%m-%d %H:%M"   # formato aceito pela API: "2026-04-01 00:00"

CSV_DIR = ROOT / "relatorios_csv"

# O operationName precisa bater com o nome da mutation declarada no query
MUTATION = """
mutation generateReportCsv($reportId: Int!, $isCustom: Boolean!, $filters: String) {
  generateReportCsv(reportId: $reportId, isCustom: $isCustom, filters: $filters) {
    data
  }
}
"""

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Auth / headers
# ──────────────────────────────────────────────────────────────────────────────

def _cognito_login() -> str:
    """Autentica com KIPER_EMAIL + KIPER_PASSWORD via Cognito USER_PASSWORD_AUTH."""
    email    = _require_env("KIPER_EMAIL")
    password = _require_env("KIPER_PASSWORD")

    client = boto3.client(
        "cognito-idp",
        region_name=COGNITO_REGION,
        config=Config(signature_version=UNSIGNED),
    )
    try:
        resp = client.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
            ClientId=COGNITO_CLIENT_ID,
        )
    except ClientError as exc:
        raise RuntimeError(f"Cognito login falhou: {exc}") from exc

    token = resp["AuthenticationResult"]["AccessToken"]
    log.info("Cognito login OK — token obtido para %s", email)
    return token


def _get_token() -> str:
    raw = (os.environ.get("KIPER_AUTHORIZATION") or "").strip()
    if raw and raw.upper() != "DISABLED" and raw.lower().startswith("bearer "):
        return raw.removeprefix("Bearer ").removeprefix("bearer ").strip()
    return _cognito_login()


def _require_env(key: str) -> str:
    val = os.environ.get(key) or ""
    if not val:
        raise RuntimeError(f"{key} não encontrado. Defina no .env.")
    return val.strip()


def _build_headers() -> dict:
    return {
        "Authorization":          f"Bearer {_get_token()}",
        "Applicationkey":         KIPER_APP_KEY,
        "Content-Type":           "application/json",
        "topcontextid":           _require_env("KIPER_TOPCONTEXTID"),
        "topnodeid":              _require_env("KIPER_TOPNODEID"),
        "usercontextid":          _require_env("KIPER_USERCONTEXTID"),
        "userpartnercontextid":   _require_env("KIPER_USERPARTNERCONTEXTID"),
    }


def _build_cookies() -> dict:
    cookies = {"kiper-authorization": _get_token()}
    refresh = os.environ.get("KIPER_REFRESH") or ""
    if refresh:
        cookies["kiper-refresh"] = refresh
    return cookies


# ──────────────────────────────────────────────────────────────────────────────
# GraphQL → CSV string
# ──────────────────────────────────────────────────────────────────────────────

def _build_filters(day: date) -> str:
    """
    Retorna a string JSON com os 3 filtros exigidos pela API:
      filterId 9  → data inicial  "YYYY-MM-DD 00:00"
      filterId 10 → data final    "YYYY-MM-DD 23:59"
      filterId 7  → topnodeid     (inteiro, ID do condomínio)
    """
    topnode = int(_require_env("KIPER_TOPNODEID"))
    return json.dumps([
        {"filterId": FILTER_ID_END,     "value": day.strftime(DATE_FMT).replace("00:00", "23:59")},
        {"filterId": FILTER_ID_START,   "value": day.strftime(DATE_FMT)},
        {"filterId": FILTER_ID_TOPNODE, "value": topnode},
    ])


def fetch_kiper_csv(day: date) -> str:
    """
    Chama a mutation generateReportCsv para um dia completo e retorna o CSV.
    A API devolve data.generateReportCsv.data como lista de inteiros (bytes ASCII).
    """
    filters_str = _build_filters(day)

    payload = {
        "operationName": "generateReportCsv",
        "query":         MUTATION,
        "variables": {
            "reportId": REPORT_ID,
            "isCustom": IS_CUSTOM,
            "filters":  filters_str,
        },
    }

    log.debug("[%s] POST %s  filters=%s", day, KIPER_URL, filters_str)

    resp = requests.post(
        KIPER_URL,
        headers=_build_headers(),
        cookies=_build_cookies(),
        json=payload,
        timeout=120,
    )

    if resp.status_code == 401:
        raise RuntimeError(
            "HTTP 401 — token expirado ou inválido. "
            "Atualize KIPER_AUTHORIZATION no .env."
        )
    resp.raise_for_status()

    body = resp.json()

    if "errors" in body:
        err = body["errors"]
        for e in err:
            error_type = str(e.get("errorType", "")).upper()
            message    = str(e.get("message", "")).upper()
            if "UNAUTHENTICATED" in error_type or "UNAUTHENTICATED" in message:
                raise RuntimeError(
                    f"UNAUTHENTICATED: token inválido ou expirado. "
                    f"{e.get('message', '')} — atualize KIPER_AUTHORIZATION "
                    f"ou defina KIPER_EMAIL + KIPER_PASSWORD no .env."
                )
        # tenta decodificar buffer de erro do .NET
        for e in err:
            ext = e.get("extensions", {})
            raw = ext.get("response", "")
            if isinstance(raw, dict) and raw.get("type") == "Buffer":
                detail = bytes(raw["data"]).decode("utf-8", errors="replace")
                raise RuntimeError(f"Kiper backend error: {detail[:300]}")
        raise RuntimeError(f"Kiper GraphQL errors: {err}")

    byte_array = body["data"]["generateReportCsv"]["data"]

    if not byte_array:
        return ""

    # byte_array é lista de ints (bytes UTF-8) — decodifica direto, sem chr() por byte
    return bytes(byte_array).decode("utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# Nomes de arquivo (padrão existente no projeto)
# ──────────────────────────────────────────────────────────────────────────────

def _csv_filename(day: date) -> str:
    """eventsReport - 2026-04-01 00_00_2026-04-01 23_59.csv"""
    d = day.strftime("%Y-%m-%d")
    return f"eventsReport - {d} 00_00_{d} 23_59.csv"


def _csv_path(day: date) -> Path:
    return CSV_DIR / _csv_filename(day)


# ──────────────────────────────────────────────────────────────────────────────
# Banco de dados (standalone, sem st.secrets)
# ──────────────────────────────────────────────────────────────────────────────

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


def _source_files_in_db(conn) -> set:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT source_file
            FROM public.events
            WHERE source_file IS NOT NULL AND btrim(source_file) <> ''
        """)
        return {row[0] for row in cur.fetchall()}


def scan_missing_days(conn, start: date, end: date) -> list:
    """
    Consulta o banco e retorna os dias do intervalo [start, end] que NÃO têm
    nenhum evento registrado (baseado em event_timestamp, não em source_file).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT DATE(event_timestamp) FROM public.events "
            "WHERE DATE(event_timestamp) BETWEEN %s AND %s",
            (start, end),
        )
        days_in_db = {row[0] for row in cur.fetchall()}

    total = (end - start).days + 1
    return [
        start + timedelta(days=i)
        for i in range(total)
        if (start + timedelta(days=i)) not in days_in_db
    ]


def _insert_events(conn, df) -> int:
    if df.empty:
        return 0

    import pandas as pd
    df_clean = df.copy().astype(object).where(pd.notnull(df), None)
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

    with conn.cursor() as cur:
        execute_values(cur, sql, rows, page_size=2000)
    conn.commit()
    return len(rows)


def _refresh_views(conn) -> None:
    views = [
        "public.mv_passages_v5",
        "public.mv_passages_v6",
        "public.mv_passage_classification_v5",
    ]
    with conn.cursor() as cur:
        # Desabilita statement_timeout: os REFRESHes podem levar vários minutos
        cur.execute("SET statement_timeout = 0")
        for view in views:
            log.info("  Refreshing %s...", view)
            cur.execute(f"REFRESH MATERIALIZED VIEW {view}")
    try:
        conn.commit()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Ingestão de um CSV salvo em disco
# ──────────────────────────────────────────────────────────────────────────────

def ingest_csv_file(conn, csv_path: Path) -> int:
    with open(csv_path, "rb") as raw:
        buf = io.BytesIO(raw.read())

    df_raw = read_kiper_csv(buf)
    n_raw = len(df_raw)
    df_norm = normalize_kiper_csv(df_raw, source_file=csv_path.name)
    inserted = _insert_events(conn, df_norm)
    log.info("  %s: %d lidos → %d inseridos", csv_path.name, n_raw, inserted)
    return inserted


# ──────────────────────────────────────────────────────────────────────────────
# Pipeline principal
# ──────────────────────────────────────────────────────────────────────────────

def run(start: date, end: date, days_override: list = None) -> None:
    """
    Baixa e ingere dias do intervalo [start, end].

    days_override: se fornecido, processa exatamente esses dias (usado pelo
    modo --fill após scan_missing_days) em vez de derivar a lista do intervalo.
    """
    today = date.today()

    # Nunca baixar o dia atual (dados incompletos)
    if end >= today:
        new_end = today - timedelta(days=1)
        log.warning("end=%s inclui hoje — ajustando para %s (dia incompleto ignorado)", end, new_end)
        end = new_end

    if end < start:
        log.info("Nada a fazer: intervalo vazio após ajuste de data.")
        return

    CSV_DIR.mkdir(parents=True, exist_ok=True)
    conn = _get_db_conn()

    try:
        if days_override is not None:
            # --fill: lista já vem filtrada por scan_missing_days
            all_days = [d for d in days_override if d <= end]
            disk_only_days = [d for d in all_days if _csv_path(d).exists()]
            fetch_days     = [d for d in all_days if not _csv_path(d).exists()]
            log.info(
                "Modo fill — %d dias faltantes | %d só em disco | %d para baixar",
                len(all_days), len(disk_only_days), len(fetch_days),
            )
        else:
            already_ingested = _source_files_in_db(conn)
            log.info("Source-files já no banco: %d", len(already_ingested))

            total_days = (end - start).days + 1
            all_days = [start + timedelta(days=i) for i in range(total_days)]

            skip_days, disk_only_days, fetch_days = [], [], []
            for day in all_days:
                fname = _csv_filename(day)
                if fname in already_ingested:
                    skip_days.append(day)
                elif _csv_path(day).exists():
                    disk_only_days.append(day)
                else:
                    fetch_days.append(day)

            log.info(
                "Período %s → %s: %d dias | %d já no banco | %d só em disco | %d para baixar",
                start, end, total_days, len(skip_days), len(disk_only_days), len(fetch_days),
            )

        new_files: list[Path] = []

        # ── 1. CSV em disco mas ainda não ingerido ─────────────────────────
        for day in disk_only_days:
            log.info("[%s] CSV em disco, ingerindo diretamente…", day)
            new_files.append(_csv_path(day))

        # ── 2. Download dos dias ausentes ──────────────────────────────────
        for day in fetch_days:
            log.info("[%s] Baixando do Kiper (00:00 – 23:59)…", day)
            try:
                csv_text = fetch_kiper_csv(day)
            except RuntimeError as exc:
                if "UNAUTHENTICATED" in str(exc):
                    raise
                log.error("[%s] ERRO ao baixar: %s", day, exc)
                continue
            except Exception as exc:
                log.error("[%s] ERRO ao baixar: %s", day, exc)
                continue

            if not csv_text.strip():
                log.warning("[%s] Resposta vazia — nenhum evento neste dia.", day)
                continue

            csv_path = _csv_path(day)
            csv_path.write_text(csv_text, encoding="utf-8")
            event_lines = max(0, csv_text.count("\n") - 1)
            log.info("[%s] Salvo: %s  (~%d eventos)", day, csv_path.name, event_lines)
            new_files.append(csv_path)

        # ── 3. Ingestão ────────────────────────────────────────────────────
        if not new_files:
            log.info("Nada para ingerir.")
            return

        total_inserted = 0
        for csv_path in new_files:
            try:
                total_inserted += ingest_csv_file(conn, csv_path)
            except Exception as exc:
                log.error("ERRO ao ingerir %s: %s", csv_path.name, exc)
                conn.rollback()

        log.info("Total de eventos inseridos: %d", total_inserted)

        # ── 4. Refresh das materialized views ─────────────────────────────
        log.info("Atualizando materialized views…")
        _refresh_views(conn)
        log.info("Views atualizadas.")

        # ── 5. Rebuild do event_audit_map ──────────────────────────────────
        log.info("Atualizando event_audit_map…")
        for csv_path in new_files:
            log.info("  audit map: %s", csv_path.name)
            build_event_audit_map_for_source_file(csv_path.name, conn=conn)
        log.info("Event_audit_map atualizado.")

    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _parse_args():
    parser = argparse.ArgumentParser(
        description="Baixa relatórios diários do Kiper e ingere no banco. "
                    "O dia atual nunca é baixado (dados incompletos)."
    )

    # ── Intervalo de datas (obrigatório) ──────────────────────────────────
    range_group = parser.add_mutually_exclusive_group(required=True)
    range_group.add_argument(
        "--start", metavar="YYYY-MM-DD",
        help="Data inicial do período.",
    )
    range_group.add_argument(
        "--days", type=int, metavar="N",
        help="Últimos N dias completos (exclui hoje automaticamente).",
    )
    parser.add_argument(
        "--end", metavar="YYYY-MM-DD", default=None,
        help="Data final (padrão: ontem). Usado com --start.",
    )

    # ── Modos de operação (opcionais) ─────────────────────────────────────
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--scan", action="store_true",
        help="Varre o banco e lista os dias faltantes no intervalo. Não baixa nada.",
    )
    mode_group.add_argument(
        "--fill", action="store_true",
        help="Varre o banco e baixa apenas os dias faltantes (por event_timestamp).",
    )
    mode_group.add_argument(
        "--fill-until-complete", action="store_true",
        help=(
            "Roda --fill em loop até que todos os dias estejam no banco "
            "ou até atingir --max-attempts tentativas (padrão: 10). "
            "Aguarda 30 s entre cada rodada."
        ),
    )

    parser.add_argument(
        "--max-attempts", type=int, default=10, metavar="N",
        help="Número máximo de rodadas para --fill-until-complete (padrão: 10).",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    yesterday = date.today() - timedelta(days=1)

    if args.days:
        end   = yesterday
        start = end - timedelta(days=args.days - 1)
    else:
        start = date.fromisoformat(args.start)
        end   = date.fromisoformat(args.end) if args.end else yesterday

    if end < start:
        log.error("--end (%s) é anterior a --start (%s).", end, start)
        sys.exit(1)

    # ── --scan: só mostra, não baixa ──────────────────────────────────────
    if args.scan:
        conn = _get_db_conn()
        try:
            missing = scan_missing_days(conn, start, end)
        finally:
            conn.close()

        total = (end - start).days + 1
        print(f"Dias faltantes no banco: {len(missing)} de {total} ({start} a {end})")
        for d in missing:
            print(f"  {d}  {d.strftime('%A')}")
        sys.exit(0)

    # ── --fill: varre e baixa só os dias sem dados ─────────────────────────
    if args.fill:
        conn = _get_db_conn()
        try:
            missing = scan_missing_days(conn, start, end)
        finally:
            conn.close()

        total = (end - start).days + 1
        log.info(
            "Scan completo: %d dias faltantes de %d (%s → %s)",
            len(missing), total, start, end,
        )
        if not missing:
            log.info("Nenhum dia faltante. Banco já está completo para este intervalo.")
            sys.exit(0)

        run(start, end, days_override=missing)
        sys.exit(0)

    # ── --fill-until-complete: loop até zerar os faltantes ───────────────
    if args.fill_until_complete:
        max_attempts = args.max_attempts
        wait_secs    = 30
        total_days   = (end - start).days + 1
        round_num    = 0

        while round_num < max_attempts:
            round_num += 1

            conn = _get_db_conn()
            try:
                missing = scan_missing_days(conn, start, end)
            finally:
                conn.close()

            log.info(
                "[Rodada %d/%d] %d dias faltantes de %d (%s a %s)",
                round_num, max_attempts, len(missing), total_days, start, end,
            )

            if not missing:
                log.info("Banco completo para o periodo — concluido em %d rodada(s).", round_num - 1 if round_num > 1 else 1)
                break

            run(start, end, days_override=missing)

            if round_num < max_attempts:
                # Re-scan para verificar se ainda há dias faltantes antes de aguardar
                conn = _get_db_conn()
                try:
                    still_missing = scan_missing_days(conn, start, end)
                finally:
                    conn.close()

                if not still_missing:
                    log.info("Banco completo apos rodada %d.", round_num)
                    break

                log.info(
                    "Ainda faltam %d dia(s). Aguardando %ds antes da rodada %d...",
                    len(still_missing), wait_secs, round_num + 1,
                )
                time.sleep(wait_secs)

        # Resumo final
        conn = _get_db_conn()
        try:
            remaining = scan_missing_days(conn, start, end)
        finally:
            conn.close()

        print(f"\n=== Resumo fill-until-complete ===")
        print(f"Rodadas executadas : {round_num}")
        print(f"Dias no periodo    : {total_days} ({start} a {end})")
        print(f"Dias faltantes     : {len(remaining)}")
        if remaining:
            print("Dias ainda ausentes no banco:")
            for d in remaining:
                print(f"  {d}  {d.strftime('%A')}")
        else:
            print("Todos os dias estao no banco.")
        sys.exit(0)

    # ── modo padrão: baixa o intervalo completo (pula por source_file) ────
    run(start, end)
