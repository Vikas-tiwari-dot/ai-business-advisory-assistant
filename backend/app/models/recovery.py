import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import AIStage, RecoveryActionType, RecoveryCaseStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecoveryCase(Base):
    __tablename__ = "recovery_cases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), index=True)
    status: Mapped[RecoveryCaseStatus] = mapped_column(default=RecoveryCaseStatus.OPEN, index=True)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    revenue_at_risk: Mapped[int] = mapped_column(Integer, default=0)  # minor units
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    payment: Mapped["Payment"] = relationship(back_populates="recovery_cases")  # noqa: F821
    analyses: Mapped[list["AIAnalysis"]] = relationship(back_populates="recovery_case")
    actions: Mapped[list["RecoveryAction"]] = relationship(back_populates="recovery_case")


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id", ondelete="RESTRICT"), index=True)
    stage: Mapped[AIStage] = mapped_column()
    model_provider: Mapped[str] = mapped_column(String(32))  # "gemini" | "openai" | "fallback_rules"
    raw_output_json: Mapped[dict] = mapped_column(JSON, default=dict)
    diagnosis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Business-facing explanation only -- never the model's raw chain-of-thought.
    reasoning_summary: Mapped[str] = mapped_column(String(512), default="")
    schema_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    recovery_case: Mapped["RecoveryCase"] = relationship(back_populates="analyses")


class RecoveryAction(Base):
    __tablename__ = "recovery_actions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recovery_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recovery_cases.id", ondelete="RESTRICT"), index=True)
    proposed_action: Mapped[RecoveryActionType] = mapped_column()
    policy_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_reason: Mapped[str] = mapped_column(String(256), default="")
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    execution_result: Mapped[str | None] = mapped_column(String(16), nullable=True)  # ExecutionResult value
    revenue_recovered: Mapped[int] = mapped_column(Integer, default=0)  # minor units
    human_override: Mapped[str | None] = mapped_column(String(16), nullable=True)  # HumanOverride value
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    recovery_case: Mapped["RecoveryCase"] = relationship(back_populates="actions")
