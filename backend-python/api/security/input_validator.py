"""
Input validation for all user-facing text fields.

Three layers:
    1. Pydantic schema — length cap and type enforcement.
    2. bleach — strips any HTML tags that slip through.
    3. rebuff — detects SQL injection and prompt injection patterns.

If any layer fails, a ValueError is raised with a safe error message.
The raw malicious input is never logged — only a sanitised summary.
"""
from __future__ import annotations

import re

import bleach
from pydantic import BaseModel, Field, field_validator

# Rebuff client — reads REBUFF_API_KEY from environment if using the hosted API.
# For self-hosted, pass api_url to the constructor.
try:
    from rebuff import Rebuff
    _rebuff = Rebuff()
    REBUFF_AVAILABLE = True
except Exception:
    # Rebuff is optional — if it cannot initialise (no API key, network error),
    # the other two layers still provide meaningful protection.
    _rebuff = None
    REBUFF_AVAILABLE = False


# ── Pydantic schema ────────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    """Validated incoming question from the chat frontend."""

    question: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="User question, max 2048 characters",
    )

    @field_validator("question", mode="before")
    @classmethod
    def strip_html(cls, v: str) -> str:
        """Strip all HTML tags before any further processing."""
        return bleach.clean(str(v), tags=[], strip=True)

    @field_validator("question", mode="after")
    @classmethod
    def no_null_bytes(cls, v: str) -> str:
        """Remove null bytes — these can break downstream parsers."""
        return v.replace("\x00", "")


# ── Injection detection ────────────────────────────────────────────────────────
# Basic SQL injection heuristic — patterns that strongly suggest an attempt.
# Rebuff provides a deeper ML-based check; this is a fast pre-filter.

_SQL_PATTERNS = re.compile(
    r"(\bUNION\b|\bSELECT\b|\bDROP\b|\bINSERT\b|\bDELETE\b|--\s|;.*\b(SELECT|DROP)\b)",
    re.IGNORECASE,
)


def validate_query(raw_question: str) -> str:
    """
    Validate and sanitise a user question.

    Steps:
        1. Run through QueryRequest (Pydantic + bleach).
        2. Check for SQL injection patterns.
        3. Check for prompt injection via Rebuff (if available).

    Args:
        raw_question: The raw string from the request body.

    Returns:
        The sanitised question string, safe to pass to the pipeline.

    Raises:
        ValueError: With a safe error message (never echoes raw input).
        fastapi.HTTPException: Not raised here — callers are responsible for
            wrapping ValueError into an HTTP 400 response.
    """
    # Layer 1: Pydantic + bleach
    try:
        validated = QueryRequest(question=raw_question)
    except Exception as exc:
        raise ValueError(f"Input validation failed: {exc}") from exc

    clean = validated.question

    # Layer 2: SQL injection heuristic
    if _SQL_PATTERNS.search(clean):
        raise ValueError("Input contains disallowed SQL patterns")

    # Layer 3: Rebuff prompt injection check
    if REBUFF_AVAILABLE and _rebuff is not None:
        try:
            result = _rebuff.detect_injection(clean)
            if result.injection_detected:
                raise ValueError("Potential prompt injection detected")
        except ValueError:
            raise
        except Exception:
            # Rebuff API unreachable — degrade gracefully, do not block request
            pass

    return clean