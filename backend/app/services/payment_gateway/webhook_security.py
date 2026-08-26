"""
Razorpay webhook signature verification (spec §17: "webhook signature
verification where applicable").

Razorpay signs webhook payloads with HMAC-SHA256 over the raw request body,
using the webhook secret configured in the merchant dashboard, and sends the
result in the X-Razorpay-Signature header. This must be verified against the
*raw bytes* of the body, not a re-serialized version of the parsed JSON --
re-serializing can change whitespace/key order and silently break
verification, so callers must pass the exact bytes received.
"""
import hashlib
import hmac


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    # constant-time comparison -- avoids leaking timing information about how
    # much of the signature matched, which is the whole point of HMAC verification.
    return hmac.compare_digest(expected, signature)
