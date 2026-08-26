import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import CustomerSegment


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    external_customer_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Segment is *derived* by CustomerContextEngine on read; this column caches the
    # last computed value for fast list/filter queries on the dashboard.
    segment: Mapped[CustomerSegment] = mapped_column(default=CustomerSegment.NEW)
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    payments: Mapped[list["Payment"]] = relationship(back_populates="customer")
