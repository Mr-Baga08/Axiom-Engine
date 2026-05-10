"""
llm_client.py
─────────────
Provider-agnostic LLM abstraction for the agent loop.

Set  LLM_PROVIDER=anthropic  (default) or  LLM_PROVIDER=gemini  in .env.
Set  GEMINI_AGENT_MODEL  to override the default gemini-2.5-flash.

The abstraction exposes only what the agent loop needs:
    reset()           — start a fresh conversation
    complete()        → LLMResponse  (normalised stop_reason, text, tool_calls, tokens)
    add_tool_results() — append results so the next complete() continues the turn
    simple_complete()  → str  (single-turn text, no tools — used by din_sql)

Each provider keeps its own message history internally so the agent
loop never handles provider-specific objects.
"""

from __future__ import annotations

import json
import os
import warnings

# Suppress the google.generativeai end-of-life notice. The package functions
# correctly on 0.8.x; the warning fires on every import and pollutes logs.
warnings.filterwarnings(
    "ignore",
    message=".*google.generativeai.*",
    category=FutureWarning,
)

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# ── Public types ─────────────────────────────────────────────────────────────

@dataclass
class ToolCallBlock:
    tool_id: str
    tool_name: str
    tool_input: dict


@dataclass
class LLMResponse:
    stop_reason: str          # "end_turn" | "tool_use"
    text: str                 # populated when stop_reason == "end_turn"
    tool_calls: list[ToolCallBlock]
    input_tokens: int
    output_tokens: int


# ── Abstract base ─────────────────────────────────────────────────────────────

class LLMClient(ABC):
    model_name: str

    @abstractmethod
    def reset(self, system: str, first_user_message: str) -> None:
        """Initialise (or re-initialise) the conversation."""

    @abstractmethod
    def complete(self) -> LLMResponse:
        """Run one generation step and return a normalised response."""

    @abstractmethod
    def add_tool_results(self, results: list[dict]) -> None:
        """
        Queue tool results for the next complete() call.
        results: [{"id": tool_call_id, "content": json_str_or_text}]
        """

    @abstractmethod
    def simple_complete(self, prompt: str) -> str:
        """Single-turn text generation without tools. Used by din_sql."""


# ── Anthropic implementation ──────────────────────────────────────────────────

