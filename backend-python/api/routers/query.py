from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from auth.rbac import require_scope
from middleware.rate_limiter import limiter
from observability.audit import record
from security.input_validator import validate_query

router = APIRouter(tags=["query"])


class QueryBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=2048)


@router.post("/query")
@limiter.limit("10/minute")
async def query(
    request: Request,
    body: QueryBody,
    token: dict = Depends(require_scope("query:read")),
):
    """REST query endpoint — same agent pipeline as /api/chat."""
    from agent import run_agent

    db = getattr(request.app.state, "db_pool", None) or getattr(request.app.state, "db", None)

    try:
        clean_question = validate_query(body.question)
    except ValueError as exc:
        if db:
            await record(db, token=token, action="query", resource="route:/query",
                         raw_query=body.question, ip_address=request.client.host,
                         status="error", detail={"reason": str(exc)})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    schema   = getattr(request.app.state, "schema_ddl", "")
    examples = getattr(request.app.state, "few_shot_examples", [])

    try:
        result = await run_agent(
            question=clean_question,
            token_data=token,
            db=db,
            schema=schema,
            examples=examples,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Agent error — check server logs")

    if db:
        await record(db, token=token, action="query", resource="route:/query",
                     raw_query=body.question, ip_address=request.client.host,
                     status="success", detail={"tool_count": len(result.get("tool_trace", []))})

    return result
