"""
Gemini evaluation client.

Used exclusively for RAGAS evaluation (relevance scoring, faithfulness scoring)
and the CRAG relevance evaluator. The main agent continues to use Anthropic Claude.

Why a separate module:
  - Clean separation of eval LLM from main LLM — easier to swap one without
    affecting the other.
  - Gemini Flash is ~50x cheaper per token than Claude Opus, making it
    cost-effective to call per-chunk during retrieval.
  - RAGAS requires a LangChain-compatible LLM wrapper — this module provides
    one without polluting the main pipeline with LangChain dependencies.

Two clients are exposed:
  get_gemini_client()      — raw google.generativeai client for direct calls
                             (used by CRAG eval_llm replacement in Step 12)
  get_langchain_gemini()   — LangChain wrapper for RAGAS evaluate() calls
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

_gemini_client = None
_langchain_gemini = None

GEMINI_EVAL_MODEL: str = os.getenv("GEMINI_EVAL_MODEL", "gemini-1.5-flash")


def get_gemini_client():
    """
    Return the raw Gemini GenerativeModel singleton.
    Raises RuntimeError if GOOGLE_API_KEY is not set.
    """
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_API_KEY is not set in .env. "
            "Get a key at https://aistudio.google.com/apikey"
        )

    import google.generativeai as genai
    genai.configure(api_key=api_key)
    _gemini_client = genai.GenerativeModel(model_name=GEMINI_EVAL_MODEL)
    logger.info("Gemini eval client initialised", model=GEMINI_EVAL_MODEL)
    return _gemini_client


def get_langchain_gemini():
    """
    Return a LangChain ChatGoogleGenerativeAI wrapper around the Gemini eval model.
    This wrapper is required by RAGAS — it implements the LangChain BaseChatModel
    interface that ragas.evaluate() expects.

    temperature=0 for determinism — evaluation scores must be reproducible.
    """
    global _langchain_gemini
    if _langchain_gemini is not None:
        return _langchain_gemini

    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY is not set in .env")

    from langchain_google_genai import ChatGoogleGenerativeAI
    _langchain_gemini = ChatGoogleGenerativeAI(
        model=GEMINI_EVAL_MODEL,
        google_api_key=api_key,
        temperature=0,
        convert_system_message_to_human=True,  # Gemini doesn't have system role
    )
    return _langchain_gemini


def gemini_generate(prompt: str, temperature: float = 0.0, max_tokens: int = 64) -> str:
    """
    Synchronous single-turn Gemini generation.

    Used as a direct replacement for eval_llm() in crag.py — same interface,
    different backend. temperature=0 by default for scoring consistency.

    Args:
        prompt:      The full prompt string.
        temperature: Generation temperature (0.0 = deterministic).
        max_tokens:  Maximum tokens in the response (keep low for eval calls).

    Returns:
        The response text, stripped of leading/trailing whitespace.

    Raises:
        RuntimeError: if GOOGLE_API_KEY is not set.
        google.api_core.exceptions.GoogleAPIError: on API failure (not caught
        here — callers should wrap in try/except and return a safe default).
    """
    import google.generativeai as genai

    client = get_gemini_client()
    config = genai.types.GenerationConfig(
        temperature=temperature,
        max_output_tokens=max_tokens,
    )
    response = client.generate_content(prompt, generation_config=config)

    try:
        from observability.token_cost import extract_usage_gemini, record_token_cost
        input_tokens, output_tokens = extract_usage_gemini(response)
        record_token_cost(GEMINI_EVAL_MODEL, input_tokens, output_tokens)
    except Exception:
        pass   # cost recording must never fail an eval call

    return response.text.strip()
