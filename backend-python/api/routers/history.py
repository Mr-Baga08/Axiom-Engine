"""
Conversation history — per-user, Redis-backed, 30-day TTL.

Endpoints
─────────
  GET  /history        last 50 entries for the authenticated user
  DELETE /history      clear all entries for the authenticated user

Internal helper
───────────────
  push_history_entry() — called by main.py after every successful agent run
"""
from __future__ import annotations

import json
import os
import time

from fastapi import APIRouter, Depends, Request

from auth.rbac import require_scope
from middleware.rate_limiter import limiter

router = APIRouter(prefix="/history", tags=["history"])

_HISTORY_TTL_SECONDS = 30 * 24 * 3600   # 30 days
_MAX_ITEMS           = 100


def _redis():
    import redis
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


def push_history_entry(
    user_id: str,
    query: str,
    answer_preview: str,
    tools_used: list[str],
) -> None:
    """
    Push one history entry onto the user's Redis list.
    Silently swallows all errors so a Redis outage never blocks a chat response.
    """
    try:
        r = _redis()
        key = f"history:{user_id}"
        entry = {
            "query":          query,
            "answer_preview": answer_preview[:150],
            "tools_used":     tools_used,
            "timestamp":      int(time.time() * 1000),
        }
        r.lpush(key, json.dumps(entry))
        r.ltrim(key, 0, _MAX_ITEMS - 1)
        r.expire(key, _HISTORY_TTL_SECONDS)
    except Exception:
        pass


@router.get("")
@limiter.limit("30/minute")
async def get_history(
    request: Request,
    token: dict = Depends(require_scope("query:read")),
):
    """Return the last 50 queries for the authenticated user."""
    user_id = token.get("sub", "anonymous")
    try:
        r = _redis()
        raw = r.lrange(f"history:{user_id}", 0, 49)
        return {"history": [json.loads(e) for e in raw]}
    except Exception:
        return {"history": []}


@router.delete("")
@limiter.limit("10/minute")
async def clear_history(
    request: Request,
    token: dict = Depends(require_scope("query:read")),
):
    """Delete all history entries for the authenticated user."""
    user_id = token.get("sub", "anonymous")
    try:
        r = _redis()
        r.delete(f"history:{user_id}")
    except Exception:
        pass
    return {"cleared": True}
