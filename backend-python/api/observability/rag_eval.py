"""
Split RAG evaluation pipeline.

CRITICAL TIMING RULE (enforced here, not in callers):
  - context_relevance: computed at retrieval time, inside crag_retrieve().
                       Does not require the answer — only query + chunks.
  - faithfulness:      computed AFTER run_agent() returns the final answer.
                       Requires query + chunks + answer. Never call before
                       the answer exists.

Calling faithfulness before the answer exists would produce meaningless
scores (RAGAS would evaluate against an empty string) and pollute the
Redis rolling window with incorrect data.

Scores are written to Redis as a FIFO list capped at RAGAS_EVAL_QUEUE_SIZE.
The watchdog polls this list to compute rolling averages. Scores are only
written when TraceContext.sampled is True — so the rolling window reflects
the true distribution even at low sample rates.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

QUEUE_SIZE: int = int(os.getenv("RAGAS_EVAL_QUEUE_SIZE", "50"))
REDIS_RELEVANCE_KEY = "ragas:context_relevance"
REDIS_FAITHFULNESS_KEY = "ragas:faithfulness"


def _get_redis():
    try:
        import redis as redis_lib
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        return redis_lib.from_url(url)
    except Exception as exc:
        logger.warning("Redis unavailable for RAGAS score storage: %s", exc)
        return None


def _push_score(redis_client, key: str, score: float, trace_id: str) -> None:
    """Push a score to the Redis FIFO list, capped at QUEUE_SIZE."""
    if redis_client is None:
        return
    try:
        entry = json.dumps({"score": score, "trace_id": trace_id, "ts": time.time()})
        pipe = redis_client.pipeline()
        pipe.lpush(key, entry)
        pipe.ltrim(key, 0, QUEUE_SIZE - 1)
        pipe.execute()
    except Exception as exc:
        logger.warning("Could not push RAGAS score to Redis: %s", exc)


def record_relevance(
    query: str,
    chunks: list[str],
    trace_id: Optional[str] = None,
) -> float:
    """
    Compute and store context_relevance. Call at retrieval time.

    Args:
        query:    The user's question.
        chunks:   List of retrieved chunk texts.
        trace_id: LangFuse trace ID (defaults to current TraceContext).

    Returns:
        The context_relevance score (0.0–1.0), or 0.0 on failure.
    """
    from trace_context import get_trace_context
    ctx = get_trace_context()

    if not ctx.sampled:
        return 0.0   # not sampled — skip eval and Redis write

    if trace_id is None:
        trace_id = ctx.trace_id

    from phoenix_setup import run_ragas_relevance
    scores = run_ragas_relevance(queries=[query], retrieved_contexts=[chunks])
    score = scores.get("context_relevance", 0.0)

    r = _get_redis()
    _push_score(r, REDIS_RELEVANCE_KEY, score, trace_id)

    try:
        from langfuse_client import get_client
        lf = get_client()
        if lf:
            lf.score(trace_id=trace_id, name="context_relevance", value=score)
    except Exception:
        pass

    logger.info(
        "RAGAS context_relevance",
        score=round(score, 4),
        query_preview=query[:80],
        chunk_count=len(chunks),
        trace_id=trace_id,
    )
    return score


def record_faithfulness(
    query: str,
    chunks: list[str],
    answer: str,
    trace_id: Optional[str] = None,
) -> float:
    """
    Compute and store faithfulness. Call ONLY after the final answer exists.

    This function MUST NOT be called before run_agent() returns. Calling it
    with an empty or partial answer produces invalid scores.

    Args:
        query:    The user's question.
        chunks:   The same chunks used to generate the answer.
        answer:   The final synthesised answer from DSPy/run_agent().
        trace_id: LangFuse trace ID (defaults to current TraceContext).

    Returns:
        The faithfulness score (0.0–1.0), or 0.0 on failure.
    """
    if not answer or not answer.strip():
        logger.warning(
            "record_faithfulness called with empty answer — skipping. "
            "Ensure run_agent() has completed before calling this function."
        )
        return 0.0

    from trace_context import get_trace_context
    ctx = get_trace_context()

    if not ctx.sampled:
        return 0.0

    if trace_id is None:
        trace_id = ctx.trace_id

    from phoenix_setup import run_ragas_faithfulness
    scores = run_ragas_faithfulness(
        queries=[query],
        retrieved_contexts=[chunks],
        answers=[answer],
    )
    score = scores.get("faithfulness", 0.0)

    r = _get_redis()
    _push_score(r, REDIS_FAITHFULNESS_KEY, score, trace_id)

    try:
        from langfuse_client import get_client
        lf = get_client()
        if lf:
            lf.score(trace_id=trace_id, name="faithfulness", value=score)
    except Exception:
        pass

    logger.info(
        "RAGAS faithfulness",
        score=round(score, 4),
        query_preview=query[:80],
        answer_preview=answer[:80],
        trace_id=trace_id,
    )
    return score


def get_rolling_averages() -> dict[str, float | int]:
    """
    Read the Redis rolling window and return current averages.
    Returns {"context_relevance": float, "faithfulness": float, "sample_count": int}.
    Returns zeros if Redis is unavailable or has no data.
    """
    r = _get_redis()
    if r is None:
        return {"context_relevance": 0.0, "faithfulness": 0.0, "sample_count": 0}

    def _avg(key: str) -> tuple[float, int]:
        try:
            raw_entries = r.lrange(key, 0, -1)
            if not raw_entries:
                return 0.0, 0
            scores = [json.loads(e)["score"] for e in raw_entries]
            return sum(scores) / len(scores), len(scores)
        except Exception:
            return 0.0, 0

    rel_avg, rel_count = _avg(REDIS_RELEVANCE_KEY)
    faith_avg, _ = _avg(REDIS_FAITHFULNESS_KEY)

    return {
        "context_relevance": round(rel_avg, 4),
        "faithfulness": round(faith_avg, 4),
        "sample_count": rel_count,
    }
