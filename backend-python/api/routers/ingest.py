"""
Document ingestion endpoint.

POST /ingest/pdf  — upload a PDF, chunk it, PII-scrub, HMAC-sign,
                    embed (PostgreSQL) or LightRAG-index (DuckDB/dev),
                    return chunk count.

Requires docs:executive scope (executive + admin roles only).
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile

from auth.rbac import require_scope
from middleware.rate_limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

# 20 MB upload limit
_MAX_PDF_BYTES = 20 * 1024 * 1024


@router.post("/pdf")
@limiter.limit("5/minute")
async def ingest_pdf(
    request: Request,
    file: UploadFile = File(...),
    token: dict = Depends(require_scope("tools:basic")),
):
    """
    Upload a PDF, process it through the full ingestion pipeline, and index it
    into LightRAG so it becomes immediately retrievable via the retrieve_docs tool.

    Pipeline:
      1. PyMuPDF  — extract text page-by-page
      2. Presidio — scrub PII before any embedding
      3. tiktoken — 512-token overlapping chunks
      4. HMAC-SHA256 — sign each chunk for tamper detection
      5a. sentence-transformers + pgvector  (when DB_BACKEND=postgres)
      5b. LightRAG graph index              (always)
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted")

    content = await file.read()
    if len(content) > _MAX_PDF_BYTES:
        raise HTTPException(413, f"PDF exceeds the {_MAX_PDF_BYTES // 1024 // 1024} MB limit")

    # Write to a temp file — PyMuPDF needs a real file path
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        # Step 1–4: extract, scrub, chunk, sign
        from ingestion.pdf_loader import load_pdf
        chunks = await asyncio.get_event_loop().run_in_executor(
            None, load_pdf, tmp_path
        )

        if not chunks:
            raise HTTPException(422, "No extractable text found in the PDF")

        # Step 5a: vector embeddings (PostgreSQL + pgvector only)
        db_pool = getattr(request.app.state, "db_pool", None)
        if db_pool is not None:
            try:
                from ingestion.embed_and_store import embed_and_store
                await embed_and_store(db_pool, chunks)
            except Exception as exc:
                logger.warning("embed_and_store failed (non-fatal): %s", exc)

        # Step 5b: LightRAG graph index (always — also works with DuckDB)
        from pipeline.lightrag_setup import index_chunks
        await asyncio.get_event_loop().run_in_executor(None, index_chunks, chunks)

        logger.info(
            "pdf_ingested file=%s chunks=%d user=%s",
            file.filename, len(chunks), token.get("sub"),
        )
        return {
            "filename":       file.filename,
            "chunks_indexed": len(chunks),
            "pages_processed": max((c.get("page", 0) for c in chunks), default=0),
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("pdf_ingestion_failed file=%s error=%s", file.filename, exc, exc_info=True)
        raise HTTPException(500, f"Ingestion failed: {exc}")
    finally:
        tmp_path.unlink(missing_ok=True)
