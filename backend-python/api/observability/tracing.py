"""
observability/tracing.py
────────────────────────
LangFuse tracing integration for the AI-Insights pipeline.

Decorates: run_agent, din_sql, crag_retrieve, exec_pal
Registers: DIN-SQL, CRAG-evaluator, and DSPy-compiled prompts into LangFuse
           prompt registry on first import.

Usage
-----
    from observability.tracing import observe, get_trace_context, tracer

    @observe(name="my_func", tags=["sql"])
    async def my_func(...):
        ...
"""

from __future__ import annotations

import functools
import inspect
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import structlog

# ── LangFuse ────────────────────────────────────────────────────────────────
try:
    from langfuse import Langfuse
    from langfuse.decorators import langfuse_context, observe as _lf_observe

    _LANGFUSE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LANGFUSE_AVAILABLE = False

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Singleton LangFuse client
# ---------------------------------------------------------------------------

def _build_langfuse_client() -> Optional["Langfuse"]:
    if not _LANGFUSE_AVAILABLE:
        log.warning("langfuse_not_installed", hint="pip install langfuse")
        return None
    secret = os.getenv("LANGFUSE_SECRET_KEY", "")
    public = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    host   = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    if not secret or not public:
        log.warning("langfuse_keys_missing", hint="Set LANGFUSE_SECRET_KEY and LANGFUSE_PUBLIC_KEY")
        return None
    try:
        client = Langfuse(secret_key=secret, public_key=public, host=host)
        log.info("langfuse_client_ready", host=host)
        return client
    except Exception as exc:
        log.error("langfuse_init_failed", error=str(exc))
        return None


langfuse: Optional["Langfuse"] = _build_langfuse_client()


# ---------------------------------------------------------------------------
# Trace context (propagated via structlog / request state)
# ---------------------------------------------------------------------------

@dataclass
class TraceContext:
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    user_role: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    tool_calls: List[str] = field(default_factory=list)
    total_tokens: int = 0
    start_ts: float = field(default_factory=time.time)


# Thread-local-style context (FastAPI uses async context vars)
import contextvars
_ctx_var: contextvars.ContextVar[Optional[TraceContext]] = contextvars.ContextVar(
    "trace_ctx", default=None
)


def get_trace_context() -> Optional[TraceContext]:
    return _ctx_var.get()


@contextmanager
def trace_context(user_id: str, user_role: str, session_id: Optional[str] = None,
                  tags: Optional[List[str]] = None):
    """
    Context manager that creates a TraceContext and makes it available
    throughout the request lifetime.

    Usage (FastAPI middleware)::

        async def dispatch(self, request, call_next):
            with trace_context(user_id=uid, user_role=role):
                response = await call_next(request)
            return response
    """
    ctx = TraceContext(
        user_id=user_id,
        user_role=user_role,
        session_id=session_id or str(uuid.uuid4()),
        tags=tags or [],
    )
    token = _ctx_var.set(ctx)
    try:
        yield ctx
    finally:
        _ctx_var.reset(token)


# ---------------------------------------------------------------------------
# observe() decorator — wraps LangFuse @observe with fallback no-op
# ---------------------------------------------------------------------------

