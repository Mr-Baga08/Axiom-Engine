from fastapi import APIRouter, Depends, Request

from auth.rbac import require_scope
from middleware.rate_limiter import limiter

router = APIRouter(prefix="/tools", tags=["tools"])


@router.post("/render_chart")
@limiter.limit("10/minute")
async def render_chart(
    request: Request,
    body: dict,
    token: dict = Depends(require_scope("tools:basic")),
):
    """Render a chart. Requires tools:basic scope (analyst+)."""
    return {"status": "ok", "tool": "render_chart"}


@router.post("/show_reasoning")
@limiter.limit("10/minute")
async def show_reasoning(
    request: Request,
    body: dict,
    token: dict = Depends(require_scope("tools:basic")),
):
    """Show chain-of-thought reasoning. Requires tools:basic scope (analyst+)."""
    return {"status": "ok", "tool": "show_reasoning"}