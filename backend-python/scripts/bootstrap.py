"""
Bootstrap script — run this once to set up the database and load all CSVs.

Usage:
    python -m python.scripts.bootstrap

For Docker:
    docker-compose exec api python -m python.scripts.bootstrap
"""

import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bootstrap")

# Load .env before anything else
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logger.warning("python-dotenv not installed — relying on shell environment only")


def run_duckdb_schema():
    from ..api.pipeline.db import get_db_connection

    schema_path = Path("data/duckdb/schema.sql")
    if not schema_path.exists():
        logger.error("DuckDB schema file not found: %s", schema_path)
        sys.exit(1)
    conn = get_db_connection()
    conn.execute(schema_path.read_text())
    conn.commit()
    conn.close()
    logger.info("DuckDB schema applied")


def run_postgres_init():
    import psycopg2

    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        logger.error("POSTGRES_DSN is not set")
        sys.exit(1)
    init_sql = Path("infra/postgres/init.sql").read_text()
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute(init_sql)
    conn.commit()
    conn.close()
    logger.info("PostgreSQL schema and RLS policies applied")


def load_csvs():
    from ..api.ingestion.csv_loader import load_all

    results = load_all()
    for stem, (inserted, quarantined) in results.items():
        logger.info("%-25s inserted=%d  quarantined=%d", stem, inserted, quarantined)


if __name__ == "__main__":
    backend = os.getenv("DB_BACKEND", "duckdb").lower()
    logger.info("Bootstrap starting (DB_BACKEND=%s)", backend)

    if backend == "duckdb":
        run_duckdb_schema()
    elif backend == "postgres":
        run_postgres_init()
    else:
        logger.error("Unknown DB_BACKEND: %s", backend)
        sys.exit(1)

    load_csvs()
    logger.info("Bootstrap complete")