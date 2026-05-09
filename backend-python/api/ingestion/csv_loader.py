"""
CSV loader targeting PostgreSQL via asyncpg.

Flow for each CSV file:
  1. Read with csv.DictReader.
  2. Validate each row with the matching Pydantic model from models.py.
     Skip invalid rows and log the count.
  3. Bulk-insert validated rows with pool.executemany() using $1/$2... params.
  4. Write one data_lineage row per successful batch.

Functions
─────────
  load_csv(pool, table, filepath) → int   rows inserted
  load_all(pool)                          calls load_csv for all 6 tables
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Any

import asyncpg
from pydantic import ValidationError

from .models import CSV_MODEL_MAP

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", "data/csv"))

# Maps table name to its CSV filename (without directory)
_TABLE_FILES: dict[str, str] = {
    "movies":               "movies.csv",
    "viewers":              "viewers.csv",
    "watch_activity":       "watch_activity.csv",
    "reviews":              "reviews.csv",
    "marketing_spend":      "marketing_spend.csv",
    "regional_performance": "regional_performance.csv",
}


async def load_csv(pool: asyncpg.Pool, table: str, filepath: str) -> int:
    """
    Read a CSV file, validate each row, and bulk-insert into ``table``.

    Returns:
        Number of rows successfully inserted.
    """
    if table not in CSV_MODEL_MAP:
        raise ValueError(f"No model registered for table '{table}'")

    model_cls, columns = CSV_MODEL_MAP[table]
    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
    insert_sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    )

    valid_rows: list[tuple[Any, ...]] = []
    skipped = 0

    with open(filepath, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for raw_row in reader:
            cleaned = {k.strip(): (v.strip() if v else v) for k, v in raw_row.items()}
            try:
                obj = model_cls(**cleaned)
            except (ValidationError, TypeError) as exc:
                logger.debug("Row validation failed in %s: %s", filepath, exc)
                skipped += 1
                continue

            valid_rows.append(tuple(getattr(obj, col) for col in columns))

    if skipped:
        logger.warning("Skipped %d invalid rows in %s", skipped, filepath)

    if not valid_rows:
        logger.info("No valid rows found in %s", filepath)
        return 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(insert_sql, valid_rows)
            await conn.execute(
                """
                INSERT INTO data_lineage (table_name, source_file, source_user_id)
                VALUES ($1, $2, $3)
                """,
                table,
                str(filepath),
                "system:csv_loader",
            )

    logger.info("Loaded %d rows into '%s' from %s", len(valid_rows), table, filepath)
    return len(valid_rows)


async def load_all(pool: asyncpg.Pool) -> None:
    """Load all 6 known CSV files into their respective tables."""
    total = 0
    for table, filename in _TABLE_FILES.items():
        path = DATA_DIR / filename
        if not path.exists():
            logger.warning("CSV file not found, skipping: %s", path)
            continue
        rows = await load_csv(pool, table, str(path))
        total += rows
    logger.info("load_all complete: %d rows inserted total", total)
