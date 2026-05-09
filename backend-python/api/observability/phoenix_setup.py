"""
Phoenix (Arize) server setup and RAGAS evaluation runner.

Phoenix provides the evaluation UI at http://localhost:6006.
RAGAS computes context_relevance and faithfulness metrics.
Both use Gemini (via LangChain wrapper) as the evaluating LLM.

Phoenix is started as a background thread at application startup.
It does not require a separate process — it embeds in the FastAPI process.

RAGAS metrics used:
  context_relevance  — measures how relevant retrieved chunks are to the query.
                       Computable at retrieval time (does not need the answer).
  faithfulness       — measures whether the answer is grounded in the context.
                       Requires both the answer AND the retrieved context.
                       Must only be computed AFTER run_agent() returns.

These two metrics are intentionally computed at different points in the pipeline.
See rag_eval.py for the split timing implementation.
"""

from __future__ import annotations

import logging
import os

import structlog

logger = structlog.get_logger(__name__)

_phoenix_session = None


def start_phoenix() -> None:
    """
    Launch the Phoenix evaluation server.
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _phoenix_session
    if _phoenix_session is not None:
        return

    try:
        import phoenix as px
        host = os.getenv("PHOENIX_HOST", "localhost")
        port = int(os.getenv("PHOENIX_PORT", "6006"))
        _phoenix_session = px.launch_app(host=host, port=port)
        logger.info("Phoenix evaluation server started", host=host, port=port)
    except Exception as exc:
        logger.warning("Phoenix could not start: %s — RAG eval UI disabled", exc)
        _phoenix_session = False   # Mark as attempted; don't retry


def get_phoenix_session():
    """Return the Phoenix session, or None if not started."""
    return _phoenix_session if _phoenix_session not in (None, False) else None


def build_ragas_dataset(
    queries: list[str],
    retrieved_contexts: list[list[str]],
    answers: list[str],
) -> "datasets.Dataset":  # type: ignore
    """
    Build a HuggingFace Dataset in the format RAGAS expects.

    Args:
        queries:            List of question strings.
        retrieved_contexts: List of context lists (one list per query).
        answers:            List of answer strings. Pass empty strings for
                            relevance-only evaluation (no answer needed).

    Returns:
        A datasets.Dataset with columns: question, contexts, answer.
    """
    from datasets import Dataset
    return Dataset.from_dict({
        "question": queries,
        "contexts": retrieved_contexts,
        "answer": answers,
    })


def run_ragas_relevance(
    queries: list[str],
    retrieved_contexts: list[list[str]],
) -> dict[str, float]:
    """
    Compute context_relevance only (no answer required).
    Called immediately after CRAG retrieval.

    Returns:
        {"context_relevance": float}  — mean score over all query-context pairs.
        Returns {"context_relevance": 0.0} on failure.
    """
    try:
        from ragas import evaluate
        from ragas.metrics import context_relevance
        from gemini_eval import get_langchain_gemini

        dataset = build_ragas_dataset(
            queries=queries,
            retrieved_contexts=retrieved_contexts,
            answers=[""] * len(queries),   # not needed for relevance
        )
        llm = get_langchain_gemini()
        results = evaluate(
            dataset,
            metrics=[context_relevance],
            llm=llm,
            raise_exceptions=False,
        )
        score = float(results["context_relevance"])
        return {"context_relevance": score}
    except Exception as exc:
        logger.warning("RAGAS relevance eval failed: %s", exc)
        return {"context_relevance": 0.0}


def run_ragas_faithfulness(
    queries: list[str],
    retrieved_contexts: list[list[str]],
    answers: list[str],
) -> dict[str, float]:
    """
    Compute faithfulness (requires both context AND answer).
    Called only after run_agent() returns the final answer.

    Returns:
        {"faithfulness": float} — mean score over all query-context-answer triples.
        Returns {"faithfulness": 0.0} on failure.
    """
    try:
        from ragas import evaluate
        from ragas.metrics import faithfulness
        from gemini_eval import get_langchain_gemini

        dataset = build_ragas_dataset(
            queries=queries,
            retrieved_contexts=retrieved_contexts,
            answers=answers,
        )
        llm = get_langchain_gemini()
        results = evaluate(
            dataset,
            metrics=[faithfulness],
            llm=llm,
            raise_exceptions=False,
        )
        score = float(results["faithfulness"])
        return {"faithfulness": score}
    except Exception as exc:
        logger.warning("RAGAS faithfulness eval failed: %s", exc)
        return {"faithfulness": 0.0}
