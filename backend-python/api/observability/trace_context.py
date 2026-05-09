"""
Async-safe trace context propagation via Python contextvars.

Why contextvars and not threading.local():
  FastAPI uses asyncio — each request runs in a coroutine, not a thread.
  threading.local() is per-thread, so all concurrent requests sharing the
  same event loop thread would read each other's trace IDs.
  contextvars.ContextVar is per-async-task, which maps to per-request in
  FastAPI, making it the only correct choice for async context propagation.

Usage:
  # In middleware, at request start:
  ctx = TraceContext(trace_id="abc", user_uid="25-JaneDoe-0001", role="analyst")
  _current_trace.set(ctx)

  # Anywhere downstream (no argument passing needed):
  ctx = get_trace_context()
  logger.info("tool called", trace_id=ctx.trace_id)
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TraceContext:
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_uid: str = "anonymous"
    role: str = "unknown"
    session_id: Optional[str] = None
    sampled: bool = True          # False when TRACE_SAMPLE_RATE excludes this request


# Module-level ContextVar — one slot per async task, never shared across tasks
_current_trace: ContextVar[Optional[TraceContext]] = ContextVar(
    "_current_trace", default=None
)


def set_trace_context(ctx: TraceContext) -> None:
    """Set the trace context for the current async task."""
    _current_trace.set(ctx)


def get_trace_context() -> TraceContext:
    """
    Return the current trace context.

    Returns a default no-op context if none has been set (e.g. in background
    tasks or test code that doesn't go through the middleware).
    """
    ctx = _current_trace.get()
    if ctx is None:
        ctx = TraceContext(sampled=False)
        _current_trace.set(ctx)
    return ctx


def new_trace_context(
    user_uid: str = "anonymous",
    role: str = "unknown",
    session_id: Optional[str] = None,
    sampled: bool = True,
) -> TraceContext:
    """Create, register, and return a new TraceContext for the current task."""
    ctx = TraceContext(
        trace_id=str(uuid.uuid4()),
        user_uid=user_uid,
        role=role,
        session_id=session_id,
        sampled=sampled,
    )
    set_trace_context(ctx)
    return ctx
