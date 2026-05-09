"""
Rate limiting via slowapi backed by Redis.

Limits (adjust per environment):
    - /query  : 10 requests / minute per IP
    - /auth/* : 5 requests / minute per IP (brute-force protection)
    - Global catch : 60 requests / minute per IP

Redis is used as the storage backend so limits survive worker restarts
and work correctly across multiple uvicorn workers.
"""
import os

from slowapi import Limiter
from slowapi.util import get_remote_address

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# storage_uri tells slowapi to use Redis instead of in-process memory.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=REDIS_URL,
    default_limits=["60/minute"],
)