"""
DIN-SQL: Decomposed In-context learning for text-to-SQL.

Four-stage pipeline (mirrors the DIN-SQL paper):
  Stage 1 – Schema Linking      : identify which tables/columns are relevant
  Stage 2 – Query Classification: simple / nested / set-operation
  Stage 3 – Sub-query generation: only for nested queries
  Stage 4 – SQL assembly        : final SELECT, temperature=0

Every stage uses a separate LLM call with an explicit, narrow prompt.
Separation prevents a single malformed instruction from corrupting the
output of later stages.

The final SQL is passed through sql_guard() before being returned.
If sql_guard() raises, the error is propagated to the caller — DIN-SQL
does not retry automatically (retries are the caller's responsibility).

Usage:
    from python.api.pipeline.din_sql import query_sql

    result_df = query_sql("Which genre had the highest revenue in 2025?")
"""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd

from observability.observe import observe
from pipeline.schema import get_schema_ddl
from pipeline.sql_guard import SQLGuardError, sql_guard

logger = logging.getLogger(__name__)


# ── LLM backend ────────────────────────────────────────────────────────────────
# DIN-SQL is LLM-agnostic. We default to the OpenAI chat completions API
# because Marvin is already configured with an OpenAI key.
# To swap backends (Anthropic, local Ollama, etc.), replace _call_llm() only.

