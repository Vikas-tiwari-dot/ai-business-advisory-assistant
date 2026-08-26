"""
Single choke point for "which gateway is active." Mirrors app.services.ai.registry's
pattern: one function, one decision, so nothing else in the app has to branch
on config directly.
"""
import random

from app.core.config import Settings
from app.services.payment_gateway.gateway import PaymentGateway
from app.services.payment_gateway.razorpay_gateway import RazorpayTestModeGateway
from app.services.payment_gateway.simulator import RecoverySimulator


def get_gateway(settings: Settings, *, seed: int | None = None) -> PaymentGateway:
    if settings.use_razorpay_test_mode and settings.razorpay_key_id and settings.razorpay_key_secret:
        return RazorpayTestModeGateway(settings.razorpay_key_id, settings.razorpay_key_secret)
    rng = random.Random(seed) if seed is not None else random.Random()
    return RecoverySimulator(rng=rng)