def observe(
    name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    capture_input: bool = True,
    capture_output: bool = True,
):
    """
    Decorator that instruments a function with LangFuse tracing.
    Falls back gracefully if LangFuse is unavailable.

    Parameters
    ----------
    name           : Override span name (defaults to function name)
    tags           : Extra tags merged with context-level tags
    capture_input  : Whether to record function arguments
    capture_output : Whether to record return value
    """
    _extra_tags = tags or []

    def decorator(fn: Callable) -> Callable:
        span_name = name or fn.__qualname__

        # ── LangFuse path ────────────────────────────────────────────────
        if _LANGFUSE_AVAILABLE:
            lf_decorated = _lf_observe(
                name=span_name,
                capture_input=capture_input,
                capture_output=capture_output,
            )(fn)

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                ctx = get_trace_context()
                if ctx:
                    langfuse_context.update_current_trace(
                        user_id=ctx.user_id,
                        session_id=ctx.session_id,
                        tags=ctx.tags + _extra_tags,
                    )
                    ctx.tool_calls.append(span_name)
                t0 = time.perf_counter()
                try:
                    result = await lf_decorated(*args, **kwargs)
                    return result
                except Exception as exc:
                    log.error("traced_fn_error", span=span_name, error=str(exc))
                    raise
                finally:
                    latency_ms = (time.perf_counter() - t0) * 1000
                    log.info(
                        "span_complete",
                        span=span_name,
                        latency_ms=round(latency_ms, 2),
                        trace_id=ctx.trace_id if ctx else None,
                    )

            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                ctx = get_trace_context()
                if ctx:
                    langfuse_context.update_current_trace(
                        user_id=ctx.user_id,
                        session_id=ctx.session_id,
                        tags=ctx.tags + _extra_tags,
                    )
                    ctx.tool_calls.append(span_name)
                t0 = time.perf_counter()
                try:
                    result = lf_decorated(*args, **kwargs)
                    return result
                except Exception as exc:
                    log.error("traced_fn_error", span=span_name, error=str(exc))
                    raise
                finally:
                    latency_ms = (time.perf_counter() - t0) * 1000
                    log.info(
                        "span_complete",
                        span=span_name,
                        latency_ms=round(latency_ms, 2),
                        trace_id=ctx.trace_id if ctx else None,
                    )

            return async_wrapper if inspect.iscoroutinefunction(fn) else sync_wrapper

        # ── No-op fallback ───────────────────────────────────────────────
        @functools.wraps(fn)
        async def noop_async(*args, **kwargs):
            ctx = get_trace_context()
            if ctx:
                ctx.tool_calls.append(span_name)
            return await fn(*args, **kwargs)

        @functools.wraps(fn)
        def noop_sync(*args, **kwargs):
            ctx = get_trace_context()
            if ctx:
                ctx.tool_calls.append(span_name)
            return fn(*args, **kwargs)

        return noop_async if inspect.iscoroutinefunction(fn) else noop_sync

    return decorator


# ---------------------------------------------------------------------------
# Token cost tracker (called by agent after each Anthropic response)
# ---------------------------------------------------------------------------

