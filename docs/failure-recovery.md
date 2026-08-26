# Failure Recovery

RazorRecover AI is required to demonstrate graceful degradation under real
failure conditions (spec §10), not just handle the happy path. This document
lists every scenario, exactly where it's implemented, and exactly which test
reproduces it — so each one is independently demoable, not just described.

## 1. Razorpay API timeout

**Where handled:** `app/services/payment_gateway/razorpay_gateway.py` wraps
the outbound `httpx.post` call in a broad `try/except`. Any transport failure
(timeout, connection refused, DNS failure, non-2xx response) is caught and
converted into `ActionResult(executed=False, result="failed")` — never a
raised exception, never a false "success."

**Reproduced by:**
`backend/tests/unit/test_failure_injection.py::test_razorpay_gateway_timeout_returns_failed_result_not_a_crash`
and `test_razorpay_gateway_connection_error_also_handled_gracefully` — both
monkeypatch `httpx.post` to raise (`httpx.TimeoutException`,
`httpx.ConnectError`) without touching the network, and assert the gateway
degrades to a clean failed result.

## 2. Malformed webhook

**Where handled:** `app/api/events.py::receive_webhook` — a body that isn't
valid JSON returns `422`; valid JSON that doesn't have the shape a Razorpay
payment webhook should have (missing `payload.payment.entity`, missing
required fields like `amount`) also returns `422` via
`MalformedWebhookError` from `razorpay_normalizer.py`.

**Reproduced by:**
`backend/tests/integration/test_webhook_api.py::test_malformed_json_body_returns_422`
and `test_valid_json_but_wrong_shape_returns_422`.

## 3. Duplicate webhook

**Where handled:** every event carries an `event_id`. `AuditLogger.event_already_seen()`
checks whether that `event_id` has already produced an audit entry; if so,
`run_pipeline_for_event` logs a single `DUPLICATE_IGNORED` entry and returns
immediately — no second `Payment`, `RecoveryCase`, `PaymentAttempt`, or
`RecoveryAction` row, no re-execution against the gateway.

**Reproduced by:**
`backend/tests/integration/test_webhook_api.py::test_duplicate_webhook_delivery_is_ignored_on_second_call`
(same signed payload delivered twice) and
`backend/tests/unit/test_orchestrator.py::test_duplicate_event_only_logs_once_and_creates_no_second_case`.

## 4. LLM timeout

**Where handled:** `app/services/ai/structured.py::call_structured` catches
*any* exception a provider's `.complete()` raises (a timeout is just one
flavor), retries once, and returns `(None, metadata)` on repeated failure.
`DiagnosisEngine`/`RecoveryAgent` treat that as license to fall back to
deterministic rules, marked with `AI unavailable — deterministic fallback
used.` and logged as `AI_FALLBACK_USED` in the audit trail — matching the
spec's documented recovery path exactly:

```
LLM unavailable
    ↓
Fallback deterministic rules
    ↓
Continue safe workflow
```

**Reproduced by:**
`backend/tests/unit/test_failure_injection.py::test_llm_timeout_falls_back_and_pipeline_completes_successfully`
uses a `TimeoutProvider` whose `.complete()` always raises `TimeoutError`, run
through the *real* orchestrator (not just the isolated engine), and confirms
both the audit trail and a stored `AIAnalysis` row show the fallback was used.
`test_llm_timeout_does_not_prevent_a_successful_recovery` confirms the
pipeline still reaches a successful outcome despite the LLM being completely
unavailable for the entire run.

## 5. Invalid LLM JSON

**Where handled:** same `call_structured` choke point as #4 — malformed JSON
and schema-violating JSON are both caught, retried once, then trigger the
same deterministic fallback path.

**Reproduced by:** `backend/tests/unit/test_ai_structured.py` (the
`call_structured` layer directly, including the literal "intentionally
malformed LLM output, twice" test) and
`backend/tests/unit/test_diagnosis_engine.py` /
`backend/tests/unit/test_recovery_agent.py` (end-to-end through each engine,
including a scripted "AI" that tries to propose an action outside the six
allowed actions).

## 6. Database failure simulation

**Where handled:** `app/services/pipeline/orchestrator.py::_commit_with_retry`
wraps every `db.commit()` call. On a `SQLAlchemyError`, it rolls back and
retries once; if the second attempt also fails, it raises
`DatabaseWriteError` (`app/core/exceptions.py`) — a typed, catchable error —
rather than letting a raw SQLAlchemy exception escape or leaving a
half-written transaction. `app/main.py` registers a handler for
`RazorRecoverError` (the base class `DatabaseWriteError` extends) that turns
this into a clean `503` with an `error.code = "DATABASE_WRITE_ERROR"` envelope.

**Reproduced by:**
`backend/tests/unit/test_failure_injection.py::test_commit_retry_succeeds_after_one_transient_failure`
(transient failure that clears on retry — succeeds),
`test_commit_retry_raises_typed_error_after_exhausting_attempts` (persistent
failure — typed error, not a crash), and
`test_database_failure_surfaces_as_503_through_the_live_api`, which exercises
this through the *real* FastAPI app + TestClient (not just the internal
service layer) and asserts the exact `503` + error envelope a client would see.

## 7. Payment retry failure

**Where handled:** `run_pipeline_for_event` increments
`RecoveryCase.recovery_attempts` on every failed execution. Once that count
reaches `settings.max_recovery_attempts` (default 3), the payment is marked
`unrecoverable` and the case `closed_unrecovered` — it does not retry
forever. Independently, the policy engine's own max-attempts rule (Phase 7)
blocks a *new* retry attempt from even being proposed once the threshold is
hit, redirecting to `ESCALATE_HUMAN` instead — two separate layers arriving
at the same "stop trying automatically" outcome.

**Reproduced by:**
`backend/tests/unit/test_failure_injection.py::test_repeated_retry_failures_eventually_mark_payment_unrecoverable`
runs three separate failed-attempt events against the same payment (as three
webhook deliveries would) and confirms the payment lands on `unrecoverable`/
`closed_unrecovered` exactly at the configured limit, not before and not
after. `test_payment_stays_open_before_exhausting_max_attempts` confirms a
single failure alone does *not* prematurely close the case.

---

## What this deliberately does not cover

This is a buildathon-scoped failure injection suite, not an exhaustive chaos-
engineering harness. Left out on purpose:

- **Partial-write recovery mid-transaction** (e.g. the audit log commits but
  the recovery case update doesn't) — `_commit_with_retry` treats a single
  `db.commit()` as atomic, which SQLite/Postgres transactions genuinely are,
  so this class of failure isn't reachable the way it would be across
  multiple independent commits.
- **Network partition between the API and a real Postgres instance** — only
  tested against SQLite in this environment (see the Phase 2 write-up); the
  retry/typed-error mechanism is dialect-agnostic, but a real network
  partition against Postgres specifically hasn't been exercised live.
- **Concurrent double-submission race conditions** (two requests for the same
  new `event_id` arriving at the same instant) — the dedup check is
  read-then-decide, not atomic under concurrent load; a production system
  would want a unique DB constraint on `AuditLog.event_id` as a backstop.
