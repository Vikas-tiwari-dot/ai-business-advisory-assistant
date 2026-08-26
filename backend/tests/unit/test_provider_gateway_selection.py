from app.core.config import get_settings
from app.services.ai.gemini_provider import GeminiProvider
from app.services.ai.openai_provider import OpenAIProvider
from app.services.ai.registry import get_provider
from app.services.payment_gateway.razorpay_gateway import RazorpayTestModeGateway
from app.services.payment_gateway.registry import get_gateway
from app.services.payment_gateway.simulator import RecoverySimulator


# --- AI provider selection: the branches not exercised by AI_PROVIDER=none --


def test_get_provider_returns_gemini_when_configured():
    settings = get_settings()
    settings.ai_provider = "gemini"
    settings.gemini_api_key = "fake_key_for_selection_test_only"
    provider = get_provider(settings)
    assert isinstance(provider, GeminiProvider)
    settings.ai_provider = "none"
    settings.gemini_api_key = None


def test_get_provider_returns_openai_when_configured():
    settings = get_settings()
    settings.ai_provider = "openai"
    settings.openai_api_key = "fake_key_for_selection_test_only"
    provider = get_provider(settings)
    assert isinstance(provider, OpenAIProvider)
    settings.ai_provider = "none"
    settings.openai_api_key = None


def test_get_provider_returns_none_for_gemini_without_a_key():
    settings = get_settings()
    settings.ai_provider = "gemini"
    settings.gemini_api_key = None
    assert get_provider(settings) is None
    settings.ai_provider = "none"


# --- Gateway selection: the Razorpay branch --------------------------------


def test_get_gateway_returns_razorpay_when_fully_configured():
    settings = get_settings()
    settings.use_razorpay_test_mode = True
    settings.razorpay_key_id = "rzp_test_fake"
    settings.razorpay_key_secret = "fake_secret"
    gateway = get_gateway(settings)
    assert isinstance(gateway, RazorpayTestModeGateway)
    settings.use_razorpay_test_mode = False
    settings.razorpay_key_id = None
    settings.razorpay_key_secret = None


def test_get_gateway_falls_back_to_simulator_without_full_config():
    settings = get_settings()
    settings.use_razorpay_test_mode = False
    gateway = get_gateway(settings)
    assert isinstance(gateway, RecoverySimulator)


# --- Razorpay gateway: the success path and the defensive fallback ---------


class FakeResponse:
    def __init__(self, json_body):
        self._json = json_body

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def test_razorpay_gateway_success_path_creates_payment_link(monkeypatch):
    import httpx

    from app.schemas.diagnosis import DiagnosisResult
    from app.schemas.events import CustomerHistory, NormalizedEvent

    def _fake_post(*args, **kwargs):
        return FakeResponse({"short_url": "https://rzp.io/l/fake123"})

    monkeypatch.setattr(httpx, "post", _fake_post)

    gateway = RazorpayTestModeGateway(key_id="rzp_test_x", key_secret="secret_x")
    event = NormalizedEvent(
        event_id="evt_1", customer_id="cust_1", payment_id="pay_1", amount=100000,
        currency="INR", status="failed", failure_code="NETWORK_ERROR", failure_reason="x",
        timestamp="2026-08-20T10:00:00Z", payment_method="card", attempt_number=1,
        customer_history=CustomerHistory(),
    )
    diagnosis = DiagnosisResult(
        diagnosis="temporary_failure", confidence=0.8, reasoning_summary="x",
        recommended_strategy="retry_later", model_provider="fallback_rules",
        schema_valid=True, fallback_used=True,
    )

    result = gateway.execute("RETRY_PAYMENT", event, diagnosis, revenue_at_risk=100000)

    assert result.executed is True
    assert result.result == "skipped"  # link created, but not a confirmed payment -- see module docstring
    assert "rzp.io/l/fake123" in result.message


def test_razorpay_gateway_defensive_fallback_for_unsupported_action():
    """
    Defensive branch: should be unreachable given the closed ResolvedAction
    set upstream, but the adapter must still fail cleanly rather than crash
    if it's ever called with something outside its known action set.
    """
    from app.schemas.diagnosis import DiagnosisResult
    from app.schemas.events import CustomerHistory, NormalizedEvent

    gateway = RazorpayTestModeGateway(key_id="rzp_test_x", key_secret="secret_x")
    event = NormalizedEvent(
        event_id="evt_1", customer_id="cust_1", payment_id="pay_1", amount=100000,
        currency="INR", status="failed", failure_code="NETWORK_ERROR", failure_reason="x",
        timestamp="2026-08-20T10:00:00Z", payment_method="card", attempt_number=1,
        customer_history=CustomerHistory(),
    )
    diagnosis = DiagnosisResult(
        diagnosis="temporary_failure", confidence=0.8, reasoning_summary="x",
        recommended_strategy="retry_later", model_provider="fallback_rules",
        schema_valid=True, fallback_used=True,
    )
    result = gateway.execute("SOME_UNEXPECTED_ACTION", event, diagnosis, revenue_at_risk=100000)
    assert result.executed is False
    assert result.result == "skipped"
