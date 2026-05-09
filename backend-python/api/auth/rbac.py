"""
RBAC – scope-based access control for FastAPI route handlers.

Roles and their allowed scopes:
    viewer     → query:read
    analyst    → query:read, tools:basic
    executive  → query:read, tools:basic, docs:executive
    admin      → query:read, tools:basic, docs:executive, admin:all

A scope is a colon-separated string: "<resource>:<permission>".
The require_scope() dependency raises HTTP 403 if the token's role does not
include the requested scope.

Usage in a route:
    @router.post("/executive-summary")
    async def executive_summary(
        token: dict = Depends(require_scope("docs:executive")),
    ):
        ...
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, status
from .jwt_handler import verify_token

# ── Scope map ──────────────────────────────────────────────────────────────────

SCOPE_MAP: dict[str, set[str]] = {
    "viewer": {
        "query:read",
    },
    "analyst": {
        "query:read",
        "tools:basic",
    },
    "executive": {
        "query:read",
        "tools:basic",
        "docs:executive",
    },
    "admin": {
        "query:read",
        "tools:basic",
        "docs:executive",
        "admin:all",
    },
}

FORBIDDEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Insufficient permissions for this resource",
)


# ── Dependency factory ─────────────────────────────────────────────────────────

def require_scope(scope: str):
    """
    FastAPI dependency factory.

    Returns a dependency that:
    1. Verifies the Bearer token (delegates to verify_token).
    2. Looks up the role in SCOPE_MAP.
    3. Raises HTTP 403 if the role does not include `scope`.
    4. Returns the full decoded token payload on success.

    Example:
        token = Depends(require_scope("docs:executive"))
    """
    async def _check(token: dict = Depends(verify_token)) -> dict:
        role: str = token.get("role", "")
        allowed = SCOPE_MAP.get(role, set())
        if scope not in allowed:
            raise FORBIDDEN
        return token

    # Give the inner dependency a unique name so FastAPI doesn't merge/cache
    # across different require_scope() calls.
    _check.__name__ = f"require_scope_{scope.replace(':', '_')}"
    return _check