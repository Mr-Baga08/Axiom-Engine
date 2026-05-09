"""
Token cost recording — YAML pricing, streaming + non-streaming paths.

Streaming vs non-streaming (Anthropic SDK):
  Non-streaming response:
    response.usage.input_tokens   → int
    response.usage.output_tokens  → int

  Streaming response (accumulated via MessageStream):
    The SDK accumulates usage in the final MessageStreamEvent of type
    'message_delta' with a 'usage' field containing 'output_tokens'.
    The input_tokens are in the 'message_start' event.
    Callers using stream.get_final_usage() after the stream closes get
    a single Usage object — treat this the same as non-streaming.

    If usage is not available (early stream termination), return 0 cost
    and log a warning — never raise.

Google Gemini usage:
  response.usage_metadata.prompt_token_count    → int
  response.usage_metadata.candidates_token_count → int

Both paths are handled in record_token_cost().
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

_pricing: dict[str, dict] | None = None


def _load_pricing() -> dict[str, dict]:
    global _pricing
    if _pricing is not None:
        return _pricing

    config_path = Path(os.getenv("MODEL_PRICING_CONFIG", "config/model_pricing.yaml"))
    if not config_path.exists():
        logger.warning(
            "Pricing config not found at %s — token costs will be $0.00. "
            "Create config/model_pricing.yaml to enable cost tracking.",
            config_path,
        )
        _pricing = {}
        return _pricing

    with config_path.open() as fh:
        raw = yaml.safe_load(fh)

    _pricing = raw.get("models", {})
    logger.info("Loaded pricing for %d models from %s", len(_pricing), config_path)
    return _pricing


def get_model_price(model: str) -> dict[str, float]:
    """
    Return {'input_per_million': float, 'output_per_million': float} for a model.
    Returns zeros if the model is not in the pricing config.
    """
    pricing = _load_pricing()
    if model not in pricing:
        logger.warning(
            "Model %r not in pricing config — cost recorded as $0.00. "
            "Add it to config/model_pricing.yaml.",
            model,
        )
        return {"input_per_million": 0.0, "output_per_million": 0.0}
    return pricing[model]


def _calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return total cost in USD for a given token usage."""
    prices = get_model_price(model)
    input_cost = (input_tokens / 1_000_000) * prices["input_per_million"]
    output_cost = (output_tokens / 1_000_000) * prices["output_per_million"]
    return round(input_cost + output_cost, 8)


def extract_usage_anthropic(response_or_usage: Any) -> tuple[int, int]:
    """
    Extract (input_tokens, output_tokens) from an Anthropic response object.

    Handles both:
      - Non-streaming: anthropic.types.Message (has .usage attribute)
      - Streaming accumulated: anthropic.types.Usage (returned by
        stream.get_final_usage() or from message_delta event)
      - Raw usage dict: {"input_tokens": N, "output_tokens": N}

    Returns (0, 0) on any failure — cost recording must never raise.
    """
    try:
        if isinstance(response_or_usage, tuple) and len(response_or_usage) == 2:
            return int(response_or_usage[0]), int(response_or_usage[1])

        if isinstance(response_or_usage, dict):
            return (
                int(response_or_usage.get("input_tokens", 0)),
                int(response_or_usage.get("output_tokens", 0)),
            )

        usage = getattr(response_or_usage, "usage", response_or_usage)
        return (
            int(getattr(usage, "input_tokens", 0)),
            int(getattr(usage, "output_tokens", 0)),
        )
    except Exception as exc:
        logger.warning("Could not extract Anthropic usage: %s", exc)
        return 0, 0


def extract_usage_gemini(response: Any) -> tuple[int, int]:
    """
    Extract (input_tokens, output_tokens) from a Gemini response object.

    Gemini SDK: response.usage_metadata.prompt_token_count
                response.usage_metadata.candidates_token_count

    Returns (0, 0) on any failure.
    """
    try:
        meta = getattr(response, "usage_metadata", None)
        if meta is None:
            return 0, 0
        return (
            int(getattr(meta, "prompt_token_count", 0)),
            int(getattr(meta, "candidates_token_count", 0)),
        )
    except Exception as exc:
        logger.warning("Could not extract Gemini usage: %s", exc)
        return 0, 0


def record_token_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    trace_id: Optional[str] = None,
) -> float:
    """
    Calculate cost and attach it to the current LangFuse trace.

    Args:
        model:         Model name exactly as it appears in model_pricing.yaml.
        input_tokens:  Prompt token count.
        output_tokens: Completion token count.
        trace_id:      LangFuse trace ID to attach the cost to. If None,
                       uses the current TraceContext.

    Returns:
        Total cost in USD as a float (useful for test assertions).
    """
    cost = _calculate_cost(model, input_tokens, output_tokens)

    if trace_id is None:
        try:
            from trace_context import get_trace_context
            trace_id = get_trace_context().trace_id
        except Exception:
            pass

    try:
        from langfuse_client import get_client
        lf = get_client()
        if lf is not None and trace_id:
            lf.score(
                trace_id=trace_id,
                name="token_cost_usd",
                value=cost,
                comment=f"{model}: {input_tokens} in / {output_tokens} out",
            )
    except Exception as exc:
        logger.warning("Could not record token cost in LangFuse: %s", exc)

    logger.debug(
        "Token cost: model=%s input=%d output=%d cost=$%.6f",
        model, input_tokens, output_tokens, cost,
    )
    return cost
