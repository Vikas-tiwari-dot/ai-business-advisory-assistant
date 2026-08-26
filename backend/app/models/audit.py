import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AuditLog(Base):
    """
    Append-only. The service layer (app/services/audit) never issues UPDATE or
    DELETE against this table -- only INSERT. That guarantee is enforced at the
    repository layer, not just by convention, and is covered by a dedicated test.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_payment_timestamp", "payment_id", "timestamp"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str] = mapped_column(String(64), index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), index=True)
    stage: Mapped[str] = mapped_column(String(32))  # AuditStage value
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)
    system_version: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    dataset_split: Mapped[str] = mapped_column(String(16), default="holdout")
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
