from fastapi import APIRouter, Depends, Request

from auth.rbac import require_scope
from middleware.rate_limiter import limiter

router = APIRouter(prefix="/docs", tags=["docs"])


@router.get("/executive/{path:path}")
@limiter.limit("10/minute")
async def executive_docs(
    request: Request,
    path: str,
    token: dict = Depends(require_scope("docs:executive")),
):
    """Access executive documents. Requires docs:executive scope (executive+)."""
    return {"status": "ok", "path": path}