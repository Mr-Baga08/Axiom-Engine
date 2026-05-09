"""
Embedding pipeline: PDF chunks → SentenceTransformer → pgvector (PostgreSQL).

Replaces the ChromaDB-based implementation. Embeddings are stored directly
in the ``document_chunks`` table using an ivfflat index for cosine similarity
search. HMAC signatures are computed per chunk to enable tamper detection
at retrieval time (verified in crag.py).

Functions
─────────
  embed_and_store(pool, chunks) → int   rows inserted
"""

from __future__ import annotations

import hashlib
import hmac as hmac_lib
import logging
import os
from typing import Any

import asyncpg
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

EMBED_MODEL_NAME: str = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_BATCH_SIZE: int = 64
HMAC_SECRET: bytes = os.getenv("CHUNK_HMAC_SECRET", "dev-hmac").encode()

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        logger.info("Loading embedding model: %s", EMBED_MODEL_NAME)
        _model = SentenceTransformer(EMBED_MODEL_NAME)
    return _model


def _compute_hmac(text: str) -> str:
    sig = hmac_lib.new(HMAC_SECRET, text.encode(), hashlib.sha256)
    return sig.hexdigest()


def _embed_batch(texts: list[str]) -> list[list[float]]:
    model = _get_model()
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return vectors.tolist()


async def embed_and_store(
    pool: asyncpg.Pool,
    chunks: list[dict[str, Any]],
) -> int:
    """
    Embed each chunk and insert into ``document_chunks``.

    Each dict must have a ``text`` key. Optional keys:
      source, page, access_level, allowed_roles

    Returns:
        Number of rows inserted.
    """
    if not chunks:
        return 0

    texts = [c["text"] for c in chunks]
    all_embeddings: list[list[float]] = []

    for i in range(0, len(texts), EMBED_BATCH_SIZE):
        batch = texts[i : i + EMBED_BATCH_SIZE]
        all_embeddings.extend(_embed_batch(batch))
        logger.debug("Embedded batch %d-%d", i, i + len(batch))

    rows = [
        (
            chunk.get("source", "unknown"),
            chunk.get("page"),
            chunk["text"],
            embedding,
            chunk.get("access_level", "internal"),
            chunk.get("allowed_roles", ["analyst", "executive"]),
            _compute_hmac(chunk["text"]),
        )
        for chunk, embedding in zip(chunks, all_embeddings)
    ]

    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO document_chunks
              (source, page, chunk_text, embedding, access_level, allowed_roles, hmac_signature)
            VALUES ($1, $2, $3, $4::vector, $5, $6, $7)
            """,
            rows,
        )

    logger.info("Stored %d chunks with embeddings", len(rows))
    return len(rows)
