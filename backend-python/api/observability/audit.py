"""
Audit logger — appends one row per significant action to the audit_log table.

Rules enforced at the DB level (see migration in Step 1):
    - The app role has INSERT + SELECT only. UPDATE and DELETE are revoked.
    - Rows cannot be modified after creation.

This module never raises — a logging failure must not break the user request.
Errors are written to the standard logger at WARNING level.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from auth.jwt_handler import hash_query

logger = logging.getLogger(__name__)


async def record(
    db,                          # your async DB connection / pool
    *,
    token: dict[str, Any],       # decoded JWT payload from verify_token
    action: str,                 # 'query' | 'ingest' | 'tool_call' | 'denied' | 'error'
    resource: str,               # 'document:42' | 'tool:render_chart' | 'route:/query'
    raw_query: str = "",         # raw user input — stored as hash only, never plain text
    ip_address: str | None = None,
    status: str = "success",     # 'success' | 'denied' | 'error'
    detail: dict[str, Any] | None = None,
) -> None:
    """
    Insert one row into audit_log.

    This function is a fire-and-forget coroutine — call it with `await` but
    do not let its failure propagate. Wrap callers in try/except if needed.

    The raw_query is hashed with SHA-256 before storage. The original text
    is never persisted.
    """
    try:
        await db.execute(
            """
            INSERT INTO audit_log
                (user_uid, user_role, action, resource, query_hash,
                 ip_address, status, detail)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            token.get("sub", "unknown"),
            token.get("role", "unknown"),
            action,
            resource,
            hash_query(raw_query) if raw_query else "",
            ip_address,
            status,
            json.dumps(detail or {}),
        )
    except Exception as exc:
        # Never crash the request over a logging failure
        logger.warning("audit_log insert failed: %s", exc)