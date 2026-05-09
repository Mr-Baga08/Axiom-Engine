from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from auth.rbac import require_scope
from observability.audit import record

router = APIRouter(prefix="/admin", tags=["admin"])


@router.delete("/user/{uid}/data")
async def erase_user_data(
    request: Request,
    uid: str,
    token: dict = Depends(require_scope("admin:all")),
):
    """
    GDPR erasure: mark all data_lineage rows for a user as 'erased'.

    Physical deletion of the documents themselves is a separate pipeline step
    (not implemented here — this is the audit trail for compliance).
    """
    db = getattr(request.app.state, "db", None)
    now = datetime.now(tz=timezone.utc)

    if db:
        await db.execute(
            """
            UPDATE data_lineage
            SET erasure_status = 'erased',
                erased_at = $1
            WHERE source_user_uid = $2
              AND erasure_status = 'retained'
            """,
            now,
            uid,
        )

        await record(
            db,
            token=token,
            action="gdpr_erasure",
            resource=f"user:{uid}",
            status="success",
            detail={"erased_uid": uid},
        )

    return {"erased_uid": uid, "erased_at": now.isoformat()}