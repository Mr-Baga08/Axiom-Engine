"""
Vector store factory.

Returns a store object that supports .add() and .query() with the same
call signature regardless of backend (ChromaDB for dev, pgvector for prod).

Both backends store the same metadata fields produced by pdf_loader.py.

ChromaDB:
  - File-persisted at CHROMA_PERSIST_DIR.
  - Collection name: 'dumb_lens_docs'.
  - Distance metric: cosine.

pgvector:
  - Uses the asyncpg pool on app.state.db_pool (set up in Phase 1).
  - Embeddings stored in the 'doc_embeddings' table (schema below).
  - Requires the pgvector extension (already enabled in Phase 1 init.sql).

Add to infra/postgres/init.sql before running migrations:

    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS doc_embeddings (
        id              BIGSERIAL PRIMARY KEY,
        chunk_text      TEXT        NOT NULL,
        embedding       vector(384),             -- all-MiniLM-L6-v2 dim
        source          TEXT        NOT NULL,
        page            INTEGER     NOT NULL,
        chunk_index     INTEGER     NOT NULL,
        access_level    TEXT        NOT NULL DEFAULT 'internal',
        allowed_roles   TEXT[]      NOT NULL,
        hmac_signature  TEXT        NOT NULL,
        pii_detected    BOOLEAN     NOT NULL DEFAULT FALSE,
        inserted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_doc_embeddings_roles
        ON doc_embeddings USING GIN (allowed_roles);

    CREATE INDEX IF NOT EXISTS idx_doc_embeddings_vec
        ON doc_embeddings USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100);
"""

from __future__ import annotations

import os
from typing import Any, Protocol


# ── Shared protocol ───────────────────────────────────────────────────────────

class VectorStore(Protocol):
    def add(self, texts: list[str], embeddings: list[list[float]], metadatas: list[dict]) -> None:
        ...

    def query(
        self,
        embedding: list[float],
        n_results: int,
        where: dict | None,
    ) -> list[dict[str, Any]]:
        ...


# ── ChromaDB (dev) ────────────────────────────────────────────────────────────

class ChromaStore:
    """Thin wrapper around a ChromaDB collection."""

    _COLLECTION = "dumb_lens_docs"

    def __init__(self) -> None:
        import chromadb
        persist_dir = os.getenv("CHROMA_PERSIST_DIR", "data/chroma")
        os.makedirs(persist_dir, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._col = self._client.get_or_create_collection(
            self._COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        """
        Insert a batch of chunks.
        IDs are derived from source + page + chunk_index to be idempotent —
        re-inserting the same chunk updates rather than duplicates.
        """
        ids = [
            f"{m['source']}::p{m['page']}::c{m['chunk_index']}"
            for m in metadatas
        ]
        # ChromaDB metadata values must be str, int, float, or bool.
        # Coerce lists (allowed_roles) to comma-separated strings.
        safe_metas = [
            {
                k: ",".join(v) if isinstance(v, list) else v
                for k, v in m.items()
            }
            for m in metadatas
        ]
        self._col.upsert(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=safe_metas,
        )

    def query(
        self,
        embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return up to n_results nearest chunks.

        `where` is a ChromaDB metadata filter dict, e.g.:
            {"allowed_roles": {"$contains": "analyst"}}

        Returns a list of dicts each with keys: text, metadata, distance.
        """
        kwargs: dict[str, Any] = {
            "query_embeddings": [embedding],
            "n_results": n_results,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        raw = self._col.query(**kwargs)

        results = []
        for text, meta, dist in zip(
            raw["documents"][0],
            raw["metadatas"][0],
            raw["distances"][0],
        ):
            # Re-expand comma-joined role string back to list
            if "allowed_roles" in meta and isinstance(meta["allowed_roles"], str):
                meta = {**meta, "allowed_roles": meta["allowed_roles"].split(",")}
            results.append({"text": text, "metadata": meta, "distance": dist})

        return results


# ── pgvector (prod) ───────────────────────────────────────────────────────────

class PgVectorStore:
    """
    asyncpg-backed vector store using the pgvector extension.
    Requires app.state.db_pool to be set (FastAPI lifespan).
    """

    def __init__(self, pool) -> None:
        self._pool = pool

    def add(
        self,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        raise NotImplementedError(
            "PgVectorStore.add() is async — use add_async() from async contexts."
        )

    async def add_async(
        self,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
    ) -> None:
        import json
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO doc_embeddings
                    (chunk_text, embedding, source, page, chunk_index,
                     access_level, allowed_roles, hmac_signature, pii_detected)
                VALUES ($1, $2::vector, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT DO NOTHING
                """,
                [
                    (
                        text,
                        str(emb),          # pgvector accepts '[0.1,0.2,...]' strings
                        m["source"],
                        m["page"],
                        m["chunk_index"],
                        m["access_level"],
                        m["allowed_roles"],
                        m["hmac_signature"],
                        m.get("pii_detected", False),
                    )
                    for text, emb, m in zip(texts, embeddings, metadatas)
                ],
            )

    async def query(
        self,
        embedding: list[float],
        n_results: int = 10,
        where: dict | None = None,
    ) -> list[dict[str, Any]]:
        """
        ANN search using <=> (cosine distance) operator.
        `where` supports: {"allowed_roles": "analyst"} →
            WHERE $role = ANY(allowed_roles)
        """
        role_filter = where.get("allowed_roles") if where else None
        emb_str = str(embedding)

        async with self._pool.acquire() as conn:
            if role_filter:
                rows = await conn.fetch(
                    """
                    SELECT chunk_text, source, page, chunk_index,
                           access_level, allowed_roles, hmac_signature,
                           embedding <=> $1::vector AS distance
                    FROM doc_embeddings
                    WHERE $2 = ANY(allowed_roles)
                    ORDER BY distance
                    LIMIT $3
                    """,
                    emb_str, role_filter, n_results,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT chunk_text, source, page, chunk_index,
                           access_level, allowed_roles, hmac_signature,
                           embedding <=> $1::vector AS distance
                    FROM doc_embeddings
                    ORDER BY distance
                    LIMIT $2
                    """,
                    emb_str, n_results,
                )

        return [
            {
                "text": r["chunk_text"],
                "metadata": {
                    "source": r["source"],
                    "page": r["page"],
                    "chunk_index": r["chunk_index"],
                    "access_level": r["access_level"],
                    "allowed_roles": list(r["allowed_roles"]),
                    "hmac_signature": r["hmac_signature"],
                },
                "distance": float(r["distance"]),
            }
            for r in rows
        ]


# ── Factory ───────────────────────────────────────────────────────────────────

def get_vector_store(pool=None) -> ChromaStore | PgVectorStore:
    """
    Return the appropriate vector store based on DB_BACKEND.

    Args:
        pool: asyncpg pool — required when DB_BACKEND=postgres.

    Raises:
        ValueError: if DB_BACKEND=postgres but pool is None.
    """
    backend = os.getenv("DB_BACKEND", "duckdb").lower()
    if backend == "postgres":
        if pool is None:
            raise ValueError("pool is required for PgVectorStore")
        return PgVectorStore(pool)
    return ChromaStore()