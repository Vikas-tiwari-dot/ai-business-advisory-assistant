import hashlib
import hmac

from app.services.payment_gateway.webhook_security import verify_signature

SECRET = "test_webhook_secret_123"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def test_valid_signature_is_accepted():
    body = b'{"event": "payment.failed", "payload": {}}'
    signature = _sign(body)
    assert verify_signature(body, signature, SECRET) is True


def test_tampered_body_is_rejected():
    body = b'{"event": "payment.failed", "payload": {}}'
    signature = _sign(body)
    tampered_body = b'{"event": "payment.captured", "payload": {}}'
    assert verify_signature(tampered_body, signature, SECRET) is False


def test_tampered_signature_is_rejected():
    body = b'{"event": "payment.failed", "payload": {}}'
    signature = _sign(body)
    tampered_signature = signature[:-4] + "0000"
    assert verify_signature(body, tampered_signature, SECRET) is False


def test_wrong_secret_is_rejected():
    body = b'{"event": "payment.failed", "payload": {}}'
    signature = _sign(body, secret="a_different_secret")
    assert verify_signature(body, signature, SECRET) is False


def test_missing_signature_is_rejected():
    body = b'{"event": "payment.failed", "payload": {}}'
    assert verify_signature(body, None, SECRET) is False


def test_empty_signature_is_rejected():
    body = b'{"event": "payment.failed", "payload": {}}'
    assert verify_signature(body, "", SECRET) is False


def test_signature_verification_is_deterministic():
    body = b'{"a": 1}'
    signature = _sign(body)
    assert verify_signature(body, signature, SECRET) is True
    assert verify_signature(body, signature, SECRET) is True  # not consumed/stateful


def test_whitespace_change_in_body_breaks_signature():
    """
    This is exactly why verification must happen against raw bytes, not a
    re-serialized version of parsed JSON -- even a single extra space changes
    the HMAC entirely.
    """
    body = b'{"event": "payment.failed"}'
    signature = _sign(body)
    reserialized = b'{"event":  "payment.failed"}'  # two spaces instead of one
    assert verify_signature(reserialized, signature, SECRET) is False