class AnthropicClient(LLMClient):
    def __init__(self, tools: list[dict], model_name: str) -> None:
        import anthropic as _anthropic
        self._client = _anthropic.Anthropic()
        self.model_name = model_name
        self._tools = tools
        self._system = ""
        self._messages: list[dict] = []

    def reset(self, system: str, first_user_message: str) -> None:
        self._system = system
        self._messages = [{"role": "user", "content": first_user_message}]

    def complete(self) -> LLMResponse:
        import anthropic as _anthropic
        response = self._client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            system=self._system,
            tools=self._tools,
            messages=self._messages,
        )

        if response.stop_reason == "end_turn":
            text = next(
                (b.text for b in response.content if hasattr(b, "text")), ""
            )
            self._messages.append({"role": "assistant", "content": response.content})
            return LLMResponse(
                stop_reason="end_turn",
                text=text,
                tool_calls=[],
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )

        # tool_use
        tool_calls = [
            ToolCallBlock(tool_id=b.id, tool_name=b.name, tool_input=b.input)
            for b in response.content
            if getattr(b, "type", None) == "tool_use"
        ]
        self._messages.append({"role": "assistant", "content": response.content})
        return LLMResponse(
            stop_reason="tool_use",
            text="",
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    def add_tool_results(self, results: list[dict]) -> None:
        tool_result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": r["id"],
                "content": r["content"],
            }
            for r in results
        ]
        self._messages.append({"role": "user", "content": tool_result_blocks})

    def simple_complete(self, prompt: str) -> str:
        m = self._client.messages.create(
            model=self.model_name,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return m.content[0].text


# ── Gemini implementation ─────────────────────────────────────────────────────

def _strip_unsupported_schema_keys(schema: dict) -> dict:
    """Recursively remove JSON Schema keys Gemini's parameter spec rejects."""
    UNSUPPORTED = {"additionalProperties", "$schema", "$defs", "$ref", "default"}
    result = {k: v for k, v in schema.items() if k not in UNSUPPORTED}

    if "properties" in result:
        result["properties"] = {
            k: _strip_unsupported_schema_keys(v)
            for k, v in result["properties"].items()
        }

    # Gemini requires items for array types
    if result.get("type") == "array" and "items" not in result:
        result["items"] = {"type": "object"}

    return result


def _to_gemini_tools(anthropic_tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool-use schema to Gemini function_declarations format."""
    declarations = []
    for tool in anthropic_tools:
        declarations.append({
            "name": tool["name"],
            "description": tool["description"],
            "parameters": _strip_unsupported_schema_keys(tool["input_schema"]),
        })
    return [{"function_declarations": declarations}]


class GeminiClient(LLMClient):
    def __init__(self, tools: list[dict], model_name: str) -> None:
        import google.generativeai as genai

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. "
                "Get a key at https://aistudio.google.com/apikey"
            )
        genai.configure(api_key=api_key)

        import google.generativeai.protos as _protos

        self._genai = genai
        self._protos = _protos
        self.model_name = model_name
        self._gemini_tools = _to_gemini_tools(tools)
        self._chat: Any = None
        self._pending_send: Any = None
        self._last_tool_calls: dict[str, ToolCallBlock] = {}

    def reset(self, system: str, first_user_message: str) -> None:
        # Force the first generation to call a tool (mode=ANY).
        # After tool results arrive, add_tool_results() switches to AUTO so
        # the model can give a final text answer on subsequent rounds.
        self._system = system
        model = self._genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system,
            tools=self._gemini_tools,
            tool_config={"function_calling_config": {"mode": "ANY"}},
        )
        self._chat = model.start_chat(history=[])
        self._pending_send = first_user_message
        self._last_tool_calls = {}
        self._forced_first_call = True

    def complete(self) -> LLMResponse:
        import re
        import time

        protos = self._protos
        from observability.token_cost import extract_usage_gemini

        if self._pending_send is None:
            raise RuntimeError("complete() called with no pending message")

        for attempt in range(4):
            try:
                response = self._chat.send_message(self._pending_send)
                break
            except Exception as exc:
                err = str(exc)
                if ("resource_exhausted" in err.lower() or "429" in err or "quota" in err.lower()) and attempt < 3:
                    m = re.search(r"retry in (\d+(?:\.\d+)?)\s*s", err, re.IGNORECASE)
                    wait = min(float(m.group(1)) if m else 15, 65)
                    log.warning("gemini_rate_limit_retry", attempt=attempt + 1, wait_s=wait)
                    time.sleep(wait)
                else:
                    raise

        self._pending_send = None

        input_t, output_t = extract_usage_gemini(response)

        # Detect tool calls by presence of function_call parts (Gemini uses
        # finish_reason=STOP even when returning function calls)
        tool_calls: list[ToolCallBlock] = []
        text_parts: list[str] = []

        for candidate in response.candidates:
            for part in candidate.content.parts:
                fc = getattr(part, "function_call", None)
                if fc and fc.name:
                    tc = ToolCallBlock(
                        tool_id=f"call_{fc.name}_{len(tool_calls)}",
                        tool_name=fc.name,
                        tool_input=dict(fc.args),
                    )
                    tool_calls.append(tc)
                    self._last_tool_calls[tc.tool_id] = tc
                elif getattr(part, "text", None):
                    text_parts.append(part.text)

        if tool_calls:
            return LLMResponse(
                stop_reason="tool_use",
                text="",
                tool_calls=tool_calls,
                input_tokens=input_t,
                output_tokens=output_t,
            )

        return LLMResponse(
            stop_reason="end_turn",
            text="".join(text_parts),
            tool_calls=[],
            input_tokens=input_t,
            output_tokens=output_t,
        )

    def add_tool_results(self, results: list[dict]) -> None:
        protos = self._protos
        parts: list[Any] = []
        for r in results:
            tc = self._last_tool_calls.get(r["id"])
            if tc is None:
                log.warning("gemini_unknown_tool_id", tool_id=r["id"])
                continue

            try:
                parsed = json.loads(r["content"])
            except (json.JSONDecodeError, TypeError):
                parsed = None

            # FunctionResponse.response must be a dict (proto Struct).
            # SQL results are lists of row dicts — wrap them.
            if isinstance(parsed, list):
                content = {"rows": parsed}
            elif isinstance(parsed, dict):
                content = parsed
            else:
                content = {"result": str(r["content"])}

            parts.append(
                protos.Part(
                    function_response=protos.FunctionResponse(
                        name=tc.tool_name,
                        response=content,
                    )
                )
            )

        if parts:
            self._pending_send = parts

        # After the first tool call is answered, switch to AUTO mode so the
        # model can either call more tools or give a final text answer.
        if getattr(self, "_forced_first_call", False):
            self._forced_first_call = False
            new_model = self._genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=self._system,
                tools=self._gemini_tools,
                tool_config={"function_calling_config": {"mode": "AUTO"}},
            )
            self._chat = new_model.start_chat(history=self._chat.history)

    def simple_complete(self, prompt: str) -> str:
        model = self._genai.GenerativeModel(model_name=self.model_name)
        response = model.generate_content(prompt)
        return response.text.strip()


# ── Factory ───────────────────────────────────────────────────────────────────

_ANTHROPIC_AGENT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
_GEMINI_AGENT_MODEL = os.getenv("GEMINI_AGENT_MODEL", "gemini-2.5-flash")


def make_llm_client(tools: list[dict]) -> LLMClient:
    """
    Return an LLMClient for the provider named in LLM_PROVIDER.
    Defaults to anthropic.
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()

    if provider == "gemini":
        log.info("llm_provider", provider="gemini", model=_GEMINI_AGENT_MODEL)
        return GeminiClient(tools=tools, model_name=_GEMINI_AGENT_MODEL)

    log.info("llm_provider", provider="anthropic", model=_ANTHROPIC_AGENT_MODEL)
    return AnthropicClient(tools=tools, model_name=_ANTHROPIC_AGENT_MODEL)
