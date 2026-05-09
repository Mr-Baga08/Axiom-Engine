"""
observability/phoenix_eval.py
──────────────────────────────
Arize Phoenix integration for RAG evaluation.

After every retrieval step the pipeline calls `log_rag_evaluation()` which:
  1. Builds a pandas DataFrame of {query, chunk, relevance_score} triples.
  2. Ships it to a local Phoenix server via the OpenInference trace protocol.
  3. Computes RAGAS `context_relevance` and `faithfulness` scores.
  4. Stores rolling scores in Redis for the watchdog (see ragas_watchdog.py).

Phoenix server is started automatically in the background if not already running.
Set PHOENIX_COLLECTOR_ENDPOINT to point at an external instance.

Environment variables
---------------------
PHOENIX_COLLECTOR_ENDPOINT : e.g. "http://localhost:6006"  (default)
REDIS_URL                  : e.g. "redis://redis:6379/0"
ANTHROPIC_API_KEY          : used for RAGAS LLM evaluators
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import structlog

log = structlog.get_logger(__name__)

# ── Optional heavy deps — graceful degradation ───────────────────────────────
try:
    import pandas as pd
    _PANDAS = True
except ImportError:
    _PANDAS = False

try:
    import phoenix as px
    from phoenix.trace import SpanEvaluations
    from openinference.instrumentation.langchain import LangChainInstrumentor  # noqa: F401
    _PHOENIX = True
except ImportError:
    _PHOENIX = False
    log.warning("phoenix_not_installed", hint="pip install arize-phoenix openinference-instrumentation-langchain")

try:
    from ragas.metrics import context_relevance, faithfulness
    from ragas import evaluate as ragas_evaluate
    from datasets import Dataset as HFDataset
    _RAGAS = True
except ImportError:
    _RAGAS = False
    log.warning("ragas_not_installed", hint="pip install ragas datasets")

try:
    import redis.asyncio as aioredis
    _REDIS = True
except ImportError:
    _REDIS = False


# ---------------------------------------------------------------------------
# Phoenix session (singleton)
# ---------------------------------------------------------------------------

_phoenix_session = None


def ensure_phoenix_running() -> None:
    """Start an in-process Phoenix server if no external endpoint is configured."""
    global _phoenix_session
    if not _PHOENIX:
        return
    endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "")
    if endpoint:
        log.info("phoenix_external", endpoint=endpoint)
        return
    if _phoenix_session is None:
        try:
            _phoenix_session = px.launch_app()
            log.info("phoenix_started", url=str(_phoenix_session.url))
        except Exception as exc:
            log.error("phoenix_start_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Redis helper
# ---------------------------------------------------------------------------

_REDIS_SCORES_KEY = "ragas:scores"   # Redis list, JSON items
_REDIS_SCORES_MAX = 500              # rolling window size


async def _push_scores_to_redis(scores: Dict[str, float]) -> None:
    if not _REDIS:
        return
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        r = await aioredis.from_url(redis_url, decode_responses=True)
        entry = json.dumps({**scores, "ts": time.time()})
        await r.lpush(_REDIS_SCORES_KEY, entry)
        await r.ltrim(_REDIS_SCORES_KEY, 0, _REDIS_SCORES_MAX - 1)
        await r.aclose()
    except Exception as exc:
        log.warning("redis_push_failed", error=str(exc))


async def get_rolling_scores(n: int = 100) -> List[Dict[str, Any]]:
    """Return the last `n` RAGAS score snapshots from Redis."""
    if not _REDIS:
        return []
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        r = await aioredis.from_url(redis_url, decode_responses=True)
        raw = await r.lrange(_REDIS_SCORES_KEY, 0, n - 1)
        await r.aclose()
        return [json.loads(x) for x in raw]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Main evaluation function
# ---------------------------------------------------------------------------

async def log_rag_evaluation(
    query: str,
    chunks: List[Dict[str, Any]],
    final_answer: str,
    trace_id: Optional[str] = None,
) -> Dict[str, float]:
    """
    Log a RAG evaluation event to Phoenix and compute RAGAS metrics.

    Parameters
    ----------
    query        : The original user question.
    chunks       : List of retrieved chunks, each with keys:
                   "text", "relevance_score" (float 0-1), "source" (str).
    final_answer : The final LLM-generated answer string.
    trace_id     : LangFuse trace ID for correlation.

    Returns
    -------
    Dict with keys "context_relevance" and "faithfulness" (floats 0–1).
    Returns empty dict on failure so callers never crash.
    """
    if not chunks:
        return {}

    scores: Dict[str, float] = {}

    # ── 1. Build evaluation DataFrame ────────────────────────────────────
    if _PANDAS:
        rows = [
            {
                "query": query,
                "document": c.get("text", ""),
                "relevance_score": c.get("relevance_score", 0.0),
                "source": c.get("source", "unknown"),
                "trace_id": trace_id or "",
            }
            for c in chunks
        ]
        df = pd.DataFrame(rows)

        # ── 2. Ship DataFrame to Phoenix ──────────────────────────────────
        if _PHOENIX and _phoenix_session is not None:
            try:
                _log_to_phoenix(query=query, df=df, answer=final_answer, trace_id=trace_id)
            except Exception as exc:
                log.warning("phoenix_log_failed", error=str(exc))

        # ── 3. Compute RAGAS scores ───────────────────────────────────────
        if _RAGAS:
            try:
                scores = await _compute_ragas(query=query, chunks=chunks, answer=final_answer)
            except Exception as exc:
                log.warning("ragas_eval_failed", error=str(exc))
                # Fallback: use mean of CRAG relevance scores as proxy
                if rows:
                    mean_rel = sum(r["relevance_score"] for r in rows) / len(rows)
                    scores = {"context_relevance": round(mean_rel, 4), "faithfulness": 0.0}
        else:
            # Without RAGAS, use CRAG relevance scores as proxy metric
            mean_rel = sum(c.get("relevance_score", 0.0) for c in chunks) / len(chunks)
            scores = {"context_relevance": round(mean_rel, 4), "faithfulness": 0.0}

    # ── 4. Persist scores to Redis for watchdog ──────────────────────────
    if scores:
        await _push_scores_to_redis(scores)
        log.info(
            "rag_eval_complete",
            context_relevance=scores.get("context_relevance"),
            faithfulness=scores.get("faithfulness"),
            trace_id=trace_id,
            num_chunks=len(chunks),
        )

    return scores


# ---------------------------------------------------------------------------
# Phoenix logging helper
# ---------------------------------------------------------------------------

def _log_to_phoenix(
    query: str,
    df: "pd.DataFrame",
    answer: str,
    trace_id: Optional[str],
) -> None:
    """Send retrieval spans to Phoenix collector."""
    import phoenix as px  # already confirmed available

    # Phoenix expects SpanEvaluations for eval scores
    eval_df = df[["query", "document", "relevance_score"]].copy()
    eval_df = eval_df.rename(columns={"relevance_score": "score"})
    eval_df["label"] = eval_df["score"].apply(
        lambda s: "relevant" if s >= 0.5 else "not relevant"
    )
    eval_df["explanation"] = eval_df.apply(
        lambda row: f"CRAG score {row['score']:.2f} for query: {row['query'][:60]}", axis=1
    )

    span_evals = SpanEvaluations(
        eval_name="context_relevance",
        dataframe=eval_df,
    )
    px.Client().log_evaluations(span_evals)


# ---------------------------------------------------------------------------
# RAGAS computation
# ---------------------------------------------------------------------------

async def _compute_ragas(
    query: str,
    chunks: List[Dict[str, Any]],
    answer: str,
) -> Dict[str, float]:
    """Compute context_relevance and faithfulness via RAGAS."""
    from ragas.metrics import context_relevance as cr_metric, faithfulness as f_metric
    from ragas import evaluate as ragas_evaluate
    from datasets import Dataset as HFDataset
    from langchain_anthropic import ChatAnthropic
    from ragas.llms import LangchainLLMWrapper

    contexts = [c.get("text", "") for c in chunks]

    dataset = HFDataset.from_dict({
        "question":  [query],
        "answer":    [answer],
        "contexts":  [contexts],
        "ground_truth": [""],   # not required for these two metrics
    })

    llm_wrapper = LangchainLLMWrapper(
        ChatAnthropic(
            model="claude-haiku-4-5-20251001",
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
    )

    result = ragas_evaluate(
        dataset=dataset,
        metrics=[cr_metric, f_metric],
        llm=llm_wrapper,
        raise_exceptions=False,
    )

    scores_df = result.to_pandas()
    return {
        "context_relevance": round(float(scores_df["context_relevance"].mean()), 4),
        "faithfulness":      round(float(scores_df["faithfulness"].mean()), 4),
    }