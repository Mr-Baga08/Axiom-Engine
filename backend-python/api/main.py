"""
FastAPI application entrypoint.

Wires together all security layers:
    - JWT authentication (RS256/HS256)
    - RBAC scope enforcement
    - Rate limiting via slowapi + Redis
    - PII scrubbing (applied in route handlers)
    - Input validation (applied in route handlers)
    - Audit logging (applied in route handlers)
    - Data lineage (applied in ingestion handlers)
    - RLS context injection (Phase 1 — PostgreSQL only)
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIASGIMiddleware

# ── Direct (non-relative) imports — PYTHONPATH must include backend-python/api/
from auth.rbac import require_scope
from agent import run_agent
from auth.jwt_handler import verify_token
from middleware.rate_limit import limiter
from middleware.rls_context import RLSContextMiddleware
from observability.audit import record
from routers.admin import router as admin_router
from routers.auth import router as auth_router
from routers.docs import router as docs_router
from routers.query import router as query_router
from routers.tools import router as tools_router
from security.input_validator import validate_query

# ── Observability bootstrap ────────────────────────────────────────────────
from observability.logging_config import configure_logging, get_logger
from observability.tracing import (
    get_trace_context,
    observe,
    register_prompts,
    trace_context,
)
from observability.phoenix_eval import ensure_phoenix_running
from observability.ragas_watchdog import ragas_watchdog

# Phase 5 observability additions (gracefully skip if not available)
try:
    from observability.langfuse_client import register_prompts as register_prompts_v2
except ImportError:
    def register_prompts_v2(): pass  # type: ignore[misc]

try:
    from observability.phoenix_setup import start_phoenix
except ImportError:
    def start_phoenix(): pass  # type: ignore[misc]

try:
    from observability.watchdog import start_watchdog
except ImportError:
    import asyncio as _asyncio
    def start_watchdog(db_pool=None):  # type: ignore[misc]
        return _asyncio.get_event_loop().create_task(_asyncio.sleep(0))

try:
    from middleware.logging_middleware import LoggingMiddleware
except ImportError:
    from starlette.middleware.base import BaseHTTPMiddleware
    class LoggingMiddleware(BaseHTTPMiddleware):  # type: ignore[misc]
        async def dispatch(self, request, call_next):
            return await call_next(request)
 
configure_logging(
    level=os.getenv("LOG_LEVEL", "INFO"),
    json_output=os.getenv("LOG_FORMAT", "json") == "json",
)
log = get_logger("main")

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
 
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    # 0. Load secrets from Vault (falls back to env vars when VAULT_ADDR is unset)
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

    log.info("app_startup")

    # 1. Register prompts in LangFuse (legacy + new client)
    register_prompts()
    try:
        register_prompts_v2()
    except Exception:
        pass

    # 2. Ensure Phoenix is running (legacy + new client)
    ensure_phoenix_running()
    try:
        start_phoenix()
    except Exception:
        pass

    # 3. Start RAGAS watchdog background task (new — with done-callback + min-samples guard)
    db_pool = getattr(app.state, "db_pool", None)
    app.state.watchdog_task = start_watchdog(db_pool=db_pool)
    log.info("ragas_watchdog_task_created")

    yield

    # Shutdown
    if hasattr(app.state, "watchdog_task"):
        app.state.watchdog_task.cancel()
        try:
            await app.state.watchdog_task
        except asyncio.CancelledError:
            pass
    log.info("app_shutdown")
 
 
# ---------------------------------------------------------------------------
# App construction
# ---------------------------------------------------------------------------
 
app = FastAPI(
    title="AI-Insights API",
    version="0.5.0",
    description="Entertainment analytics agent with full observability",
    lifespan=lifespan,
)
 
# ── CORS ──────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
# ── Structured logging middleware (new: sampling + TraceContext creation) ──
# Middleware registration order (last-registered = outermost):
# 1. LoggingMiddleware (outermost — creates TraceContext first, sets X-Trace-Id)
# 2. RLSContextMiddleware (reads TraceContext.role)
# 3. SlowAPIASGIMiddleware (rate limiting)
app.add_middleware(LoggingMiddleware)
 
 
# ---------------------------------------------------------------------------
# Per-request TraceContext middleware
# ---------------------------------------------------------------------------
 
@app.middleware("http")
async def trace_context_middleware(request: Request, call_next):
    """
    Extract user identity from the verified JWT (set by auth dependency)
    and create a TraceContext for the duration of the request.
    """
    # These will be populated if auth runs first; fall back to anonymous
    user_id   = getattr(request.state, "user_id",   "anonymous")
    user_role = getattr(request.state, "user_role",  "unknown")
    session   = request.headers.get("X-Session-Id", str(uuid.uuid4()))
 
    with trace_context(
        user_id=user_id,
        user_role=user_role,
        session_id=session,
        tags=["api"],
    ):
        return await call_next(request)
 
 
# ---------------------------------------------------------------------------
# Auth dependency (JWT)
# ---------------------------------------------------------------------------
 
from auth.jwt_handler import verify_token, _bearer   # noqa: E402  (project module)
 
 
async def get_current_user(request: Request, token_data=Depends(verify_token)):
    # Inject identity into request.state so middleware picks it up
    request.state.user_id   = token_data.get("sub", "anonymous")
    request.state.user_role = token_data.get("role", "viewer")
    return token_data
 
 
# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
 
from slowapi import Limiter                     # noqa: E402
from slowapi.util import get_remote_address     # noqa: E402
from slowapi.middleware import SlowAPIMiddleware # noqa: E402
 
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
 
 
# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
 
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2048)
    session_id: Optional[str] = None
 
 
class QueryResponse(BaseModel):
    answer:       str
    sources:      list
    tool_trace:   list
    total_tokens: int
    cost_usd:     float
    trace_id:     Optional[str] = None
 
 
# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "version": app.version}
 
 
@app.get("/observability/status")
async def observability_status():
    """Return status of observability components."""
    try:
        from langfuse import Langfuse
        lf_ok = bool(os.getenv("LANGFUSE_SECRET_KEY"))
    except ImportError:
        lf_ok = False
 
    try:
        import phoenix as px
        phoenix_ok = True
    except ImportError:
        phoenix_ok = False
 
    try:
        import ragas
        ragas_ok = True
    except ImportError:
        ragas_ok = False
 
    return {
        "langfuse":  {"available": lf_ok},
        "phoenix":   {"available": phoenix_ok},
        "ragas":     {"available": ragas_ok},
        "structlog": {"available": True},
    }
 
 
@app.get("/observability/scores")
async def get_rag_scores(token_data=Depends(get_current_user)):
    """Return rolling RAGAS scores from Redis (last 50 evaluations)."""
    from observability.phoenix_eval import get_rolling_scores
    scores = await get_rolling_scores(n=50)
    if not scores:
        return {"scores": [], "avg_context_relevance": None, "avg_faithfulness": None}
 
    avg_cr = sum(s.get("context_relevance", 0) for s in scores) / len(scores)
    avg_f  = sum(s.get("faithfulness",      0) for s in scores) / len(scores)
 
    return {
        "scores":               scores,
        "avg_context_relevance": round(avg_cr, 4),
        "avg_faithfulness":      round(avg_f,  4),
        "window_size":           len(scores),
    }
 
 
@app.post("/query", response_model=QueryResponse)
@limiter.limit("10/minute")
async def query_endpoint(
    request:    Request,
    body:       QueryRequest,
    token_data: Dict[str, Any] = Depends(get_current_user),
):
    """
    Main query endpoint.
    Runs the agent loop and returns answer + observability metadata.
    """
    import bleach
    from agent import run_agent
 
    # ── Input sanitisation ────────────────────────────────────────────────
    clean_question = bleach.clean(body.question, tags=[], strip=True)
    if not clean_question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty after sanitisation")
 
    # ── Get DB + RAG dependencies ─────────────────────────────────────────
    db  = request.app.state.db   # set during startup (not shown — DuckDB or asyncpg)
    rag = request.app.state.rag  # LightRAG instance
    schema   = getattr(request.app.state, "schema_ddl",  "")
    examples = getattr(request.app.state, "few_shot_examples", [])
 
    try:
        result = await run_agent(
            question=clean_question,
            token_data=token_data,
            db=db,
            rag=rag,
            schema=schema,
            examples=examples,
        )
    except Exception as exc:
        log.error("query_endpoint_error", error=str(exc), exc_info=True)
        raise HTTPException(status_code=500, detail="Agent error — check server logs")
 
    ctx = get_trace_context()
    return QueryResponse(
        answer=result["answer"],
        sources=result["sources"],
        tool_trace=result["tool_trace"],
        total_tokens=result["total_tokens"],
        cost_usd=result["cost_usd"],
        trace_id=ctx.trace_id if ctx else None,
    )
 
 
# ---------------------------------------------------------------------------
# Global error handler
# ---------------------------------------------------------------------------
 
@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    log.error("unhandled_exception", error=str(exc), path=request.url.path, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )
 

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: initialise database connection and schema DDL."""
    backend = os.getenv("DB_BACKEND", "duckdb").lower()

    if backend == "postgres":
        from pipeline.db import create_async_pool
        app.state.db_pool = await create_async_pool()
    else:
        # DuckDB — open a persistent connection for query dispatch
        from pipeline.db import get_db_connection
        app.state.db = get_db_connection()

    # Pre-compute schema DDL for DIN-SQL (works for both backends)
    try:
        from pipeline.schema import get_schema_ddl
        app.state.schema_ddl = get_schema_ddl()
        log.info("schema_ddl_loaded", tables=app.state.schema_ddl.count("TABLE "))
    except Exception as exc:
        log.warning("schema_ddl_load_failed", error=str(exc))
        app.state.schema_ddl = ""

    yield

    if hasattr(app.state, "db_pool"):
        await app.state.db_pool.close()
    if hasattr(app.state, "db"):
        app.state.db.close()


