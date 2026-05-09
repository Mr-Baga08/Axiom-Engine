"""
Database connection factory.

Returns a live connection object appropriate for the current DB_BACKEND.
Callers receive the same interface (execute / executemany / commit) because
both DuckDB's Python API and psycopg2 implement the DB-API 2.0 spec.

This module owns no state — it creates a new connection on every call.
For production use, wrap this inside a connection pool (e.g. asyncpg pool
or psycopg2 connection pool). That upgrade is out of scope for Phase 1.
"""

from __future__ import annotations

import os


def get_db_connection():
    """
    Return a DB-API 2.0 connection to either DuckDB or PostgreSQL,
    depending on the DB_BACKEND environment variable.

    DuckDB:
        - File-based; created automatically if it does not exist.
        - Enables 'httpfs' and 'json' extensions for ASOF joins and JSON ops.

    PostgreSQL:
        - Uses psycopg2 (sync) for ingestion scripts and migrations.
        - For async FastAPI routes, use asyncpg directly (not via this factory).

    Raises:
        ValueError: if DB_BACKEND is not 'duckdb' or 'postgres'.
        RuntimeError: if a required env variable is not set.
    """
    backend = os.getenv("DB_BACKEND", "duckdb").lower()

    if backend == "duckdb":
        import duckdb
        import pathlib

        path = os.getenv("DUCKDB_PATH", "data/duckdb/analytics.duckdb")
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)

        conn = duckdb.connect(path)
        # Load extensions used by the analytical layer
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        conn.execute("INSTALL json;  LOAD json;")
        return conn

    if backend == "postgres":
        import psycopg2

        dsn = os.getenv("POSTGRES_DSN")
        if not dsn:
            raise RuntimeError("POSTGRES_DSN is not set in .env")
        return psycopg2.connect(dsn)

    raise ValueError(
        f"Unknown DB_BACKEND: {backend!r}. Set to 'duckdb' or 'postgres'."
    )


# ── Async PostgreSQL pool (FastAPI) ────────────────────────────────────────────
# Used by FastAPI route handlers — NOT by the ingestion scripts above.
# Initialised once at app startup; stored on app.state.db_pool.

async def create_async_pool():
    """
    Create an asyncpg connection pool for use in FastAPI.
    Only call this when DB_BACKEND=postgres.
    Call this in the FastAPI lifespan event.
    """
    import asyncpg

    dsn = os.getenv("POSTGRES_DSN")
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is not set in .env")
    return await asyncpg.create_pool(dsn, min_size=2, max_size=10)