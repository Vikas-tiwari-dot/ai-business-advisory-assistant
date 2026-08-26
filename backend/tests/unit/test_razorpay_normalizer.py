import pytest

from app.services.payment_gateway.razorpay_normalizer import (
    MalformedWebhookError,
    build_partial_event_from_webhook,
    extract_payment_entity,
)


def _webhook_payload(**entity_overrides) -> dict:
    entity = {
        "id": "pay_ABC123",
        "amount": 499900,
        "currency": "INR",
        "status": "failed",
        "method": "card",
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Payment failed due to insufficient funds.",
        "created_at": 1735689600,  # fixed epoch for determinism
        "email": "cust_a@example.com",
    }
    entity.update(entity_overrides)
    return {
        "entity": "event",
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {"payment": {"entity": entity}},
        "created_at": 1735689600,
    }


# --- Malformed payload handling ---------------------------------------------


def test_missing_payload_key_raises_malformed_error():
    with pytest.raises(MalformedWebhookError):
        extract_payment_entity({"event": "payment.failed"})


def test_missing_payment_key_raises_malformed_error():
    with pytest.raises(MalformedWebhookError):
        extract_payment_entity({"payload": {}})


def test_completely_wrong_shape_raises_malformed_error():
    with pytest.raises(MalformedWebhookError):
        extract_payment_entity({"totally": "unrelated", "shape": 123})


def test_missing_required_entity_fields_raises_malformed_error():
    payload = _webhook_payload()
    del payload["payload"]["payment"]["entity"]["amount"]
    with pytest.raises(MalformedWebhookError, match="amount"):
        build_partial_event_from_webhook(payload)


# --- Correct field mapping ----------------------------------------------------


def test_basic_field_mapping():
    payload = _webhook_payload()
    result = build_partial_event_from_webhook(payload)
    assert result["payment_id"] == "pay_ABC123"
    assert result["amount"] == 499900
    assert result["currency"] == "INR"
    assert result["failure_code"] == "BAD_REQUEST_ERROR"
    assert result["failure_reason"] == "Payment failed due to insufficient funds."
    assert result["payment_method"] == "card"


def test_status_mapping_failed():
    payload = _webhook_payload(status="failed")
    result = build_partial_event_from_webhook(payload)
    assert result["status"] == "failed"


def test_status_mapping_captured_to_recovered():
    payload = _webhook_payload(status="captured", error_code=None, error_description=None)
    result = build_partial_event_from_webhook(payload)
    assert result["status"] == "recovered"


def test_status_mapping_authorized_to_pending():
    payload = _webhook_payload(status="authorized", error_code=None, error_description=None)
    result = build_partial_event_from_webhook(payload)
    assert result["status"] == "pending"


def test_status_mapping_unknown_status_defaults_to_pending():
    payload = _webhook_payload(status="some_new_razorpay_status", error_code=None, error_description=None)
    result = build_partial_event_from_webhook(payload)
    assert result["status"] == "pending"


def test_timestamp_derived_from_created_at_epoch():
    payload = _webhook_payload(created_at=1735689600)  # 2025-01-01T00:00:00Z
    result = build_partial_event_from_webhook(payload)
    assert result["timestamp"].startswith("2025-01-01")


def test_missing_created_at_falls_back_to_now_without_crashing():
    payload = _webhook_payload()
    del payload["payload"]["payment"]["entity"]["created_at"]
    result = build_partial_event_from_webhook(payload)
    assert result["timestamp"]  # just needs to not crash and produce something


# --- Method mapping ------------------------------------------------------------


@pytest.mark.parametrize("method", ["card", "upi", "netbanking", "wallet", "emi"])
def test_known_methods_map_directly(method):
    payload = _webhook_payload(method=method)
    result = build_partial_event_from_webhook(payload)
    assert result["payment_method"] == method
    assert result["unmapped_method"] is False


def test_unrecognized_method_falls_back_without_crashing():
    payload = _webhook_payload(method="paylater")  # Razorpay supports this; we don't model it separately
    result = build_partial_event_from_webhook(payload)
    assert result["payment_method"] == "card"  # documented fallback
    assert result["unmapped_method"] is True  # but the fallback is flagged, not silent


def test_missing_method_uses_fallback_without_flagging_as_unmapped():
    payload = _webhook_payload()
    del payload["payload"]["payment"]["entity"]["method"]
    result = build_partial_event_from_webhook(payload)
    assert result["payment_method"] == "card"
    assert result["unmapped_method"] is False  # absent is different from present-but-unrecognized


# --- Customer ID fallback chain -------------------------------------------------


def test_customer_id_falls_back_to_email_when_customer_id_absent():
    payload = _webhook_payload(email="someone@example.com")
    result = build_partial_event_from_webhook(payload)
    assert result["customer_id"] == "someone@example.com"


def test_customer_id_prefers_explicit_customer_id_field():
    payload = _webhook_payload()
    payload["payload"]["payment"]["entity"]["customer_id"] = "cust_explicit_123"
    result = build_partial_event_from_webhook(payload)
    assert result["customer_id"] == "cust_explicit_123"


def test_customer_id_falls_back_to_unknown_marker_when_nothing_available():
    payload = _webhook_payload()
    del payload["payload"]["payment"]["entity"]["email"]
    result = build_partial_event_from_webhook(payload)
    assert result["customer_id"].startswith("unknown_")


# --- Event ID determinism (needed for dedup) ------------------------------------


def test_event_id_is_deterministic_for_identical_payload():
    payload = _webhook_payload()
    result_a = build_partial_event_from_webhook(payload)
    result_b = build_partial_event_from_webhook(payload)
    assert result_a["event_id"] == result_b["event_id"]


def test_event_id_differs_for_different_payment_ids():
    result_a = build_partial_event_from_webhook(_webhook_payload(id="pay_AAA"))
    result_b = build_partial_event_from_webhook(_webhook_payload(id="pay_BBB"))
    assert result_a["event_id"] != result_b["event_id"]
