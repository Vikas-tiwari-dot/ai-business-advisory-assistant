"""
Import every model here so Base.metadata is fully populated for Alembic
autogenerate and for `Base.metadata.create_all()` in tests/dev.
"""
from app.models.audit import AuditLog, EvaluationResult  # noqa: F401
from app.models.customer import Customer  # noqa: F401
from app.models.payment import Payment, PaymentAttempt  # noqa: F401
from app.models.recovery import AIAnalysis, RecoveryAction, RecoveryCase  # noqa: F401

__all__ = [
    "Customer",
    "Payment",
    "PaymentAttempt",
    "RecoveryCase",
    "AIAnalysis",
    "RecoveryAction",
    "AuditLog",
    "EvaluationResult",
]
