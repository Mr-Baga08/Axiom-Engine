"""
FastAPI application entrypoint — axiom-engine
Single app, single lifespan. All middleware, routes, and routers wired here.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from auth.rbac import require_scope
from agent import run_agent
from auth.jwt_handler import verify_token, _bearer
from middleware.rls_context import RLSContextMiddleware
from observability.audit import record
from observability.logging_config import configure_logging, get_logger
from observability.tracing import get_trace_context, register_prompts, trace_context
from observability.phoenix_eval import ensure_phoenix_running
from security.input_validator import validate_query

configure_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    json_output=os.getenv("LOG_FORMAT", "json") == "json",
)
log = get_logger("main")


# ── DuckDB auto-initialisation ─────────────────────────────────────────────────

def _ensure_duckdb_data(conn) -> None:
    """
    Load CSVs into DuckDB via read_csv_auto() if tables are missing or empty.
    Called once at startup so DIN-SQL always has a populated schema to work with.
    """
    data_dir = Path(os.getenv("DATA_DIR", "data/csv"))
    tables = {
        "movies":               "movies.csv",
        "viewers":              "viewers.csv",
        "watch_activity":       "watch_activity.csv",
        "reviews":              "reviews.csv",
        "marketing_spend":      "marketing_spend.csv",
        "regional_performance": "regional_performance.csv",
    }
    for table, filename in tables.items():
        csv_path = data_dir / filename
        if not csv_path.exists():
            log.warning("csv_not_found_skipping", table=table, path=str(csv_path))
            continue
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count > 0:
                continue  # already loaded
        except Exception:
            pass  # table doesn't exist yet — create it
        try:
            conn.execute(
                f"CREATE OR REPLACE TABLE {table} AS "
                f"SELECT * FROM read_csv_auto('{csv_path}')"
            )
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            log.info("duckdb_table_loaded", table=table, rows=n)
        except Exception as exc:
            log.error("duckdb_table_load_failed", table=table, error=str(exc))


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 0. Vault secrets → env vars (graceful fallback to .env)
    try:
        from auth.vault_client import get_secrets
        secrets = get_secrets()
        os.environ.setdefault("ANTHROPIC_API_KEY",   secrets.get("anthropic_api_key", ""))
        os.environ.setdefault("LANGFUSE_SECRET_KEY", secrets.get("langfuse_secret", ""))
        os.environ.setdefault("LANGFUSE_PUBLIC_KEY", secrets.get("langfuse_public", ""))
        os.environ.setdefault("JWT_SECRET",          secrets.get("jwt_secret", ""))
        os.environ.setdefault("CHUNK_HMAC_SECRET",   secrets.get("chunk_hmac_secret", ""))
        os.environ.setdefault("SLACK_WEBHOOK_URL",   secrets.get("slack_webhook", ""))
    except Exception as exc:
        log.warning("vault_secrets_load_failed", error=str(exc))

    # 1. Database connection
    backend = os.getenv("DB_BACKEND", "duckdb").lower()
    if backend == "postgres":
        from pipeline.db import create_async_pool
        app.state.db_pool = await create_async_pool()
        app.state.db = None
    else:
        from pipeline.db import get_db_connection
        app.state.db = get_db_connection()
        app.state.db_pool = None
        # Auto-load CSVs so schema is always populated for DIN-SQL
        _ensure_duckdb_data(app.state.db)

    # 2. Pre-compute schema DDL for DIN-SQL (must run AFTER data is loaded)
    try:
        from pipeline.schema import get_schema_ddl
        app.state.schema_ddl = get_schema_ddl()
        log.info("schema_ddl_loaded")
    except Exception as exc:
        log.warning("schema_ddl_load_failed", error=str(exc))
        app.state.schema_ddl = ""

    # 3. Load DSPy compiled few-shot demos (if available)
    # These are injected into the agent system prompt at inference time.
    # Produce them by running: python scripts/generate_examples.py
    #                    then: python -m pipeline.compile_dspy
    few_shots: list[dict] = []
    # /repo is the project root volume mount inside Docker; fall back to relative path for local dev
    _data_root = Path(os.getenv("REPO_ROOT", "/repo"))
    compiled_path = Path(os.getenv("DSPY_OUTPUT_PATH", str(_data_root / "data" / "dspy_compiled.json")))
    examples_path = Path(os.getenv("EXAMPLES_PATH",    str(_data_root / "data" / "dspy_examples.json")))

    for candidate in (compiled_path, examples_path):
        try:
            if candidate.exists():
                raw = json.loads(candidate.read_text())
                # Compiled state has demos under predict.demos; raw examples are a list
                if isinstance(raw, list):
                    few_shots = raw
                elif isinstance(raw, dict):
                    # DSPy ChainOfThought compiled state
                    few_shots = (
                        raw.get("predict", {}).get("demos", []) or
                        raw.get("demos", []) or
                        []
                    )
                if few_shots:
                    log.info("dspy_few_shots_loaded", source=str(candidate), count=len(few_shots))
                    break
        except Exception as exc:
            log.warning("dspy_load_failed", path=str(candidate), error=str(exc))

    app.state.few_shot_examples = few_shots

    # 3. LangFuse prompt registry
    try:
        register_prompts()
    except Exception:
        pass
    try:
        from observability.langfuse_client import register_prompts as rp2
        rp2()
    except Exception:
        pass

    # 4. Phoenix eval server
    try:
        ensure_phoenix_running()
    except Exception:
        pass
    try:
        from observability.phoenix_setup import start_phoenix
        start_phoenix()
    except Exception:
        pass

    # 5. RAGAS watchdog background task
    app.state.watchdog_task = None
    try:
        from observability.watchdog import start_watchdog
        app.state.watchdog_task = start_watchdog(db_pool=app.state.db_pool)
    except Exception:
        pass

    log.info("app_startup_complete", backend=backend)
    yield

    # Shutdown
    if getattr(app.state, "watchdog_task", None):
        app.state.watchdog_task.cancel()
        try:
            await app.state.watchdog_task
        except asyncio.CancelledError:
            pass
    if getattr(app.state, "db_pool", None):
        await app.state.db_pool.close()
    if getattr(app.state, "db", None):
        app.state.db.close()
    log.info("app_shutdown")


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Axiom Engine API",
    version="1.0.0",
    description="Secure AI Insights Assistant — entertainment analytics agent",
    lifespan=lifespan,
)

# ── Rate limiter ───────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# ── Middleware stack ───────────────────────────────────────────────────────────
# Starlette applies in reverse registration order (last registered = outermost).
# Execution order: CORS → LoggingMiddleware → RLS → handler.

app.add_middleware(RLSContextMiddleware)

try:
    from middleware.logging_middleware import LoggingMiddleware
    app.add_middleware(LoggingMiddleware)
except Exception:
    pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Per-request trace context ──────────────────────────────────────────────────

@app.middleware("http")
async def trace_context_middleware(request: Request, call_next):
    user_id   = getattr(request.state, "user_id",  "anonymous")
    user_role = getattr(request.state, "user_role", "unknown")
    session   = request.headers.get("X-Session-Id", str(uuid.uuid4()))
    with trace_context(user_id=user_id, user_role=user_role, session_id=session, tags=["api"]):
        return await call_next(request)


# ── Auth dependency ────────────────────────────────────────────────────────────

async def get_current_user(request: Request, token_data=Depends(verify_token)):
    request.state.user_id   = token_data.get("sub", "anonymous")
    request.state.user_role = token_data.get("role", "viewer")
    return token_data


# ── Exception handlers ─────────────────────────────────────────────────────────

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded. Please slow down."})


@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    db = getattr(app.state, "db", None) or getattr(app.state, "db_pool", None)
    if db:
        try:
            credentials = await _bearer(request)
            payload = verify_token(credentials)
            await record(
                db, token=payload, action="access_denied",
                resource=str(request.url.path),
                ip_address=request.client.host,
                status="denied",
            )
        except Exception:
            pass
    return JSONResponse(status_code=403, content={"detail": "Insufficient permissions"})


@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    log.error("unhandled_exception", error=str(exc), path=request.url.path, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── Request models ─────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2048)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2048)
    session_id: Optional[str] = None


# ── Shared agent dispatch helper ───────────────────────────────────────────────

async def _dispatch_agent(request: Request, message: str, token: dict) -> dict:
    """Sanitise input, run the agent, push history, return result dict."""
    import bleach
    clean = bleach.clean(message, tags=[], strip=True)
    if not clean.strip():
        raise HTTPException(400, "Question cannot be empty after sanitisation")

    db       = getattr(request.app.state, "db_pool", None) or getattr(request.app.state, "db", None)
    schema   = getattr(request.app.state, "schema_ddl", "")
    examples = getattr(request.app.state, "few_shot_examples", [])

    try:
        result = await run_agent(
            question=clean,
            token_data=token,
            db=db,
            schema=schema,
            examples=examples,
        )
    except Exception as exc:
        log.error("agent_error", error=str(exc), exc_info=True)
        raise HTTPException(500, "Agent error — check server logs")

    # Persist to per-user Redis history
    try:
        from routers.history import push_history_entry
        push_history_entry(
            user_id=token.get("sub", "anonymous"),
            query=clean,
            answer_preview=result.get("answer", "")[:150],
            tools_used=list({e["tool"] for e in result.get("tool_trace", [])}),
        )
    except Exception:
        pass

    return result


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "version": app.version}


@app.post("/api/chat", tags=["chat"])
@limiter.limit("10/minute")
async def api_chat(
    request: Request,
    body: ChatRequest,
    token: dict = Depends(require_scope("query:read")),
):
    """
    Main chat endpoint — returns full JSON response.
    Called by the Next.js API route which also falls back here when the Go
    SSE gateway is unavailable.
    """
    result = await _dispatch_agent(request, body.message, token)
    ctx = get_trace_context()
    return {
        "answer":       result["answer"],
        "sources":      result["sources"],
        "tool_trace":   result["tool_trace"],
        "total_tokens": result["total_tokens"],
        "cost_usd":     result["cost_usd"],
        "trace_id":     ctx.trace_id if ctx else None,
    }


@app.post("/internal/query", tags=["internal"])
@limiter.limit("10/minute")
async def internal_query_sse(
    request: Request,
    body: ChatRequest,
    token: dict = Depends(require_scope("query:read")),
):
    """
    SSE streaming endpoint consumed by the Go SSE gateway (:8080/stream).
    Emits: tool_call events → word-by-word token stream → done event.
    The Go gateway proxies these events directly to the browser.
    """
    async def _generate():
        try:
            result = await _dispatch_agent(request, body.message, token)
        except HTTPException as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': exc.detail})}\n\n"
            return
        except Exception as exc:
            log.error("sse_agent_error", error=str(exc), exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': 'Agent error'})}\n\n"
            return

        # Emit each tool call so the UI can render the trace live
        for entry in result.get("tool_trace", []):
            event = {
                "type":  "tool_call",
                "tool":  entry["tool"],
                "args":  entry["input"],
                "round": entry.get("round", 0),
            }
            yield f"data: {json.dumps(event)}\n\n"

        # Stream the answer word-by-word for a typing effect
        answer = result.get("answer", "")
        for word in answer.split(" "):
            yield f"data: {json.dumps({'type': 'token', 'content': word + ' '})}\n\n"

        # Final done event carries full metadata for history / cost display
        done = {
            "type":         "done",
            "answer":       answer,
            "tool_trace":   result.get("tool_trace", []),
            "sources":      result.get("sources", []),
            "total_tokens": result.get("total_tokens", 0),
            "cost_usd":     result.get("cost_usd", 0.0),
        }
        yield f"data: {json.dumps(done)}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "Connection":       "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/observability/status", tags=["observability"])
async def observability_status():
    lf_ok = bool(os.getenv("LANGFUSE_SECRET_KEY"))
    try:
        import phoenix as _px  # noqa: F401
        phoenix_ok = True
    except ImportError:
        phoenix_ok = False
    try:
        import ragas  # noqa: F401
        ragas_ok = True
    except ImportError:
        ragas_ok = False
    return {
        "langfuse":  {"available": lf_ok},
        "phoenix":   {"available": phoenix_ok},
        "ragas":     {"available": ragas_ok},
        "structlog": {"available": True},
    }


@app.get("/observability/scores", tags=["observability"])
async def get_rag_scores(token_data=Depends(get_current_user)):
    from observability.phoenix_eval import get_rolling_scores
    scores = await get_rolling_scores(n=50)
    if not scores:
        return {"scores": [], "avg_context_relevance": None, "avg_faithfulness": None}
    avg_cr = sum(s.get("context_relevance", 0) for s in scores) / len(scores)
    avg_f  = sum(s.get("faithfulness",      0) for s in scores) / len(scores)
    return {
        "scores":                scores,
        "avg_context_relevance": round(avg_cr, 4),
        "avg_faithfulness":      round(avg_f,  4),
        "window_size":           len(scores),
    }


# ── Routers ────────────────────────────────────────────────────────────────────

from routers.auth    import router as auth_router    # noqa: E402
from routers.query   import router as query_router   # noqa: E402
from routers.docs    import router as docs_router    # noqa: E402
from routers.admin   import router as admin_router   # noqa: E402
from routers.ingest  import router as ingest_router  # noqa: E402
from routers.history import router as history_router # noqa: E402

app.include_router(auth_router)
app.include_router(query_router)
app.include_router(docs_router)
app.include_router(admin_router)
app.include_router(ingest_router)
app.include_router(history_router)
