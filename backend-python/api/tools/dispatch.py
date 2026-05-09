"""
CRAG – Corrective Retrieval-Augmented Generation.

Algorithm:
  1. Retrieve top-K chunks from LightRAG with ACL filter.
  2. Verify HMAC signature of each chunk (tamper detection).
  3. Score each chunk's relevance to the query with a fast LLM call.
  4. Classify chunks: correct (≥threshold), ambiguous (0.2–threshold),
     incorrect (<0.2).
  5. If at least one correct chunk is found → return correct + ambiguous.
  6. If no correct chunk is found → decompose the query into sub-questions,
     retry retrieval with a lower threshold, then merge.

The evaluator LLM must be cheap and fast (gpt-4o-mini or a local model).
It is called once per chunk — do not use the main reasoning model here.

PII scrubbing is applied to chunk text at retrieval time as a second
safety net (primary scrubbing happens at ingestion in pdf_loader.py).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from ingestion.pdf_loader import verify_chunk
from pipeline.lightrag_setup import get_rag
from security.pii_scrubber import scrub

logger = logging.getLogger(__name__)

RELEVANCE_THRESHOLD: float = float(os.getenv("CRAG_RELEVANCE_THRESHOLD", "0.5"))
FALLBACK_THRESHOLD: float = float(os.getenv("CRAG_FALLBACK_THRESHOLD", "0.3"))
TOP_K: int = 10   # initial retrieval count before scoring


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class ScoredChunk:
    text: str
    metadata: dict[str, Any]
    relevance: float          # 0.0 – 1.0 from eval_llm
    tamper_ok: bool           # True if HMAC verified


@dataclass
class CRAGResult:
    chunks: list[ScoredChunk]
    query_used: str           # may differ from original if decomposition ran
    decomposed: bool = False  # True if fallback decomposition was triggered
    sources: list[dict] = field(default_factory=list)


# ── Evaluator LLM ─────────────────────────────────────────────────────────────

_EVAL_PROMPT = """\
Rate how relevant the following document chunk is to answering the question.
Return ONLY a decimal number between 0.0 and 1.0. No explanation.

Question: {question}

Chunk:
{chunk}
"""


def eval_llm(prompt: str, temperature: float = 0.0) -> str:
    """
    Cheap, fast LLM call for relevance scoring.

    Uses gpt-4o-mini by default (set EVAL_LLM_MODEL env var to override).
    temperature=0 for determinism — relevance scores must be reproducible.
    """
    import openai
    client = openai.OpenAI()
    model = os.getenv("EVAL_LLM_MODEL", "gpt-4o-mini")
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_tokens=8,   # we only need a float like "0.87"
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()


def _score_chunk(question: str, chunk_text: str) -> float:
    """
    Score a single chunk's relevance to the question. Returns 0.0 on failure.
    """
    raw = eval_llm(_EVAL_PROMPT.format(question=question, chunk=chunk_text[:1000]))
    try:
        score = float(raw)
        return max(0.0, min(1.0, score))   # clamp to [0, 1]
    except ValueError:
        logger.warning("eval_llm returned non-numeric score: %r", raw)
        return 0.0


# ── Query decomposition ───────────────────────────────────────────────────────

_DECOMPOSE_PROMPT = """\
Break the following question into 2-3 simpler sub-questions that together cover
the original question. Return each sub-question on its own line, no numbering,
no explanation.

