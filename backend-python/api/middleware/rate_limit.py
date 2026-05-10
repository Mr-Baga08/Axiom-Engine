"""
Rate limiting via slowapi — Redis backend with in-memory fallback.

Limits:
    /query   : 10 req/min per IP
    /auth/*  : 5  req/min per IP  (brute-force protection)
    global   : 60 req/min per IP
"""
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

try:
    limiter = Limiter(
        key_func=get_remote_address,
        storage_uri=REDIS_URL,
        default_limits=["60/minute"],
    )
except Exception:
    # Redis unavailable (dev without Docker) — fall back to in-process memory.
    # Limits won't survive worker restarts but everything else works normally.
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=["60/minute"],
    )
