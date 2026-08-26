import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.session import Base, get_db
from app.main import app

WEBHOOK_SECRET = "test_webhook_secret_xyz"


def _sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _webhook_body(**entity_overrides) -> bytes:
    entity = {
        "id": "pay_WEBHOOK1",
        "amount": 250000,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "error_code": "GATEWAY_ERROR",
        "error_description": "Gateway timed out.",
        "created_at": 1735689600,
        "email": "webhook_customer@example.com",
    }
    entity.update(entity_overrides)
    payload = {
        "entity": "event",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {"payment": {"entity": entity}},
        "created_at": 1735689600,
    }
    return json.dumps(payload).encode("utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()

    db_path = tmp_path / "test_webhook.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def client_no_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("RAZORPAY_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()

    db_path = tmp_path / "test_webhook_no_secret.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_settings.cache_clear()


# --- Signature verification at the HTTP layer -------------------------------


def test_valid_signature_is_accepted_and_processed(client):
    body = _webhook_body()
    resp = client.post(
        "/api/events/webhook",
        content=body,
        headers={"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    body_json = resp.json()
    assert body_json["received"] is True
    assert "case_status" in body_json or body_json.get("ignored")


def test_tampered_payload_is_rejected_with_401(client):
    body = _webhook_body()
    signature = _sign(body)
    tampered_body = _webhook_body(amount=999999999)  # different payload, same (now stale) signature
    resp = client.post(
        "/api/events/webhook",
        content=tampered_body,
        headers={"X-Razorpay-Signature": signature, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_missing_signature_header_is_rejected(client):
    body = _webhook_body()
    resp = client.post("/api/events/webhook", content=body, headers={"Content-Type": "application/json"})
    assert resp.status_code == 401


def test_wrong_secret_signature_is_rejected(client):
    body = _webhook_body()
    wrong_signature = _sign(body, secret="not_the_real_secret")
    resp = client.post(
        "/api/events/webhook",
        content=body,
        headers={"X-Razorpay-Signature": wrong_signature, "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_no_secret_configured_returns_503(client_no_secret):
    body = _webhook_body()
    resp = client_no_secret.post(
        "/api/events/webhook",
        content=body,
        headers={"X-Razorpay-Signature": "irrelevant", "Content-Type": "application/json"},
    )
    assert resp.status_code == 503


# --- Malformed payload handling ----------------------------------------------


def test_malformed_json_body_returns_422(client):
    body = b"this is not json {{{"
    resp = client.post(
        "/api/events/webhook",
        content=body,
        headers={"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_valid_json_but_wrong_shape_returns_422(client):
    body = json.dumps({"totally": "unrelated", "shape": True}).encode("utf-8")
    resp = client.post(
        "/api/events/webhook",
        content=body,
        headers={"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 422


# --- Duplicate webhook delivery ------------------------------------------------


def test_duplicate_webhook_delivery_is_ignored_on_second_call(client):
    body = _webhook_body()
    headers = {"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"}

    resp1 = client.post("/api/events/webhook", content=body, headers=headers)
    assert resp1.status_code == 200
    assert resp1.json().get("ignored") is not True  # first delivery is processed normally

    resp2 = client.post("/api/events/webhook", content=body, headers=headers)
    assert resp2.status_code == 200
    assert resp2.json().get("ignored") is True  # exact redelivery is a duplicate -> ignored


# --- Attempt-number enrichment from DB history --------------------------------


def test_second_distinct_webhook_for_same_payment_increments_attempt_number(client):
    first_body = _webhook_body(created_at=1735689600)
    resp1 = client.post(
        "/api/events/webhook",
        content=first_body,
        headers={"X-Razorpay-Signature": _sign(first_body), "Content-Type": "application/json"},
    )
    assert resp1.status_code == 200

    # A different event_id (different created_at) for the SAME payment_id --
    # simulates Razorpay redelivering a later webhook for another attempt.
    second_body = _webhook_body(created_at=1735689700)
    resp2 = client.post(
        "/api/events/webhook",
        content=second_body,
        headers={"X-Razorpay-Signature": _sign(second_body), "Content-Type": "application/json"},
    )
    assert resp2.status_code == 200

    payment_id = resp1.json()["payment_id"]
    detail = client.get(f"/api/payments/{payment_id}").json()
    attempt_numbers = [a["attempt_number"] for a in detail["attempts"]]
    assert attempt_numbers == sorted(attempt_numbers)
    assert len(attempt_numbers) == 2
    assert attempt_numbers[1] > attempt_numbers[0]


# --- Direct event ingestion endpoint -------------------------------------------


def test_direct_payment_event_endpoint_accepts_normalized_event(client):
    event = {
        "event_id": "evt_direct_1",
        "customer_id": "cust_direct_1",
        "payment_id": "pay_direct_1",
        "amount": 100000,
        "currency": "INR",
        "status": "failed",
        "failure_code": "NETWORK_ERROR",
        "failure_reason": "Temporary issue",
        "timestamp": "2026-08-20T10:00:00Z",
        "payment_method": "card",
        "attempt_number": 1,
        "customer_history": {
            "previous_successful_payments": 0,
            "previous_failed_payments": 0,
            "lifetime_value": 0,
            "opted_out": False,
        },
    }
    resp = client.post("/api/events/payment", json=event)
    assert resp.status_code == 200
    assert resp.json()["received"] is True


def test_webhook_payload_that_fails_normalized_event_validation_returns_422(client):
    """
    Exercises the second validation guard in receive_webhook: the normalizer
    itself is lenient (e.g. doesn't check amount > 0), but NormalizedEvent's
    own Pydantic validation is stricter -- this must surface as a clean 422,
    not a 500.
    """
    body = _webhook_body(amount=0)  # passes the normalizer, fails NormalizedEvent's Field(gt=0)
    resp = client.post(
        "/api/events/webhook",
        content=body,
        headers={"X-Razorpay-Signature": _sign(body), "Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_direct_payment_event_endpoint_rejects_invalid_shape(client):
    resp = client.post("/api/events/payment", json={"not": "a valid event"})
    assert resp.status_code == 422