def _call_llm(prompt: str, temperature: float = 0.2) -> str:
    """
    Single-turn LLM call for DIN-SQL chain steps.
    Routes to Gemini or Anthropic based on LLM_PROVIDER env var (default: anthropic).
    Retries up to 3 times on transient rate-limit errors with exponential backoff.
    """
    import time

    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()

    for attempt in range(3):
        try:
            if provider == "gemini":
                import warnings
                warnings.filterwarnings("ignore", message=".*generativeai.*", category=FutureWarning)
                import google.generativeai as genai

                api_key = os.environ.get("GOOGLE_API_KEY")
                if not api_key:
                    raise RuntimeError("GOOGLE_API_KEY is not set")
                genai.configure(api_key=api_key)
                model_name = os.getenv("GEMINI_AGENT_MODEL", "gemini-2.5-flash")
                model = genai.GenerativeModel(
                    model_name=model_name,
                    generation_config=genai.types.GenerationConfig(temperature=temperature),
                )
                response = model.generate_content(prompt)
                return response.text.strip()

            # Default: Anthropic
            import anthropic
            model_name = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
            client = anthropic.Anthropic()
            m = client.messages.create(
                model=model_name,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            return m.content[0].text.strip()

        except Exception as exc:
            if attempt == 2:
                raise
            err = str(exc).lower()
            if "quota" in err or "rate" in err or "429" in err or "resource_exhausted" in err:
                wait = 2 ** attempt + 1
                logger.warning("din_sql_llm_rate_limit, retrying in %ds", wait)
                time.sleep(wait)
            else:
                raise


# ── Stage prompts ──────────────────────────────────────────────────────────────
# These are templates — keep them terse. Verbose prompts increase cost and
# introduce ambiguity.

_LINK_PROMPT = """\
Given the schema below, list every table.column pair that is relevant to answering
the question. Output a plain bulleted list only — no explanation.

Schema:
{schema}

Question: {question}
"""

_CLASSIFY_PROMPT = """\
Classify the SQL query type needed to answer the question, given the relevant schema
elements listed below. Reply with exactly one word: simple, nested, or set-operation.

Relevant schema elements:
{linked}

Question: {question}
"""

_SUBQUERY_PROMPT = """\
Write only the inner subquery needed to answer the question.
Output SQL only — no explanation, no markdown fences.

Relevant schema elements:
{linked}

Question: {question}
"""

_FINAL_SQL_PROMPT = """\
Write a single SQL SELECT statement that answers the question exactly.
Output SQL only — no explanation, no markdown fences, no trailing semicolon.

Schema elements:
{linked}

Query type: {qtype}
{subquery_hint}
Question: {question}
"""


# ── Pipeline ───────────────────────────────────────────────────────────────────

@observe(name="din_sql", tags=["sql", "text-to-sql"])
def din_sql(
    question: str,
    schema: str | None = None,
    examples: list[dict] | None = None,
) -> str:
    """
    Run the four-stage DIN-SQL pipeline and return a validated SQL string.

    Args:
        question: The natural-language question from the user.
        schema:   Optional pre-computed DDL string. If None, get_schema_ddl()
                  is called automatically.
        examples: Reserved for few-shot examples (Phase 2 enhancement).
                  Not used in Phase 1 — pass None or omit.

    Returns:
        A validated SQL SELECT string, safe to execute.

    Raises:
        SQLGuardError: if the generated SQL fails any guard check.
        openai.OpenAIError: if the LLM call fails.
    """
    if schema is None:
        schema = get_schema_ddl()

    # Stage 1 – Schema linking
    linked = _call_llm(
        _LINK_PROMPT.format(schema=schema, question=question),
        temperature=0.1,
    )
    logger.debug("DIN-SQL stage 1 (linked): %s", linked)

    # Stage 2 – Query classification
    qtype = _call_llm(
        _CLASSIFY_PROMPT.format(linked=linked, question=question),
        temperature=0.1,
    ).lower().strip()
    logger.debug("DIN-SQL stage 2 (qtype): %s", qtype)

    # Normalise — LLMs sometimes return "nested query" instead of "nested"
    if "nested" in qtype:
        qtype = "nested"
    elif "set" in qtype:
        qtype = "set-operation"
    else:
        qtype = "simple"

    # Stage 3 – Sub-query (nested only)
    subq = ""
    if qtype == "nested":
        subq = _call_llm(
            _SUBQUERY_PROMPT.format(linked=linked, question=question),
            temperature=0.1,
        )
        logger.debug("DIN-SQL stage 3 (subquery): %s", subq)

    subquery_hint = f"Inner subquery:\n{subq}\n" if subq else ""

    # Stage 4 – Final SQL assembly (temperature=0 for determinism)
    raw_sql = _call_llm(
        _FINAL_SQL_PROMPT.format(
            linked=linked,
            qtype=qtype,
            subquery_hint=subquery_hint,
            question=question,
        ),
        temperature=0,
    )
    logger.debug("DIN-SQL stage 4 (raw sql): %s", raw_sql)

    # Guard before returning
    return sql_guard(raw_sql)


# ── Public query entry point ───────────────────────────────────────────────────

def query_sql(
    question: str,
    user_role: str = "analyst",
    schema: str | None = None,
) -> pd.DataFrame:
    """
    End-to-end: natural language → validated SQL → DataFrame.

    Args:
        question:  Natural-language question from the user.
        user_role: The role string from the JWT ('analyst', 'executive', etc.).
                   Injected into the DB session for RLS enforcement (PostgreSQL only).
        schema:    Pre-computed schema DDL (cached from a previous call is fine).

    Returns:
        A pandas DataFrame with the query results.

    Raises:
        SQLGuardError: if the LLM generates unsafe SQL.
        Exception:     propagated from the DB driver on execution errors.
    """
    sql = din_sql(question=question, schema=schema)
    logger.info("Executing SQL for role=%s: %s", user_role, sql)

    backend = os.getenv("DB_BACKEND", "duckdb").lower()
    from ..pipeline.db import get_db_connection

    conn = get_db_connection()
    try:
        if backend == "postgres":
            # Inject the user role into the session so PostgreSQL RLS policies
            # can read it via current_setting('app.current_user_role').
            # This must happen in the same session as the query.
            with conn.cursor() as cur:
                # Use a safe parameterised approach — do not f-string the role
                # directly into the SET command.
                cur.execute(
                    "SELECT set_config('app.current_user_role', %s, true)",
                    (user_role,),
                )
                cur.execute(sql)
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
            return pd.DataFrame(rows, columns=columns)

        else:  # duckdb
            # DuckDB has no RLS — role enforcement is handled at the FastAPI
            # layer via the require_scope() dependency (see security task).
            result = conn.execute(sql)
            return result.df()

    finally:
        conn.close()