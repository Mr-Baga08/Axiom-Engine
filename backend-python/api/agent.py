"""
agent.py
─────────
Main agent loop with LangFuse tracing, token cost accounting, and RAGAS eval.

Responsibilities
----------------
- Accept user question + auth context
- Dispatch to: query_sql | retrieve_docs | generate_chart | compute_metric
- Max 6 tool-call rounds
- Record token cost after every Anthropic call
- After final answer: log RAG evaluation to Phoenix (if docs were retrieved)
- Return structured response with answer, sources, tool_trace
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import structlog

from llm_client import make_llm_client
from observability.tracing import (
    observe,
    get_trace_context,
    record_token_cost,
    get_prompt,
)
from pipeline.din_sql import din_sql
from pipeline.crag import crag_retrieve
from tools.pal_sandbox import exec_pal

log = structlog.get_logger(__name__)

MAX_ROUNDS = 6

# ---------------------------------------------------------------------------
# Tool definitions (Anthropic tool-use format)
# ---------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "query_sql",
        "description": (
            "Execute a natural-language question against the structured entertainment "
            "database (movies, viewers, watch_activity, reviews, marketing_spend, "
            "regional_performance). Returns tabular data as JSON."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Natural-language question"},
                "filters": {
                    "type": "object",
                    "description": "Optional key-value filters, e.g. {\"genre\": \"Drama\"}",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "retrieve_docs",
        "description": (
            "Search the PDF document corpus (quarterly reports, trend analyses) "
            "using semantic retrieval. Returns relevant text chunks with sources."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Semantic search query"},
                "top_k": {"type": "integer", "description": "Max chunks to return (1-10)", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "generate_chart",
        "description": (
            "Produce a chart specification (Recharts-compatible JSON) from tabular data. "
            "Supports line, bar, pie, and scatter chart types."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data":       {"type": "array",  "description": "Array of data objects"},
                "chart_type": {"type": "string",  "enum": ["line", "bar", "pie", "scatter"]},
                "x_key":      {"type": "string",  "description": "Key for x-axis"},
                "y_keys":     {"type": "array",   "items": {"type": "string"}, "description": "Keys for y-axis series"},
                "title":      {"type": "string",  "description": "Chart title"},
            },
            "required": ["data", "chart_type", "x_key", "y_keys"],
        },
    },
    {
        "name": "compute_metric",
        "description": (
            "Execute a sandboxed Python computation (PAL) to calculate derived metrics "
            "such as YoY growth, ROI, averages, or percentages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code snippet. Must end with print(result).",
                },
            },
            "required": ["code"],
        },
    },
]

# Role → allowed tools mapping (RBAC)
_ROLE_TOOLS: Dict[str, set] = {
    "analyst":   {"query_sql", "retrieve_docs", "generate_chart", "compute_metric"},
    "executive": {"query_sql", "retrieve_docs", "generate_chart", "compute_metric"},
    "viewer":    {"retrieve_docs", "generate_chart"},
}


# ---------------------------------------------------------------------------
# Agent entrypoint
# ---------------------------------------------------------------------------

@observe(name="run_agent", tags=["agent", "llm"])
async def run_agent(
    question: str,
    token_data: Dict[str, Any],
    db,           # DuckDB or asyncpg connection
    schema: str,  # DDL string from get_schema_ddl()
    examples: List[Dict],
) -> Dict[str, Any]:
    """
    Run the main agent loop.

    Returns
    -------
    {
        "answer": str,
        "sources": list,
        "tool_trace": list,
        "total_tokens": int,
        "cost_usd": float,
    }
    """
    user_id   = token_data.get("sub", "anonymous")
    user_role = token_data.get("role", "viewer")
    ctx       = get_trace_context()

    log.info(
        "agent_start",
        question=question[:120],
        user_id=user_id,
        user_role=user_role,
        trace_id=ctx.trace_id if ctx else None,
    )

    allowed_tools = _ROLE_TOOLS.get(user_role, set())

    # System prompt: base persona + DSPy compiled few-shot demonstrations.
    # The few-shot block is loaded once at startup from data/dspy_compiled.json
    # (or data/dspy_examples.json as fallback) and costs zero extra API calls.
    base_persona = (
        "You are an expert entertainment analytics assistant.\n"
        "Use the tools available to answer questions about movies, viewers, "
        "watch activity, reviews, marketing spend, and regional performance.\n"
        "Always cite the source of any numbers you state (SQL query or document).\n"
        "Be concise and precise."
    )

    few_shot_block = ""
    if examples:
        demos = examples[:4]  # cap at 4 to avoid excessive prompt length
        lines = ["\n\nExamples of well-reasoned answers:\n"]
        for ex in demos:
            lines.append(f"Q: {ex.get('question', '')}")
            lines.append(f"A: {ex.get('answer', '')}\n")
        few_shot_block = "\n".join(lines)

    system_prompt = base_persona + few_shot_block

    llm = make_llm_client(tools=TOOLS)
    llm.reset(system=system_prompt, first_user_message=question)

    tool_trace: List[Dict]  = []
    sources:    List[str]   = []
    total_cost: float       = 0.0
    retrieved_chunks:  List = []
    final_answer: str       = ""

    # ── Agent loop (max MAX_ROUNDS rounds) ───────────────────────────────
    for round_num in range(MAX_ROUNDS):
        log.info("agent_round", round=round_num + 1)

        response = llm.complete()

        # Track token cost
        cost = record_token_cost(
            model=llm.model_name,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        total_cost += cost

        # ── end_turn: extract final answer ────────────────────────────────
        if response.stop_reason == "end_turn":
            final_answer = response.text
            log.info("agent_end_turn", round=round_num + 1)
            break

        # ── tool_use: dispatch tools ──────────────────────────────────────
        if response.stop_reason == "tool_use":
            tool_results = []

            for tc in response.tool_calls:
                # RBAC check
                if tc.tool_name not in allowed_tools:
                    log.warning("tool_rbac_denied", tool=tc.tool_name, role=user_role)
                    tool_results.append({
                        "id":      tc.tool_id,
                        "content": f"Access denied: role '{user_role}' cannot use '{tc.tool_name}'",
                    })
                    continue

                # Dispatch
                result_data, result_sources = await _dispatch_tool(
                    tool_name=tc.tool_name,
                    tool_input=tc.tool_input,
                    db=db,
                    schema=schema,
                    examples=examples,
                    user_role=user_role,
                    chunks_accumulator=retrieved_chunks,
                )

                sources.extend(result_sources)
                tool_trace.append({
                    "round":  round_num + 1,
                    "tool":   tc.tool_name,
                    "input":  {k: str(v)[:200] for k, v in tc.tool_input.items()},
                    "output": json.dumps(result_data, default=str),
                })

                tool_results.append({
                    "id":      tc.tool_id,
                    "content": json.dumps(result_data, default=str),
                })

            llm.add_tool_results(tool_results)

    # ── Post-answer: Phoenix RAG eval with final answer for faithfulness ──
    if retrieved_chunks and final_answer:
        from observability.phoenix_eval import log_rag_evaluation
        try:
            await log_rag_evaluation(
                query=question,
                chunks=retrieved_chunks,
                final_answer=final_answer,
                trace_id=ctx.trace_id if ctx else None,
            )
        except Exception as exc:
            log.warning("post_agent_phoenix_eval_failed", error=str(exc))

    # Record faithfulness AFTER answer is available — not before.
    # Uses the new split-timing eval (Gemini-backed, separate Redis key).
    try:
        from observability.rag_eval import record_faithfulness
        chunk_texts = [
            c.get("text", "") if isinstance(c, dict) else getattr(c, "text", "")
            for c in retrieved_chunks
        ]
        chunk_texts = [t for t in chunk_texts if t]
        if chunk_texts and final_answer:
            record_faithfulness(
                query=question,
                chunks=chunk_texts,
                answer=final_answer,
            )
    except Exception:
        pass

    log.info(
        "agent_complete",
        answer_preview=final_answer[:120],
        total_cost_usd=round(total_cost, 6),
        num_tool_calls=len(tool_trace),
    )

    return {
        "answer":       final_answer,
        "sources":      list(set(sources)),
        "tool_trace":   tool_trace,
        "total_tokens": ctx.total_tokens if ctx else 0,
        "cost_usd":     round(total_cost, 6),
    }


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

async def _dispatch_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    db,
    schema: str,
    examples: List[Dict],
    user_role: str,
    chunks_accumulator: List,
) -> tuple[Any, List[str]]:
    """Execute a single tool and return (result_data, sources)."""

    sources: List[str] = []

    if tool_name == "query_sql":
        question = tool_input["question"]
        filters  = tool_input.get("filters", {})

        sql = din_sql(question=question, schema=schema, examples=examples)

        # Execute SQL
        try:
            if hasattr(db, "execute"):          # DuckDB
                result_df = db.execute(sql).df()
                data = result_df.to_dict(orient="records")
            else:                               # asyncpg
                rows = await db.fetch(sql)
                data = [dict(r) for r in rows]
            sources.append(f"sql:{sql[:80]}")
        except Exception as exc:
            log.warning("sql_exec_failed", sql=sql[:200], error=str(exc))
            data = {"error": str(exc)}

        return data, sources

    elif tool_name == "retrieve_docs":
        query = tool_input["query"]

        crag_result = crag_retrieve(
            question=query,
            user_role=user_role,
        )
        chunks = crag_result.chunks
        chunks_accumulator.extend(chunks)
        sources.extend(c.metadata.get("source", "unknown") for c in chunks)

        return [
            {
                "text": c.text[:500],
                "source": c.metadata.get("source", "unknown"),
                "score": c.relevance,
            }
            for c in chunks
        ], sources

    elif tool_name == "generate_chart":
        # Chart spec construction (consumed by React frontend)
        spec = {
            "type":  tool_input.get("chart_type", "bar"),
            "title": tool_input.get("title", ""),
            "data":  tool_input.get("data", []),
            "xKey":  tool_input.get("x_key", ""),
            "yKeys": tool_input.get("y_keys", []),
        }
        return spec, []

    elif tool_name == "compute_metric":
        code   = tool_input["code"]
        result = exec_pal(code)
        return {"result": result}, ["pal_computation"]

    else:
        return {"error": f"Unknown tool: {tool_name}"}, []