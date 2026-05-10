"""
Audit logger — appends one row per significant action to audit_log.

Supports two DB backends:
  - asyncpg (PostgreSQL): async execute with $1/$2 parameters
  - DuckDB:               sync execute with ? parameters (dev mode)

This module never raises — a logging failure must never break a user request.
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from auth.jwt_handler import hash_query

logger = logging.getLogger(__name__)

_DUCKDB_SQL = """
    INSERT INTO audit_log
        (user_uid, user_role, action, resource, query_hash,
         ip_address, status, detail)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_PG_SQL = """
    INSERT INTO audit_log
        (user_uid, user_role, action, resource, query_hash,
         ip_address, status, detail)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""


async def record(
    db,
    *,
    token: dict[str, Any],
    action: str,
    resource: str,
    raw_query: str = "",
    ip_address: str | None = None,
    status: str = "success",
    detail: dict[str, Any] | None = None,
) -> None:
    """
    Insert one audit row. Fire-and-forget — never raises.
    Works with both asyncpg (PostgreSQL) and DuckDB connection objects.
    """
    if db is None:
        return

    params = (
        token.get("sub", "unknown"),
        token.get("role", "unknown"),
        action,
        resource,
        hash_query(raw_query) if raw_query else "",
        ip_address,
        status,
        json.dumps(detail or {}),
    )

    try:
        execute = getattr(db, "execute", None)
        if execute is None:
            return

        if inspect.iscoroutinefunction(execute):
            # asyncpg pool / connection — async, $1 placeholders
            await execute(_PG_SQL, *params)
        else:
            # DuckDB — sync, ? placeholders; audit_log may not exist in dev
            execute(_DUCKDB_SQL, list(params))

    except Exception as exc:
        logger.warning("audit_log insert failed: %s", exc)
