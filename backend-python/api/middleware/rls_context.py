"""
RLS context middleware for FastAPI + PostgreSQL.

Before each request, sets the PostgreSQL session variable
`app.current_user_role` to the role from the verified JWT.
PostgreSQL RLS policies read this variable via:
    current_setting('app.current_user_role', true)

The second argument `true` in current_setting() means the function returns
NULL instead of raising an error when the variable is not set — this
prevents crashes on unauthenticated requests (which are already blocked
by the JWT middleware before they reach the DB layer).

This middleware only activates when DB_BACKEND=postgres.
For DuckDB dev, it is a no-op pass-through.
"""

from __future__ import annotations

import os

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class RLSContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
    if os.getenv("DB_BACKEND", "duckdb").lower() != "postgres":
        return await call_next(request)

    token = getattr(request.state, "token", None)
    if not token:
        return await call_next(request)

    role = token.get("role", "viewer")
    pool = request.app.state.db_pool

    async with pool.acquire() as conn:
        await conn.execute(
            "SELECT set_config('app.current_user_role', $1, true)", role,
        )
        # Store the *active* connection on request state
        request.state.db_conn = conn
        # call_next must happen INSIDE the async with block
        response = await call_next(request)
    # Connection is returned to pool AFTER the response is generated
    return response
