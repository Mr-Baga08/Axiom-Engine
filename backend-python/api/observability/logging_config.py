"""
observability/logging_config.py
────────────────────────────────
Structured JSON logging via structlog.

Every log record automatically inherits:
  - trace_id  (from current TraceContext, if set)
  - user_role (from current TraceContext)
  - timestamp (ISO-8601 UTC)
  - level

Usage
-----
    from observability.logging_config import configure_logging, get_logger

    configure_logging(level="INFO", json_output=True)   # call once at startup
    log = get_logger(__name__)
    log.info("query_received", question="...", user_id="u-123")
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Dict

import structlog
from structlog.types import EventDict, WrappedLogger


# ---------------------------------------------------------------------------
# Processors
# ---------------------------------------------------------------------------

def _inject_trace_context(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    """Processor: pull TraceContext into every log record."""
    try:
        from observability.tracing import get_trace_context
        ctx = get_trace_context()
        if ctx:
            event_dict["trace_id"]  = ctx.trace_id
            event_dict["user_role"] = ctx.user_role
            event_dict["user_id"]   = ctx.user_id
            event_dict["session_id"] = ctx.session_id
    except Exception:
        pass  # never crash because of logging
    return event_dict


def _drop_color_message(
    logger: WrappedLogger, method: str, event_dict: EventDict
) -> EventDict:
    """Remove uvicorn colour codes that pollute JSON."""
    event_dict.pop("color_message", None)
    return event_dict


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """
    Configure structlog + stdlib logging.
    Call once at application startup (e.g., in main.py lifespan).

    Parameters
    ----------
    level       : Root log level ("DEBUG", "INFO", "WARNING", "ERROR")
    json_output : Emit JSON (True for prod) or coloured console (False for dev)
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _inject_trace_context,
        _drop_color_message,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(level.upper())
        ),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Silence noisy third-party loggers
    for name in ("httpx", "httpcore", "uvicorn.access", "multipart"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str = __name__) -> structlog.BoundLogger:
    return structlog.get_logger(name)


# ---------------------------------------------------------------------------
# FastAPI middleware for per-request trace_id binding
# ---------------------------------------------------------------------------

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import time


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Attaches a structlog context (trace_id, path, method) to every request.
    Also emits request/response log lines with latency.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        import uuid
        from observability.tracing import get_trace_context

        t0 = time.perf_counter()

        # Prefer trace_id from an existing TraceContext (set by auth middleware)
        ctx = get_trace_context()
        trace_id = ctx.trace_id if ctx else str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            trace_id=trace_id,
            path=request.url.path,
            method=request.method,
        )

        log = get_logger("http")
        log.info("request_started")

        try:
            response = await call_next(request)
        except Exception as exc:
            log.error("request_failed", error=str(exc), exc_info=True)
            raise
        finally:
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            log.info(
                "request_complete",
                status_code=getattr(response, "status_code", 0),
                latency_ms=latency_ms,
            )
            structlog.contextvars.clear_contextvars()

        response.headers["X-Trace-Id"] = trace_id
        return response