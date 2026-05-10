"""
Rebuild do event_audit_map para um mês específico.

Uso:
    python -m tools.rebuild_audit --month 2026-05
    python -m tools.rebuild_audit --month 2026-04 --slack 30

É seguro reiniciar (UPSERT via ON CONFLICT). Se interrompido, retoma
do início do mês — os source_files já processados serão sobrescritos
com os mesmos valores (idempotente).
"""
import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from src.db import build_event_audit_map_for_source_file  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


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


def _fetch_source_files_for_month(conn, year: int, month: int) -> list:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT source_file
            FROM public.events
            WHERE DATE_TRUNC('month', event_timestamp AT TIME ZONE 'UTC') = %(month_start)s
              AND source_file IS NOT NULL
              AND btrim(source_file) <> ''
            ORDER BY source_file
        """, {"month_start": date(year, month, 1)})
        return [row[0] for row in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser(
        description="Rebuild event_audit_map para um mês específico."
    )
    parser.add_argument(
        "--month", required=True, metavar="YYYY-MM",
        help="Mês a processar (ex: 2026-05)"
    )
    parser.add_argument(
        "--slack", type=int, default=30,
        help="Folga em segundos para match de passagens (padrão: 30)"
    )
    args = parser.parse_args()

    try:
        parts = args.month.split("-")
        year, month = int(parts[0]), int(parts[1])
        if not (1 <= month <= 12):
            raise ValueError("mês fora do intervalo")
    except (ValueError, IndexError):
        log.error("--month deve estar no formato YYYY-MM (ex: 2026-05)")
        sys.exit(1)

    log.info("=== Rebuild audit map — mes: %s (slack=%ds) ===", args.month, args.slack)

    conn = _get_db_conn()
    try:
        source_files = _fetch_source_files_for_month(conn, year, month)
        total = len(source_files)

        if total == 0:
            log.info("Nenhum source_file encontrado para %s. Nada a fazer.", args.month)
            return

        log.info("%d source_files para processar.", total)

        for i, sf in enumerate(source_files, start=1):
            log.info("[%d/%d] %s", i, total, sf)
            build_event_audit_map_for_source_file(sf, slack_seconds=args.slack, conn=conn)
            log.info("[%d/%d] OK", i, total)

        log.info("=== Concluido: %d/%d source_files processados para %s ===",
                 total, total, args.month)

    except KeyboardInterrupt:
        log.warning("Interrompido pelo usuario. Rode novamente para continuar (UPSERT e idempotente).")
        sys.exit(130)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
