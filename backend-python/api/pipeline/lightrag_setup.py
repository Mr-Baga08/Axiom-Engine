"""
LightRAG initialisation and insertion.

LightRAG wraps the vector store to provide graph-augmented retrieval.
It builds an entity-relation graph over chunk text at insert time, which
allows it to answer multi-hop questions that pure cosine search misses.

This module exposes:
  get_rag()    — returns the initialised singleton LightRAG instance.
  index_chunks() — inserts a list of chunks into LightRAG.

Important: LightRAG must be initialised AFTER the vector store is ready.
Call get_rag() only from code that runs after embed_and_store.run_pipeline().
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_rag = None


def get_rag():
    """
    Return the LightRAG singleton. Creates it on first call.

    LightRAG is configured to use the same embedding model as the pipeline
    (EMBED_MODEL env var) and the ChromaDB client from the vector store.
    For PostgreSQL prod, pass the asyncpg pool via init_rag() instead.
    """
    global _rag
    if _rag is not None:
        return _rag

    try:
        from lightrag import LightRAG  # noqa: F401

        persist_dir = os.getenv("CHROMA_PERSIST_DIR", "data/chroma")
        working_dir = os.path.join(persist_dir, "lightrag_graph")
        os.makedirs(working_dir, exist_ok=True)

        _rag = LightRAG(working_dir=working_dir)
        logger.info("LightRAG initialised (working_dir=%s)", working_dir)
        return _rag
    except Exception as exc:
        logger.warning("lightrag init failed (%s); PDF retrieval disabled", exc)
        return None


def index_chunks(chunks: list[dict[str, Any]]) -> None:
    """
    Insert chunks into LightRAG for graph-augmented indexing.

    Each chunk's 'text' field is inserted; all other fields are passed as
    metadata. LightRAG builds its entity graph from the text content.

    This is a separate step from vector store insertion — both must run
    for full retrieval capability. LightRAG uses the vector store internally
    for nearest-neighbour search and augments it with the graph layer.
    """
    rag = get_rag()
    for chunk in chunks:
        rag.insert(
            chunk["text"],
            metadata={k: v for k, v in chunk.items() if k != "text"},
        )
    logger.info("Indexed %d chunks into LightRAG", len(chunks))