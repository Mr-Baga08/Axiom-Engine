"""
@observe() decorator for LangFuse span creation with sampling.

The decorator wraps both sync and async functions. It checks TRACE_SAMPLE_RATE
before creating a LangFuse span — if the request was not sampled (determined
at middleware entry), the function runs unwrapped with zero overhead.

Sampling contract:
  - TRACE_SAMPLE_RATE=1.0 → every call creates a span (dev/staging default).
  - TRACE_SAMPLE_RATE=0.1 → ~10% of calls create a span (production default).
  - The sampling decision is made ONCE per request in LoggingMiddleware and
    stored on TraceContext.sampled. All child spans in the same request inherit
    that decision — either the entire request is traced or none of it is.
    This ensures traces are complete (no orphaned child spans).
  - RAGAS scores are only written to Redis when sampled=True, so the rolling
    window naturally reflects the sample rate.

Usage:
    @observe(name="crag_retrieve", tags=["rag", "retrieval"])
    async def crag_retrieve(question, user_role):
        ...

    @observe(name="din_sql")
    def din_sql(question, schema, examples, llm):
        ...
"""

from __future__ import annotations

import functools
import inspect
import os
import random
import time
from typing import Any, Callable

from .trace_context import get_trace_context

TRACE_SAMPLE_RATE: float = float(os.getenv("TRACE_SAMPLE_RATE", "1.0"))


def _get_langfuse():
    """
    Return the LangFuse client singleton, or None if LangFuse is not configured.
    Graceful no-op fallback: missing keys → returns None, never raises.
    """
    try:
        from .langfuse_client import get_client
        return get_client()
    except Exception:
        return None


def observe(
    name: str | None = None,
    tags: list[str] | None = None,
    capture_input: bool = True,
    capture_output: bool = True,
):
    """
    Decorator factory for LangFuse span creation with sampling.

    Args:
        name:           Span name shown in LangFuse UI. Defaults to function name.
        tags:           List of string tags for filtering in LangFuse.
        capture_input:  Whether to record function arguments as span input.
                        Set False for functions receiving sensitive data.
        capture_output: Whether to record the return value as span output.
    """
    def decorator(func: Callable) -> Callable:
        span_name = name or func.__qualname__
        is_async = inspect.iscoroutinefunction(func)

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                ctx = get_trace_context()
                lf = _get_langfuse() if ctx.sampled else None

                if lf is None:
                    # Not sampled or LangFuse unavailable — run unwrapped
                    return await func(*args, **kwargs)

                span = lf.span(
                    name=span_name,
                    trace_id=ctx.trace_id,
                    tags=(tags or []) + [f"role:{ctx.role}"],
                    input=_safe_input(args, kwargs) if capture_input else None,
                )
                start = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    span.end(
                        output=_safe_output(result) if capture_output else None,
                        metadata={"latency_ms": round((time.perf_counter() - start) * 1000)},
                    )
                    return result
                except Exception as exc:
                    span.end(
                        level="ERROR",
                        status_message=str(exc),
                        metadata={"latency_ms": round((time.perf_counter() - start) * 1000)},
                    )
                    raise
            return async_wrapper

        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                ctx = get_trace_context()
                lf = _get_langfuse() if ctx.sampled else None

                if lf is None:
                    return func(*args, **kwargs)

                span = lf.span(
                    name=span_name,
                    trace_id=ctx.trace_id,
                    tags=(tags or []) + [f"role:{ctx.role}"],
                    input=_safe_input(args, kwargs) if capture_input else None,
                )
                start = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    span.end(
                        output=_safe_output(result) if capture_output else None,
                        metadata={"latency_ms": round((time.perf_counter() - start) * 1000)},
                    )
                    return result
                except Exception as exc:
                    span.end(level="ERROR", status_message=str(exc))
                    raise
            return sync_wrapper

    return decorator


def _safe_input(args: tuple, kwargs: dict) -> dict[str, Any]:
    """Serialise function arguments, truncating large values."""
    try:
        combined = {f"arg_{i}": _truncate(v) for i, v in enumerate(args)}
        combined.update({k: _truncate(v) for k, v in kwargs.items()})
        return combined
    except Exception:
        return {"_serialisation_error": True}


def _safe_output(value: Any) -> Any:
    """Serialise return value, truncating if large."""
    try:
        return _truncate(value)
    except Exception:
        return {"_serialisation_error": True}


def _truncate(value: Any, max_len: int = 2000) -> Any:
    """Truncate strings and large collections for span storage."""
    if isinstance(value, str) and len(value) > max_len:
        return value[:max_len] + f"…[truncated {len(value) - max_len} chars]"
    if isinstance(value, (list, tuple)) and len(value) > 20:
        return list(value[:20]) + [f"…[{len(value) - 20} more items]"]
    if isinstance(value, dict) and len(value) > 20:
        items = list(value.items())[:20]
        return dict(items) | {"_truncated": True}
    return value


def should_sample() -> bool:
    """
    Determine whether the current request should be traced.
    Called once per request in LoggingMiddleware.
    """
    return random.random() < TRACE_SAMPLE_RATE
