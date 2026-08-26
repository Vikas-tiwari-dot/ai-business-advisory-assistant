import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.audit import AuditLog
from app.schemas.api_responses import AuditEntry

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=list[AuditEntry])
def list_audit(
    payment_id: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[AuditEntry]:
    query = db.query(AuditLog)

    if payment_id:
        try:
            query = query.filter(AuditLog.payment_id == uuid.UUID(payment_id))
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid payment_id format")

    if stage:
        query = query.filter(AuditLog.stage == stage)
    if from_:
        query = query.filter(AuditLog.timestamp >= from_)
    if to:
        query = query.filter(AuditLog.timestamp <= to)

    rows = query.order_by(AuditLog.timestamp.desc()).limit(limit).all()

    return [
        AuditEntry(
            id=str(r.id),
            event_id=r.event_id,
            stage=r.stage,
            payload=r.payload_json,
            system_version=r.system_version,
            timestamp=r.timestamp,
        )
        for r in rows
    ]