# Anthropic pricing (USD per 1M tokens) as of mid-2025 — update as needed
_COST_TABLE: Dict[str, Dict[str, float]] = {
    "claude-sonnet-4-20250514": {"input": 3.0,  "output": 15.0},
    "claude-opus-4-20250514":   {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5-20251001":{"input": 0.25, "output": 1.25},
}


def record_token_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Record token usage into the current TraceContext and LangFuse.
    Returns total cost in USD.
    """
    prices = _COST_TABLE.get(model, {"input": 3.0, "output": 15.0})
    cost_usd = (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000

    ctx = get_trace_context()
    if ctx:
        ctx.total_tokens += input_tokens + output_tokens

    if langfuse:
        try:
            langfuse_context.update_current_observation(
                usage={
                    "input": input_tokens,
                    "output": output_tokens,
                    "unit": "TOKENS",
                },
                metadata={"cost_usd": round(cost_usd, 6), "model": model},
            )
        except Exception:
            pass  # best-effort

    log.info(
        "token_cost",
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=round(cost_usd, 6),
    )
    return cost_usd


# ---------------------------------------------------------------------------
# Prompt Registry — register canonical prompts on startup
# ---------------------------------------------------------------------------

PROMPT_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "din-sql-schema-link": {
        "prompt": (
            "You are a SQL expert. Given the schema below, list only the "
            "table.column items relevant to answering the question.\n\n"
            "Schema:\n{{schema}}\n\nQuestion: {{question}}\n\nRelevant columns:"
        ),
        "labels": ["production"],
        "tags": ["sql", "schema-linking"],
    },
    "din-sql-classify": {
        "prompt": (
            "Classify the question as one of: simple | nested | set-operation.\n"
            "Question: {{question}}\nRelevant schema: {{linked_schema}}\n\nType:"
        ),
        "labels": ["production"],
        "tags": ["sql", "classification"],
    },
    "din-sql-generate": {
        "prompt": (
            "Write a syntactically correct SQL query for the question below.\n"
            "Return ONLY the SQL — no explanation.\n\n"
            "Question: {{question}}\nSchema: {{linked_schema}}\n"
            "Query type: {{qtype}}\nSubquery (if any): {{subquery}}\n\nSQL:"
        ),
        "labels": ["production"],
        "tags": ["sql", "generation"],
    },
    "crag-relevance-eval": {
        "prompt": (
            "Rate the relevance of the retrieved document chunk to the query "
            "on a scale from 0.0 to 1.0.\n\n"
            "Query: {{query}}\n\nChunk:\n{{chunk}}\n\n"
            "Return only a JSON object: {\"score\": <float>, \"reason\": \"<str>\"}"
        ),
        "labels": ["production"],
        "tags": ["rag", "evaluation"],
    },
    "dspy-entertainment-analysis": {
        "prompt": (
            "You are an AI analyst for Futures First Entertainment. "
            "You have exclusive access to a private database of company data — "
            "this data does NOT exist in any public source and is NOT in your training data.\n\n"
            "RULE: You MUST call the query_sql tool before answering ANY factual question. "
            "Never answer from memory. Never say the data doesn't exist. "
            "The database contains 2025 titles with real revenue figures — just query it.\n\n"
            "Database tables (use exact names):\n"
            "  movies         — movie_id, title, genre, release_date, budget_usd, "
            "box_office_usd, rating, director, release_year\n"
            "  viewers        — viewer_id, age, gender, city, country, subscription_tier\n"
            "  watch_activity — activity_id, viewer_id, movie_id, watch_date, "
            "completion_rate, device_type\n"
            "  reviews        — review_id, viewer_id, movie_id, rating, sentiment, review_text\n"
            "  marketing_spend — spend_id, movie_id, title, channel, spend_usd, "
            "impressions, clicks, campaign_start\n"
            "  regional_performance — region_id, movie_id, city, country, month, "
            "views, revenue_usd, engagement_score\n\n"
            "Question: {{question}}\n\nSQL Results:\n{{sql_results}}\n\n"
            "Document Context:\n{{doc_context}}\n\nAnswer:"
        ),
        "labels": ["production"],
        "tags": ["dspy", "analysis"],
    },
}


def register_prompts() -> None:
    """
    Upsert all canonical prompts into the LangFuse prompt registry.
    Called once at application startup.
    """
    if not langfuse:
        log.warning("prompt_registration_skipped", reason="LangFuse unavailable")
        return

    for prompt_name, definition in PROMPT_DEFINITIONS.items():
        try:
            langfuse.create_prompt(
                name=prompt_name,
                prompt=definition["prompt"],
                labels=definition.get("labels", []),
                tags=definition.get("tags", []),
                config={"version": "1.0"},
            )
            log.info("prompt_registered", name=prompt_name)
        except Exception as exc:
            # Prompt may already exist — that's fine
            log.debug("prompt_registration_note", name=prompt_name, detail=str(exc))


def get_prompt(name: str, variables: Dict[str, str]) -> str:
    """
    Fetch a prompt from LangFuse registry and interpolate variables.
    Falls back to local definition if LangFuse is unavailable.
    """
    if langfuse:
        try:
            prompt_obj = langfuse.get_prompt(name)
            return prompt_obj.compile(**variables)
        except Exception as exc:
            log.warning("prompt_fetch_failed", name=name, error=str(exc))

    # Fallback: use local template with simple {{key}} substitution
    template = PROMPT_DEFINITIONS.get(name, {}).get("prompt", "")
    for k, v in variables.items():
        template = template.replace(f"{{{{{k}}}}}", str(v))
    return template