Question: {question}
"""


def _decompose_query(question: str) -> list[str]:
    """Split a complex question into simpler sub-questions."""
    raw = eval_llm(_DECOMPOSE_PROMPT.format(question=question))
    sub_questions = [q.strip() for q in raw.splitlines() if q.strip()]
    return sub_questions if sub_questions else [question]


# ── ACL filter builder ────────────────────────────────────────────────────────

def _build_acl_filter(user_role: str) -> dict:
    """
    Build a vector store metadata filter that restricts results to chunks
    the user_role is allowed to see.

    ChromaDB filter syntax: {"$contains": value} on a string field.
    The allowed_roles field was stored as a comma-joined string in ChromaStore.
    """
    return {"allowed_roles": {"$contains": user_role}}


# ── Core retrieval ────────────────────────────────────────────────────────────

def _retrieve_and_score(
    question: str,
    user_role: str,
    threshold: float,
    top_k: int = TOP_K,
) -> list[ScoredChunk]:
    """
    Retrieve top_k chunks for question (with ACL filter), verify HMAC,
    scrub PII at retrieval, and score each chunk.

    Returns all chunks with relevance ≥ threshold.
    """
    from lightrag import QueryParam

    rag = get_rag()
    acl_filter = _build_acl_filter(user_role)

    # LightRAG retrieval — hybrid mode uses both vector and graph
    raw_results = rag.query(
        question,
        param=QueryParam(
            mode="hybrid",
            top_k=top_k,
            metadata_filter=acl_filter,
        ),
    )

    scored: list[ScoredChunk] = []
    for result in raw_results:
        # result is expected to be a dict with 'text', 'metadata', 'distance'
        chunk_text = result.get("text", "")
        meta = result.get("metadata", {})

        # HMAC tamper detection
        sig = meta.get("hmac_signature", "")
        tamper_ok = verify_chunk(chunk_text, sig) if sig else False
        if not tamper_ok:
            logger.warning(
                "HMAC verification FAILED for chunk from %s page %s — skipping",
                meta.get("source"),
                meta.get("page"),
            )
            continue   # never pass a tampered chunk to the LLM

        # Secondary PII scrub at retrieval
        clean_result = scrub(chunk_text)
        clean_text = clean_result.scrubbed_text

        score = _score_chunk(question, clean_text)

        if score >= threshold:
            scored.append(ScoredChunk(
                text=clean_text,
                metadata=meta,
                relevance=score,
                tamper_ok=tamper_ok,
            ))

    return scored


# ── Public entry point ────────────────────────────────────────────────────────

def crag_retrieve(
    question: str,
    user_role: str = "analyst",
) -> CRAGResult:
    """
    CRAG retrieval with self-correction.

    Args:
        question:  The user's natural-language question.
        user_role: Role from JWT — used for ACL filtering.

    Returns:
        A CRAGResult containing scored chunks, the query used, and sources.
        Always returns a CRAGResult — never raises on empty results.

    Algorithm:
        Pass 1: Retrieve with RELEVANCE_THRESHOLD.
        If correct chunks found → return immediately (fast path).
        Pass 2: Decompose query → sub-questions.
                Retry each with FALLBACK_THRESHOLD.
                Merge, deduplicate by source+page, return.
    """
    logger.info("CRAG pass 1: question=%r role=%s", question, user_role)
    pass1_chunks = _retrieve_and_score(question, user_role, RELEVANCE_THRESHOLD)

    if pass1_chunks:
        sources = _build_sources(pass1_chunks)
        return CRAGResult(
            chunks=pass1_chunks,
            query_used=question,
            decomposed=False,
            sources=sources,
        )

    # No chunks above threshold — decompose and retry
    logger.info("CRAG pass 1 found no correct chunks — decomposing query")
    sub_questions = _decompose_query(question)
    logger.info("Sub-questions: %s", sub_questions)

    merged: dict[str, ScoredChunk] = {}
    for sub_q in sub_questions:
        sub_chunks = _retrieve_and_score(sub_q, user_role, FALLBACK_THRESHOLD)
        for chunk in sub_chunks:
            key = f"{chunk.metadata.get('source')}::{chunk.metadata.get('page')}::{chunk.metadata.get('chunk_index')}"
            # Keep highest-scoring version if the same chunk appears across sub-queries
            if key not in merged or chunk.relevance > merged[key].relevance:
                merged[key] = chunk

    final_chunks = sorted(merged.values(), key=lambda c: c.relevance, reverse=True)
    sources = _build_sources(final_chunks)

    return CRAGResult(
        chunks=final_chunks,
        query_used=" | ".join(sub_questions),
        decomposed=True,
        sources=sources,
    )


def _build_sources(chunks: list[ScoredChunk]) -> list[dict]:
    """Build a deduplicated source citation list from scored chunks."""
    seen: set[str] = set()
    sources: list[dict] = []
    for chunk in chunks:
        key = f"{chunk.metadata.get('source')}::{chunk.metadata.get('page')}"
        if key not in seen:
            seen.add(key)
            sources.append({
                "source": chunk.metadata.get("source"),
                "page": chunk.metadata.get("page"),
                "relevance": round(chunk.relevance, 3),
            })
    return sources