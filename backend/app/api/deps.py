from fastapi import Depends

from app.core.config import Settings, get_settings
from app.services.ai.registry import get_provider
from app.services.diagnosis.engine import DiagnosisEngine
from app.services.payment_gateway.gateway import PaymentGateway
from app.services.payment_gateway.registry import get_gateway as _get_gateway
from app.services.recovery_agent.agent import RecoveryAgent


def get_diagnosis_engine(settings: Settings = Depends(get_settings)) -> DiagnosisEngine:
    provider = get_provider(settings)
    return DiagnosisEngine(provider=provider, timeout=settings.ai_timeout_seconds)


def get_recovery_agent(settings: Settings = Depends(get_settings)) -> RecoveryAgent:
    provider = get_provider(settings)
    return RecoveryAgent(provider=provider, timeout=settings.ai_timeout_seconds)


def get_gateway(settings: Settings = Depends(get_settings)) -> PaymentGateway:
    # Unseeded (real) randomness for live app traffic -- seeding is only for
    # reproducible batch evaluation runs, not for the running application.
    return _get_gateway(settings)
