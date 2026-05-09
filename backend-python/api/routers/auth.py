from fastapi import APIRouter, Body, HTTPException, Request, status
from pydantic import BaseModel

from auth.jwt_handler import (
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
)
from middleware.rate_limiter import limiter

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Dev user registry (override via DEV_USERS_JSON env var in production) ─────

import json as _json
import os as _os

_DEFAULT_DEV_USERS = [
    {"username": "analyst_user", "password": "test",   "uid": "analyst-001", "role": "analyst"},
    {"username": "exec_user",    "password": "test",   "uid": "exec-001",    "role": "executive"},
    {"username": "viewer_user",  "password": "test",   "uid": "viewer-001",  "role": "viewer"},
    {"username": "admin_user",   "password": "test",   "uid": "admin-001",   "role": "admin"},
]

_DEV_USERS: list = _json.loads(_os.getenv("DEV_USERS_JSON", "null")) or _DEFAULT_DEV_USERS


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest):
    """
    Issue a short-lived JWT for the given credentials.
    Checks against DEV_USERS_JSON env (defaults to hardcoded dev accounts).
    Replace this endpoint with a real user-store lookup before production.
    """
    user = next(
        (u for u in _DEV_USERS if u["username"] == body.username and u["password"] == body.password),
        None,
    )
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token  = create_access_token(subject=user["uid"], role=user["role"])
    refresh_token = create_refresh_token(subject=user["uid"], role=user["role"])
    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "uid":           user["uid"],
        "role":          user["role"],
    }


@router.post("/refresh")
@limiter.limit("5/minute")
async def refresh(
    request: Request,
    refresh_token: str = Body(..., embed=True),
):
    """
    Exchange a valid refresh token for a new access + refresh token pair.

    Old refresh token is implicitly invalidated by expiry only (stateless).
    For stateful revocation, store a token JTI blocklist in Redis.
    """
    try:
        payload = verify_refresh_token(refresh_token)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    new_access = create_access_token(subject=payload["sub"], role=payload["role"])
    new_refresh = create_refresh_token(subject=payload["sub"], role=payload["role"])
    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer",
    }