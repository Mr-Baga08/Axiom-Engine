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

    from lightrag import LightRAG, QueryParam  # noqa: F401 – QueryParam used by callers
    import chromadb

    embed_model = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
    persist_dir = os.getenv("CHROMA_PERSIST_DIR", "data/chroma")

    chroma_client = chromadb.PersistentClient(path=persist_dir)

    _rag = LightRAG(
        embedding_model=embed_model,
        vector_db=chroma_client,
        # working_dir stores LightRAG's internal KV graph cache
        working_dir=os.path.join(persist_dir, "lightrag_graph"),
    )

    logger.info("LightRAG initialised (embed_model=%s)", embed_model)
    return _rag


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