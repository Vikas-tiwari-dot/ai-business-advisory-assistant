import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import DatasetSplit, EventSource, PaymentMethod, PaymentStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (Index("ix_payments_customer_status", "customer_id", "status"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)

    amount: Mapped[int] = mapped_column(Integer)  # minor units (paise)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    payment_method: Mapped[PaymentMethod] = mapped_column(default=PaymentMethod.CARD)
    status: Mapped[PaymentStatus] = mapped_column(default=PaymentStatus.CREATED, index=True)
    source: Mapped[EventSource] = mapped_column(default=EventSource.SIMULATOR)

    # Immutable once set at generation time -- evaluation refuses to score anything
    # tagged "train". Real (non-synthetic) events default to holdout so nothing
    # from production traffic can accidentally count as training data either.
    dataset_split: Mapped[DatasetSplit] = mapped_column(default=DatasetSplit.HOLDOUT)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    customer: Mapped["Customer"] = relationship(back_populates="payments")  # noqa: F821
    attempts: Mapped[list["PaymentAttempt"]] = relationship(back_populates="payment", order_by="PaymentAttempt.attempt_number")
    recovery_cases: Mapped[list["RecoveryCase"]] = relationship(back_populates="payment")  # noqa: F821


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (Index("ix_attempts_payment_number", "payment_id", "attempt_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # AttemptStatus value
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    raw_event_json: Mapped[dict] = mapped_column(JSON, default=dict)

    payment: Mapped["Payment"] = relationship(back_populates="attempts")
