"""
AuditLogger (spec module 9). The only sanctioned way to write an AuditLog row.

Append-only by construction: this class exposes exactly two write paths --
`log()` (insert one row) and nothing else. There is no `update_entry()`, no
`delete_entry()`, no bulk-mutate helper. tests/unit/test_audit_logger.py
statically checks this file's source for UPDATE/DELETE verbs against
AuditLog so the guarantee can't quietly erode in a later edit, the same
pattern used for the "no LLM in risk_detector" and "no gateway in
recovery_agent" boundaries in earlier phases.
"""
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.audit import AuditLog


class AuditLogger:
    def __init__(self, db: Session, system_version: str | None = None):
        self.db = db
        self.system_version = system_version or get_settings().app_version

    def log(self, *, event_id: str, payment_id: uuid.UUID, stage: str, payload: dict[str, Any]) -> AuditLog:
        entry = AuditLog(
            event_id=event_id,
            payment_id=payment_id,
            stage=stage,
            payload_json=payload,
            system_version=self.system_version,
        )
        self.db.add(entry)
        self.db.flush()
        return entry

    def get_trail(self, payment_id: uuid.UUID) -> list[AuditLog]:
        """Read-only. Returns the full chain for a payment, oldest first."""
        return (
            self.db.query(AuditLog)
            .filter(AuditLog.payment_id == payment_id)
            .order_by(AuditLog.timestamp.asc())
            .all()
        )

    def event_already_seen(self, event_id: str) -> bool:
        """Used for duplicate-event detection upstream (spec §4: duplicate -> ignore)."""
        return self.db.query(AuditLog).filter(AuditLog.event_id == event_id).first() is not None