app = FastAPI(
    title="Hardened FastAPI Backend",
    description=(
        "Security-hardened backend with JWT, RBAC, rate limiting, PII scrubbing, "
        "input validation, audit logging, data lineage, and RLS."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# ── Rate limiting ──────────────────────────────────────────────────────────────

app.state.limiter = limiter
app.add_middleware(SlowAPIASGIMiddleware)
# RLS middleware must be registered AFTER JWT so request.state.token is populated.
# Middleware execution order in Starlette is last-registered = outermost (runs first),
# so we register RLS first and rate limiter second — meaning rate limiter wraps RLS.
# JWT auth runs inside route handlers via Depends(), so token is set before RLS reads it.
app.add_middleware(RLSContextMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Please slow down."},
    )


# ── RBAC denial audit handler ──────────────────────────────────────────────────

@app.exception_handler(403)
async def forbidden_handler(request: Request, exc):
    """
    Catch-all for 403 responses. Best-effort: extract JWT and write an
    'access_denied' row to audit_log before returning the 403.
    """
    db = getattr(app.state, "db", None)
    if db:
        try:
            credentials = await _bearer(request)
            payload = verify_token(credentials)
            await record(
                db,
                token=payload,
                action="access_denied",
                resource=str(request.url.path),
                ip_address=request.client.host,
                status="denied",
            )
        except Exception:
            pass  # Do not let audit failure mask the real 403

    return JSONResponse(
        status_code=403,
        content={"detail": "Insufficient permissions"},
    )


# ── Chat request model ────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str


# ── Agentic chat endpoint ────────────────────────────────────────────────────

@app.post("/api/chat")
@limiter.limit("10/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    token: dict = Depends(require_scope("query:read")),
):
    try:
        clean_question = validate_query(body.message)
    except ValueError as exc:
        await record(
            app.state.db_pool if hasattr(app.state, "db_pool") else None,
            token=token,
            action="query",
            resource="route:/api/chat",
            raw_query=body.message,
            ip_address=request.client.host,
            status="error",
            detail={"reason": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    schema   = getattr(request.app.state, "schema_ddl",  "")
    examples = getattr(request.app.state, "few_shot_examples", [])
    db       = getattr(app.state, "db_pool", None) or getattr(app.state, "db", None)
    result = await run_agent(
        question=clean_question,
        token_data=token,
        db=db,
        rag=None,   # LightRAG is accessed via get_rag() inside crag.py
        schema=schema,
        examples=examples,
    )

    await record(
        db,
        token=token,
        action="query",
        resource="route:/api/chat",
        raw_query=body.message,
        ip_address=request.client.host,
        status="success",
        detail={"tool_count": len(result["tool_trace"])},
    )

    return result


# ── Routers ────────────────────────────────────────────────────────────────────

app.include_router(auth_router)
app.include_router(query_router)
app.include_router(tools_router)
app.include_router(docs_router)
app.include_router(admin_router)


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}