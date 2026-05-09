"""
Request logging middleware with trace ID injection.

Responsibilities:
  1. Generate a per-request trace ID (or read X-Request-Id if present).
  2. Make the sampling decision for this request via should_sample().
  3. Create a TraceContext and set it on the current async task.
  4. Create a root LangFuse trace for sampled requests.
  5. Add X-Trace-Id to the response headers.
  6. Log request start, end, latency, status code, and tools called.

SECURITY NOTE — X-Trace-Id response header:
  This header is useful for correlating client-reported errors with backend
  traces in LangFuse. However, it MUST be stripped at your reverse proxy
  (nginx, Caddy, Cloudflare) before responses reach external clients.
  Reason: trace IDs allow an external attacker to map your internal
  observability infrastructure, potentially correlating request timing
  with sensitive operations.
  Add this to your nginx config:
      proxy_hide_header X-Trace-Id;
  Or in Caddy:
      header -X-Trace-Id
"""

from __future__ import annotations

import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from observability.trace_context import new_trace_context
from observability.observe import should_sample

logger = structlog.get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()

        # Determine sampling for this request (one decision per request)
        sampled = should_sample()

        # Extract user info from the JWT if already decoded by JWT middleware.
        # JWT middleware runs before this one (check registration order in main.py).
        token = getattr(request.state, "token", {})
        user_uid = token.get("sub", "anonymous") if token else "anonymous"
        role = token.get("role", "unknown") if token else "unknown"

        # Create trace context for this request
        ctx = new_trace_context(
            user_uid=user_uid,
            role=role,
            session_id=request.headers.get("X-Session-Id"),
            sampled=sampled,
        )

        # Create root LangFuse trace for sampled requests
        if sampled:
            try:
                from observability.langfuse_client import create_trace
                create_trace(
                    name=f"{request.method} {request.url.path}",
                    trace_id=ctx.trace_id,
                    user_uid=user_uid,
                    session_id=ctx.session_id,
                    tags=[f"role:{role}", f"path:{request.url.path}"],
                )
            except Exception:
                pass

        response: Response = await call_next(request)

        latency_ms = round((time.perf_counter() - start) * 1000)

        logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
            sampled=sampled,
            user_uid=user_uid,
            role=role,
        )

        # Add trace ID to response — strip at reverse proxy before external egress
        response.headers["X-Trace-Id"] = ctx.trace_id

        return response
