"""
PaymentGateway is the single interface anything that actually moves money (or
sends a customer-facing notification) goes through. Two implementations exist:
RecoverySimulator (always available, no credentials needed) and
RazorpayTestModeGateway (spec §12, only active when Razorpay Test Mode keys are
configured). Everything upstream -- policy engine, orchestrator -- calls
`execute()` without caring which one is behind it.

Only ESCALATE_HUMAN, STOP, and IGNORE are non-executing outcomes; every other
resolved action is expected to attempt something and report success/failed.
"""
from abc import ABC, abstractmethod

from app.schemas.diagnosis import DiagnosisResult
from app.schemas.events import NormalizedEvent
from app.schemas.execution import ActionResult
from app.schemas.policy import ResolvedAction

NON_EXECUTING_ACTIONS: set[ResolvedAction] = {"ESCALATE_HUMAN", "STOP", "IGNORE"}


class PaymentGateway(ABC):
    name: str

    @abstractmethod
    def execute(
        self,
        action: ResolvedAction,
        event: NormalizedEvent,
        diagnosis: DiagnosisResult,
        revenue_at_risk: int,
    ) -> ActionResult:
        raise NotImplementedError
