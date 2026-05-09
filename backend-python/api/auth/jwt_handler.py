"""
JWT Handler – supports both RS256 (production) and HS256 (dev).
Algorithm is selected via the JWT_ALGORITHM env variable.
For RS256, JWT_PRIVATE_KEY_PATH and JWT_PUBLIC_KEY_PATH must point to PEM files.
For HS256, JWT_SECRET must be set.
"""
from __future__ import annotations

import os
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

# ── Configuration ──────────────────────────────────────────────────────────────

ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))


def _load_key(path_env: str) -> str:
    path = os.getenv(path_env)
    if not path:
        raise RuntimeError(f"Env var {path_env!r} is not set")
    return Path(path).read_text()


def _signing_key() -> str:
    if ALGORITHM == "RS256":
        return _load_key("JWT_PRIVATE_KEY_PATH")
    return os.environ["JWT_SECRET"]


def _verify_key() -> str:
    if ALGORITHM == "RS256":
        return _load_key("JWT_PUBLIC_KEY_PATH")
    return os.environ["JWT_SECRET"]


# ── Token creation ─────────────────────────────────────────────────────────────

def _build_claims(
    subject: str,
    role: str,
    extra: dict[str, Any],
    expire_delta: timedelta,
    token_type: str,
) -> dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    return {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + expire_delta,
        **extra,
    }


def create_access_token(subject: str, role: str, extra: dict[str, Any] | None = None) -> str:
    """
    Create a short-lived access token (default 30 min).

    Args:
        subject: The user UID (e.g. "25-JaneDoe-0001").
        role: The user's role string (e.g. "analyst", "executive", "admin").
        extra: Optional additional claims merged into the payload.

    Returns:
        A signed JWT string.
    """
    claims = _build_claims(
        subject=subject,
        role=role,
        extra=extra or {},
        expire_delta=timedelta(minutes=ACCESS_EXPIRE_MINUTES),
        token_type="access",
    )
    return jwt.encode(claims, _signing_key(), algorithm=ALGORITHM)


def create_refresh_token(subject: str, role: str) -> str:
    """
    Create a long-lived refresh token (default 7 days).

    Refresh tokens carry the minimum viable claims — no extra payload.
    They must never be accepted by endpoints that require an access token.
    The 'type' claim is 'refresh'; verify_token will reject refresh tokens
    on protected routes.

    Args:
        subject: The user UID.
        role: The user's role string.

    Returns:
        A signed JWT string with type='refresh'.
    """
    claims = _build_claims(
        subject=subject,
        role=role,
        extra={},
        expire_delta=timedelta(days=REFRESH_EXPIRE_DAYS),
        token_type="refresh",
    )
    return jwt.encode(claims, _signing_key(), algorithm=ALGORITHM)


# ── Token verification ─────────────────────────────────────────────────────────

_bearer = HTTPBearer()

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict[str, Any]:
    """
    FastAPI dependency. Decodes and validates the Bearer token.

    - Raises 401 if the token is missing, expired, or invalid.
    - Raises 401 if the token type is 'refresh' (refresh tokens must not be
      used to call protected API endpoints — only the /auth/refresh route
      accepts them).

    Returns the decoded payload dict on success.
    """
    token = credentials.credentials
    try:
        payload = jwt.decode(token, _verify_key(), algorithms=[ALGORITHM])
    except JWTError:
        raise CREDENTIALS_EXCEPTION
    if payload.get("type") != "access":
        raise CREDENTIALS_EXCEPTION
    return payload


def verify_refresh_token(raw_token: str) -> dict[str, Any]:
    """
    Verify a refresh token submitted to the /auth/refresh endpoint.
    Raises 401 on any failure. Returns the decoded payload on success.
    """
    try:
        payload = jwt.decode(raw_token, _verify_key(), algorithms=[ALGORITHM])
    except JWTError:
        raise CREDENTIALS_EXCEPTION
    if payload.get("type") != "refresh":
        raise CREDENTIALS_EXCEPTION
    return payload


# ── Utility ────────────────────────────────────────────────────────────────────

def hash_query(query: str) -> str:
    """Return the SHA-256 hex digest of a query string. Used for audit logging."""
    return hashlib.sha256(query.encode()).hexdigest()