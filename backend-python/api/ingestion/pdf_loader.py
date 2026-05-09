"""
PDF loader with PII scrubbing and HMAC integrity signing.

Pipeline per PDF:
  1. fitz (PyMuPDF) extracts text page-by-page.
  2. Presidio scrubs PII from each page's text (reuses the scrubber from
     the security hardening task at python/api/security/pii_scrubber.py).
  3. Text is split into overlapping token-window chunks.
  4. Each chunk is HMAC-signed with HMAC_SECRET so tampering can be
     detected before the chunk is passed to the LLM.
  5. Chunks are returned as a list of metadata dicts, ready for embedding.

Token counting uses tiktoken with the cl100k_base encoding, which matches
the token budget of the embedding model closely enough for Phase 2.

HMAC algorithm: HMAC-SHA256. The signature covers only the chunk text —
metadata fields are mutable and must not be included in the signature.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Any

import fitz  # pymupdf
import tiktoken

from security.pii_scrubber import scrub

# ── Configuration ─────────────────────────────────────────────────────────────

CHUNK_TOKENS: int = int(os.getenv("CHUNK_TOKENS", "512"))
OVERLAP_TOKENS: int = int(os.getenv("CHUNK_OVERLAP_TOKENS", "64"))
HMAC_SECRET: bytes = os.getenv("HMAC_SECRET", "").encode()

if not HMAC_SECRET:
    raise RuntimeError(
        "HMAC_SECRET is not set in .env. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

_TOKENIZER = tiktoken.get_encoding("cl100k_base")


# ── HMAC helpers ──────────────────────────────────────────────────────────────

def sign_chunk(text: str) -> str:
    """Return the HMAC-SHA256 hex digest of chunk text."""
    return hmac.new(HMAC_SECRET, text.encode(), hashlib.sha256).hexdigest()


def verify_chunk(text: str, signature: str) -> bool:
    """
    Return True if the signature matches the chunk text.
    Uses hmac.compare_digest to prevent timing attacks.
    """
    expected = sign_chunk(text)
    return hmac.compare_digest(expected, signature)


# ── Tokeniser helpers ─────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[int]:
    return _TOKENIZER.encode(text)


def _detokenize(tokens: list[int]) -> str:
    return _TOKENIZER.decode(tokens)


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str, source: str, page_num: int) -> list[dict[str, Any]]:
    """
    Split `text` into overlapping token-window chunks.

    Returns a list of chunk dicts. Each dict has the structure expected by
    embed_and_store.py and LightRAG.

    Overlap implementation:
        The window slides forward by (CHUNK_TOKENS - OVERLAP_TOKENS) tokens
        on each step so consecutive chunks share OVERLAP_TOKENS tokens at
        their boundaries. This preserves sentence context across chunk edges.
    """
    tokens = _tokenize(text)
    if not tokens:
        return []

    stride = CHUNK_TOKENS - OVERLAP_TOKENS
    if stride <= 0:
        raise ValueError(
            f"OVERLAP_TOKENS ({OVERLAP_TOKENS}) must be less than "
            f"CHUNK_TOKENS ({CHUNK_TOKENS})"
        )

    chunks: list[dict[str, Any]] = []
    start = 0

    while start < len(tokens):
        end = min(start + CHUNK_TOKENS, len(tokens))
        chunk_text = _detokenize(tokens[start:end])
        signature = sign_chunk(chunk_text)

        chunks.append({
            "text": chunk_text,
            "page": page_num,
            "source": source,
            "chunk_index": len(chunks),
            "access_level": "internal",
            "allowed_roles": ["analyst", "executive"],
            "hmac_signature": signature,
        })

        if end == len(tokens):
            break
        start += stride

    return chunks


# ── Page extraction ───────────────────────────────────────────────────────────

def _extract_page_text(page: fitz.Page) -> str:
    """
    Extract text from a single PDF page.
    Uses 'text' mode (plain text, no layout reconstruction).
    """
    return page.get_text("text")


# ── Public API ────────────────────────────────────────────────────────────────

def load_pdf(
    pdf_path: Path,
    access_level: str = "internal",
    allowed_roles: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Parse a PDF into PII-scrubbed, HMAC-signed chunks.

    Args:
        pdf_path:      Path to the PDF file.
        access_level:  Access classification tag stored in chunk metadata.
        allowed_roles: List of roles that may retrieve this document.
                       Defaults to ["analyst", "executive"].

    Returns:
        A flat list of chunk dicts, one per overlapping window across all pages.
        Pages with no extractable text are silently skipped.

    Raises:
        FileNotFoundError: if pdf_path does not exist.
        RuntimeError:      if PyMuPDF cannot open the file.
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    if allowed_roles is None:
        allowed_roles = ["analyst", "executive"]

    all_chunks: list[dict[str, Any]] = []
    source = pdf_path.name

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        raise RuntimeError(f"PyMuPDF could not open {pdf_path}: {exc}") from exc

    with doc:
        for page_num, page in enumerate(doc, start=1):
            raw_text = _extract_page_text(page)
            if not raw_text.strip():
                continue

            # PII scrubbing — reuses the Presidio-backed scrubber from the
            # security hardening task. The scrubbed text only is chunked;
            # the raw text is never stored.
            scrub_result = scrub(raw_text)
            clean_text = scrub_result.scrubbed_text

            page_chunks = _chunk_text(clean_text, source, page_num)

            # Override default metadata with caller-supplied values
            for chunk in page_chunks:
                chunk["access_level"] = access_level
                chunk["allowed_roles"] = allowed_roles
                chunk["pii_detected"] = scrub_result.pii_detected

            all_chunks.extend(page_chunks)

    return all_chunks


def load_all_pdfs(
    pdf_dir: Path | None = None,
    access_level: str = "internal",
    allowed_roles: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Load every PDF in pdf_dir.

    Returns a flat list of all chunks from all PDFs.
    Missing or unreadable files are logged and skipped.
    """
    import logging
    logger = logging.getLogger(__name__)

    if pdf_dir is None:
        pdf_dir = Path("data/pdfs")

    if not pdf_dir.exists():
        logger.warning("PDF directory not found: %s", pdf_dir)
        return []

    all_chunks: list[dict[str, Any]] = []
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        try:
            chunks = load_pdf(pdf_path, access_level, allowed_roles)
            logger.info("Loaded %s → %d chunks", pdf_path.name, len(chunks))
            all_chunks.extend(chunks)
        except Exception as exc:
            logger.error("Failed to load %s: %s", pdf_path.name, exc)

    return all_chunks