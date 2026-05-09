"""
LangFuse client singleton and prompt registry.

Prompt registry:
  DIN-SQL, CRAG evaluator, and DSPy compiled prompts are stored in LangFuse
  so changes can be A/B tested, rolled back, and tracked without code deploys.

  Prompts are pushed to LangFuse at application startup via register_prompts().
  Downstream code fetches them via get_prompt(name) — if LangFuse is unavailable,
  the hardcoded fallback string is returned so the pipeline never breaks.

Fallback behaviour:
  All public functions in this module catch every exception and return a
  safe default. LangFuse being down must never take down the application.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_client = None
_SENTINEL = object()


def get_client():
    """Return the LangFuse singleton, or None if not configured."""
    global _client
    if _client is _SENTINEL:
        return None
    if _client is not None:
        return _client

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "")
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        logger.warning(
            "LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY not set — "
            "tracing is disabled. Set both in .env to enable."
        )
        _client = _SENTINEL
        return None

    try:
        from langfuse import Langfuse
        _client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
        )
        logger.info("LangFuse client initialised (host=%s)", host)
        return _client
    except Exception as exc:
        logger.error("LangFuse initialisation failed: %s — tracing disabled", exc)
        _client = _SENTINEL
        return None


# ── Prompt definitions ────────────────────────────────────────────────────────
# These are the canonical prompt strings. They are registered into LangFuse
# at startup so they can be edited via the LangFuse UI without code changes.

_PROMPT_REGISTRY: dict[str, str] = {
    "din_sql_schema_link": (
        "Given the schema below, list every table.column pair that is relevant "
        "to answering the question. Output a plain bulleted list only — no explanation.\n\n"
        "Schema:\n{{schema}}\n\nQuestion: {{question}}"
    ),
    "din_sql_classify": (
        "Classify the SQL query type needed to answer the question, given the relevant "
        "schema elements listed below. Reply with exactly one word: simple, nested, or "
        "set-operation.\n\nRelevant schema elements:\n{{linked}}\n\nQuestion: {{question}}"
    ),
    "din_sql_final": (
        "Write a single SQL SELECT statement that answers the question exactly. "
        "Output SQL only — no explanation, no markdown fences, no trailing semicolon.\n\n"
        "Schema elements:\n{{linked}}\n\nQuery type: {{qtype}}\n{{subquery_hint}}"
        "\nQuestion: {{question}}"
    ),
    "crag_relevance_eval": (
        "Rate how relevant the following document chunk is to answering the question. "
        "Return ONLY a decimal number between 0.0 and 1.0. No explanation.\n\n"
        "Question: {{question}}\n\nChunk:\n{{chunk}}"
    ),
    "crag_decompose": (
        "Break the following question into 2-3 simpler sub-questions that together cover "
        "the original question. Return each sub-question on its own line, no numbering, "
        "no explanation.\n\nQuestion: {{question}}"
    ),
}


def register_prompts() -> None:
    """
    Push all prompt strings to LangFuse at application startup.
    Idempotent — calling multiple times is safe (LangFuse versions prompts).
    Silently skips if LangFuse is not configured.
    """
    lf = get_client()
    if lf is None:
        return

    for prompt_name, prompt_text in _PROMPT_REGISTRY.items():
        try:
            lf.create_prompt(
                name=prompt_name,
                prompt=prompt_text,
                labels=["production"],
            )
            logger.debug("Registered prompt: %s", prompt_name)
        except Exception as exc:
            # Prompt may already exist — this is expected after first run
            logger.debug("Prompt registration for %r: %s", prompt_name, exc)

    logger.info("LangFuse prompt registry sync complete (%d prompts)", len(_PROMPT_REGISTRY))


def get_prompt(name: str) -> str:
    """
    Fetch the latest prompt string from LangFuse.
    Falls back to the hardcoded value if LangFuse is unavailable.

    Args:
        name: Prompt name as registered in _PROMPT_REGISTRY.

    Returns:
        The prompt string (from LangFuse if available, else hardcoded).
    """
    lf = get_client()
    if lf is not None:
        try:
            prompt_obj = lf.get_prompt(name)
            return prompt_obj.get_langchain_prompt()
        except Exception as exc:
            logger.warning("Could not fetch prompt %r from LangFuse: %s", name, exc)

    return _PROMPT_REGISTRY.get(name, "")


def create_trace(
    name: str,
    trace_id: str,
    user_uid: str,
    session_id: Optional[str] = None,
    tags: list[str] | None = None,
):
    """
    Create a root-level LangFuse trace for a request.
    Returns the trace object, or None if LangFuse is unavailable.
    """
    lf = get_client()
    if lf is None:
        return None
    try:
        return lf.trace(
            id=trace_id,
            name=name,
            user_id=user_uid,
            session_id=session_id,
            tags=tags or [],
        )
    except Exception as exc:
        logger.warning("LangFuse trace creation failed: %s", exc)
        return None
