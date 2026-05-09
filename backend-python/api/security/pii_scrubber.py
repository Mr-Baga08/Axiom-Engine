"""
PII scrubbing using Microsoft Presidio.

Scrub is applied at two points:
    1. During document ingestion (before storing to DB).
    2. Before any text is passed to the LLM (final context assembly).

The scrubber is initialised once at module load (Presidio is expensive to
construct). It is thread-safe and can be shared across workers.
"""
from __future__ import annotations

from typing import NamedTuple

from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# ── Engine singletons ──────────────────────────────────────────────────────────

_analyzer = AnalyzerEngine()
_anonymizer = AnonymizerEngine()

# ── PII entity types to detect ─────────────────────────────────────────────────
# Extend this list as your data catalogue requires.

DETECTED_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "IBAN_CODE",
    "IP_ADDRESS",
    "LOCATION",
    "DATE_TIME",
    "NRP",            # Nationality, religion, political group
    "MEDICAL_LICENSE",
    "URL",
]

# Replacement tag shown in scrubbed text, e.g. <PERSON> <EMAIL_ADDRESS>
_OPERATOR = OperatorConfig("replace", {"new_value": None})  # None → use entity type tag


# ── Public API ─────────────────────────────────────────────────────────────────

class ScrubResult(NamedTuple):
    scrubbed_text: str
    findings: list[dict]   # serialisable list of detected entity dicts
    pii_detected: bool


def scrub(text: str, language: str = "en") -> ScrubResult:
    """
    Analyse text for PII and return the anonymised version.

    Args:
        text: The input string (document chunk, user query, etc.).
        language: BCP-47 language code for the Presidio NLP engine.

    Returns:
        A ScrubResult with the cleaned text, a list of finding dicts,
        and a boolean indicating whether any PII was found.

    Notes:
        - The original text is never stored by this function.
        - findings is safe to store in audit / lineage tables (no raw PII).
    """
    if not text or not text.strip():
        return ScrubResult(scrubbed_text=text, findings=[], pii_detected=False)

    results: list[RecognizerResult] = _analyzer.analyze(
        text=text,
        entities=DETECTED_ENTITIES,
        language=language,
    )

    if not results:
        return ScrubResult(scrubbed_text=text, findings=[], pii_detected=False)

    anonymized = _anonymizer.anonymize(
        text=text,
        analyzer_results=results,
        operators={"DEFAULT": _OPERATOR},
    )

    findings = [
        {
            "type": r.entity_type,
            "start": r.start,
            "end": r.end,
            "score": round(r.score, 4),
        }
        for r in results
    ]

    return ScrubResult(
        scrubbed_text=anonymized.text,
        findings=findings,
        pii_detected=True,
    )