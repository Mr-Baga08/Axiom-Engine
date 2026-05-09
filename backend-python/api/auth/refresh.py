from fastapi import APIRouter, Body, HTTPException, status
from .jwt_handler import (
    create_access_token,
    create_refresh_token,
    verify_token,          # we'll reuse token verification but add a type check
)
from .rbac import require_scope  # not needed for refresh, but import if you keep it

router = APIRouter(prefix="/auth", tags=["auth"])

def verify_refresh_token(token: str) -> dict:
    """Validate the refresh token and ensure it's a refresh token."""
    payload = verify_token(token)  # reuses the same signature/expiry check
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not a refresh token"
        )
    return payload

@router.post("/refresh")
async def refresh(refresh_token: str = Body(..., embed=True)):
    try:
        payload = verify_refresh_token(refresh_token)
    except HTTPException:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token"
        )

    # Create tokens using the correct dict signature
    token_data = {"sub": payload["sub"], "role": payload["role"]}
    new_access = create_access_token(token_data)
    new_refresh = create_refresh_token(token_data)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer"
    }