from fastapi import APIRouter, Depends, HTTPException, Request, status

from auth.rbac import require_scope
from middleware.rate_limiter import limiter
from observability.audit import record
from security.input_validator import validate_query
from security.pii_scrubber import scrub

router = APIRouter(tags=["query"])


@router.post("/query")
@limiter.limit("10/minute")
async def query(
    request: Request,
    body: dict,
    token: dict = Depends(require_scope("query:read")),
):
    """
    Main query endpoint. Validates input, scrubs PII from context,
    and runs the pipeline.
    """
    # Inject db from app state in real usage: db = request.app.state.db
    db = getattr(request.app.state, "db", None)

    raw = body.get("question", "")

    # ── Input validation ─────────────────────────────────────────────────────
    try:
        clean_question = validate_query(raw)
    except ValueError as exc:
        if db:
            await record(
                db,
                token=token,
                action="query",
                resource="route:/query",
                raw_query=raw,
                ip_address=request.client.host,
                status="error",
                detail={"reason": str(exc)},
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # ── Pipeline (replace with real logic) ───────────────────────────────────
    # assembled_context = await build_context(clean_question)
    # clean_context, _, _ = scrub(assembled_context)   # PII scrub before LLM
    # result = await llm_call(clean_context)
    result = {"answer": "Pipeline placeholder — replace with real logic."}

    # ── Audit success ────────────────────────────────────────────────────────
    if db:
        await record(
            db,
            token=token,
            action="query",
            resource="route:/query",
            raw_query=raw,
            ip_address=request.client.host,
            status="success",
        )

    return result