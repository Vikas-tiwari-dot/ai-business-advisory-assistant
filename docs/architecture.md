# RazorRecover AI — Phase 1: Architecture & Design

Razorpay AI Buildathon 2026 · Track: AI Revenue Recovery

This document is the design contract for the whole build. Every later phase implements
against this file without re-litigating it. Nothing here talks to real money or real
customer data — Razorpay Test Mode and a local synthetic simulator are the only data
sources.

---

## 1. System Architecture

### 1.1 Guiding principle

> AI decides. Policy controls. System executes. Audit records. Metrics prove.

The LLM is a **classifier + recommender**, never an actuator. Every action it proposes
passes through a deterministic policy engine before anything executes. This separation
is the core "AI judgment" story of the demo, so it is enforced in code, not just in docs:
the `RecoveryAgent` service has no import path to the payment gateway or database writes —
it can only return a `ProposedAction` object.

### 1.2 High-level flow

```
[Event Source]                      [Ingestion]
 Webhook / CSV / Simulator  ───────▶  EventNormalizer
                                            │
                                            ▼
                                    RiskDetector (deterministic)
                                            │  risk_score, revenue_at_risk
                                            ▼
                              ┌── CustomerContextEngine (deterministic)
                              │        LTV, failure_rate, segment
                              ▼
                        DiagnosisEngine (LLM, structured JSON out)
                              │  diagnosis, confidence
                              ▼
                        RecoveryAgent (LLM, structured JSON out)
                              │  proposed action + reason
                              ▼
                        PolicyEngine (deterministic, pure functions)
                              │  allowed / blocked (+reason)
                              ▼
                    ┌─────────┴─────────┐
                    ▼                   ▼
            ActionExecutor        HumanEscalation
         (RecoverySimulator /       (Recovery Queue,
          Razorpay Test Mode)        approve/reject/stop)
                    │                   │
                    └─────────┬─────────┘
                               ▼
                          AuditLogger (append-only)
                               ▼
                     EvaluationEngine (batch metrics)
                               ▼
                          Dashboard (React)
```

### 1.3 Component map (backend services)

| Service | Type | I/O contract | Failure mode handling |
|---|---|---|---|
| `EventNormalizer` | deterministic | raw event → `NormalizedEvent` | malformed → reject w/ 422, log |
| `RiskDetector` | deterministic rules | `NormalizedEvent` → `RiskAssessment` | none needed (pure math) |
| `CustomerContextEngine` | deterministic SQL aggregation | `customer_id` → `CustomerContext` | missing history → defaults to `new_customer` segment |
| `DiagnosisEngine` | LLM + Pydantic schema | context → `Diagnosis` | invalid JSON → 1 retry → deterministic rule-based fallback, flagged `ai_unavailable=true` |
| `RecoveryAgent` | LLM + Pydantic schema | context + diagnosis → `ProposedAction` | same fallback pattern as above |
| `PolicyEngine` | deterministic rules | `ProposedAction` + case history → `PolicyDecision` | pure function, no external calls, cannot fail except on programmer error |
| `ActionExecutor` | simulator / Razorpay Test Mode | approved action → `ActionResult` | gateway timeout → retry once w/ backoff → mark `FAILED_TRANSIENT`, no double-charge |
| `AuditLogger` | deterministic | any state transition → `AuditLog` row | DB write failure → buffered retry queue, never silently dropped |
| `EvaluationEngine` | deterministic | batch of `RecoveryCase` → `EvaluationResult` | requires held-out split; refuses to run on training data (checked via dataset tag) |

### 1.4 LLM provider abstraction

```
backend/app/services/ai/
  provider.py         # LLMProvider interface: .complete(prompt, schema) -> dict
  gemini_provider.py   # implements via GEMINI_API_KEY
  openai_provider.py   # implements via OPENAI_API_KEY
  fallback_provider.py # rule-based deterministic stand-in, always available
```

Selection is via `AI_PROVIDER=gemini|openai|none` env var. `none` (or missing keys) makes
the fallback provider the default so the whole app runs with zero external credentials.
Every AI call goes through one function: `call_structured(prompt, PydanticModel)`, which
validates the response, retries once on schema failure, and falls back deterministically
on a second failure — this is the single choke point that guarantees "LLM never executes
financial actions" and "no unvalidated JSON reaches the policy engine."

### 1.5 Why not a bigger/agentic architecture

No multi-step autonomous agent loop, no tool-calling LLM with direct DB/payment access,
no unbounded retries. The workflow is a fixed DAG with one LLM call for diagnosis and one
for action proposal, each independently swappable for the deterministic fallback. This is
intentional: recoverable revenue is a bounded-risk financial domain, and the eval story
("policy blocked N actions", "human escalation rate X%") is more convincing to reviewers
than an agent with a large action space.

---

## 2. Database Schema

Postgres in prod, SQLite fallback locally (same SQLAlchemy models — no dialect-specific
features). All monetary values stored as integer paise/minor-units (matches Razorpay
convention) to avoid float rounding errors.

```
Customer
├── id                    UUID PK
├── external_customer_id  str, indexed
├── segment               enum(new, standard, high_value) — derived, recomputed on read
├── created_at            datetime
└── opted_out             bool, default false

Payment
├── id                    UUID PK
├── customer_id           FK → Customer.id, indexed
├── razorpay_payment_id   str, nullable, indexed
├── amount                int (minor units)
├── currency              str, default "INR"
├── payment_method        enum(card, upi, netbanking, wallet, emi)
├── status                enum(created, failed, recovered, unrecoverable, pending)
├── source                enum(webhook, csv, simulator)
├── dataset_split         enum(train, holdout) — set at generation time, immutable
├── created_at             datetime
└── updated_at             datetime

PaymentAttempt
├── id                    UUID PK
├── payment_id            FK → Payment.id, indexed
├── attempt_number        int
├── status                enum(success, failed)
├── failure_code           str, nullable
├── failure_reason         str, nullable
├── timestamp              datetime
└── raw_event_json         JSON  (original normalized event, for audit)

RecoveryCase
├── id                    UUID PK
├── payment_id            FK → Payment.id, indexed, unique-per-open-case
├── status                enum(open, recovered, stopped, escalated, closed_unrecovered)
├── risk_score             float
├── revenue_at_risk         int (minor units)
├── recovery_attempts       int, default 0
├── opened_at               datetime
└── closed_at               datetime, nullable

AIAnalysis
├── id                    UUID PK
├── recovery_case_id       FK → RecoveryCase.id, indexed
├── stage                  enum(diagnosis, action_proposal)
├── model_provider          str  ("gemini" | "openai" | "fallback_rules")
├── raw_output_json         JSON
├── diagnosis               str, nullable
├── confidence               float, nullable
├── reasoning_summary        str  (business-facing only, no chain-of-thought)
├── schema_valid             bool
└── created_at               datetime

RecoveryAction
├── id                    UUID PK
├── recovery_case_id       FK → RecoveryCase.id, indexed
├── proposed_action         enum(RETRY_PAYMENT, SEND_REMINDER, OFFER_ALTERNATE_METHOD,
│                                SCHEDULE_RETRY, ESCALATE_HUMAN, STOP)
├── policy_allowed           bool
├── policy_reason            str
├── executed                 bool, default false
├── execution_result          enum(success, failed, skipped), nullable
├── revenue_recovered          int (minor units), default 0
├── human_override             enum(approve, reject, escalate, stop), nullable
└── created_at                 datetime

AuditLog
├── id                    UUID PK, append-only (no UPDATE, no DELETE at app layer)
├── event_id                str, indexed
├── payment_id               FK → Payment.id, indexed
├── stage                     enum(PAYMENT_FAILED, RISK_DETECTED, AI_DIAGNOSED,
│                                  ACTION_PROPOSED, POLICY_APPROVED, POLICY_BLOCKED,
│                                  ACTION_EXECUTED, PAYMENT_RECOVERED, ESCALATED,
│                                  HUMAN_DECISION, AI_FALLBACK_USED, ERROR)
├── payload_json               JSON  (snapshot of relevant state at this step)
├── system_version              str  (git short-sha or semver)
└── timestamp                   datetime, indexed

EvaluationResult
├── id                    UUID PK
├── run_id                  str
├── dataset_split            enum(holdout)
├── metrics_json              JSON  (the 15 metrics from spec §7)
└── created_at                 datetime
```

Indexes: `Payment(customer_id, status)`, `PaymentAttempt(payment_id, attempt_number)`,
`AuditLog(payment_id, timestamp)`, `RecoveryCase(status)`. Foreign keys use `ON DELETE
RESTRICT` — audit integrity must never be silently cascaded away.

---

## 3. API Contract

Base path `/api`. All monetary fields in response bodies are minor units (int) with a
paired `_display` string field (e.g. `amount: 499900, amount_display: "₹4,999.00"`).

```
POST   /api/events/payment            Ingest one normalized payment event
POST   /api/events/webhook            Razorpay Test Mode webhook receiver
                                       (verifies X-Razorpay-Signature)
POST   /api/simulation/generate       {records, seed} → generates synthetic dataset,
                                       tags rows train/holdout
POST   /api/recovery/run              Runs the full pipeline over open cases
                                       (batch) → {processed, recovered, escalated, blocked}
POST   /api/recovery/{payment_id}/execute   Force-run pipeline for one payment
POST   /api/recovery/{payment_id}/approve   Human approves a queued action
POST   /api/recovery/{payment_id}/reject    Human rejects
POST   /api/recovery/{payment_id}/escalate  Human escalates further
POST   /api/recovery/{payment_id}/stop      Human stops recovery permanently

GET    /api/payments                  ?status=&segment=&page=  list + filters
GET    /api/payments/{payment_id}     full detail incl. AI analysis + audit timeline
GET    /api/recovery/queue            human-review queue (open + escalated cases)
GET    /api/audit                     ?payment_id=&stage=&from=&to=
GET    /api/metrics                   dashboard overview numbers (live, not eval-only)
GET    /api/evaluation                latest EvaluationResult + history
GET    /api/health                    {status, ai_provider, db, version}
```

Example: `POST /api/recovery/{payment_id}/execute` response

```json
{
  "payment_id": "pay_8f21...",
  "risk": {"risk_score": 0.87, "revenue_at_risk": 1299900, "risk_category": "payment_failure"},
  "diagnosis": {"diagnosis": "temporary_failure", "confidence": 0.89, "model_provider": "gemini"},
  "proposed_action": {"action": "SCHEDULE_RETRY", "confidence": 0.86},
  "policy": {"allowed": true, "reason": "Within retry limit; high-value returning customer"},
  "execution": {"executed": true, "result": "success", "revenue_recovered": 1299900},
  "audit_trail_id": "aud_...",
  "case_status": "recovered"
}
```

Error envelope (used consistently, incl. 422/500/503):

```json
{"error": {"code": "AI_SCHEMA_INVALID", "message": "...", "fallback_used": true}}
```

---

## 4. Folder Structure

```
razorrecover-ai/
  backend/
    app/
      api/                # FastAPI routers, one file per resource
      core/                # config, logging, security, exceptions
      models/              # SQLAlchemy ORM models
      schemas/              # Pydantic request/response + AI I/O schemas
      services/
        risk_detector/
        diagnosis/
        recovery_agent/
        policy_engine/
        payment_gateway/     # razorpay_client.py + mock_simulator.py behind one interface
        audit/
        evaluation/
        ai/                   # provider abstraction (2.4 above)
      db/                    # session, migrations (alembic)
      main.py
    tests/
  frontend/
    src/
      components/
      pages/                # Overview, AtRisk, Queue, PaymentDetail, AIDecisions, Audit, Evaluation, Settings
      services/               # api client
      hooks/
      types/
  data/
    synthetic/                # generated CSV/JSON, gitignored except .gitkeep + sample
    evaluation/                # holdout split + evaluation run outputs
  scripts/
    generate_data.py
    run_evaluation.py
    seed_database.py
  tests/
    unit/
    integration/
    evaluation/
  docs/
    architecture.md           # this file
    decisions.md
    failure-recovery.md
  docker-compose.yml
  README.md
  .env.example
```

---

## 5. Synthetic Dataset Schema

Generated by `scripts/generate_data.py --records 1000 --seed 42`, deterministic given a
seed. Output: `data/synthetic/payments_seed{N}.jsonl`, one normalized event per line, plus
a manifest recording the seed, category distribution, and train/holdout split ratio
(default 70/30, split by `customer_id` hash so a customer's history doesn't leak across
the split).

```json
{
  "event_id": "evt_000123",
  "customer_id": "cust_00042",
  "payment_id": "pay_000123",
  "amount": 499900,
  "currency": "INR",
  "status": "failed",
  "failure_code": "BANK_DECLINE",
  "failure_reason": "Issuing bank declined the transaction",
  "timestamp": "2026-07-14T09:12:03Z",
  "payment_method": "card",
  "attempt_number": 2,
  "customer_history": {
    "previous_successful_payments": 11,
    "previous_failed_payments": 2,
    "lifetime_value": 5480000,
    "last_successful_payment_at": "2026-06-30T10:00:00Z"
  },
  "ground_truth": {
    "category": "bank_decline",
    "is_recoverable": true,
    "dataset_split": "holdout"
  }
}
```

`ground_truth` is generation-time metadata used only by the evaluation engine to score
diagnosis accuracy and false positive/negative rates — it is never passed to the AI
diagnosis/recovery services, which only see the fields a real system would have at
inference time.

Category mix (configurable weights, defaults):

| Category | Share | Recoverable? | Simulated retry success prob. |
|---|---|---|---|
| temporary_failure | 20% | yes | 0.75 |
| insufficient_funds | 15% | yes | 0.30 |
| bank_decline | 15% | mostly no | 0.10 |
| expired_instrument | 10% | no (needs new method) | 0.05 direct retry / 0.55 via `OFFER_ALTERNATE_METHOD` |
| repeated_failure (3+) | 10% | escalate | n/a — policy forces `ESCALATE_HUMAN` |
| checkout_abandonment | 15% | yes | 0.40 via `SEND_REMINDER` |
| overdue_invoice | 10% | yes | 0.50 via `SEND_REMINDER`/`SCHEDULE_RETRY` |
| already_successful (control) | 5% | n/a | policy must `STOP`, tests false-positive guard |

---

## 6. Implementation Plan (Phase-by-Phase)

Matches spec §24, restated as concrete exit criteria per phase so "runnable before moving
on" is checkable, not vibes-based.

| Phase | Deliverable | Exit criteria |
|---|---|---|
| 1 | This doc + DB models + alembic migration | `alembic upgrade head` succeeds on SQLite and Postgres |
| 2 | FastAPI skeleton, `/api/health`, config/env loading | `uvicorn` boots, health check returns provider + db status |
| 3 | Payment simulator (`generate_data.py`) | 1000+ records generated, deterministic under fixed seed (hash-checked in test) |
| 4 | `RiskDetector` | Unit tests cover each risk category, no LLM calls in this module |
| 5 | `DiagnosisEngine` w/ provider abstraction + fallback | Runs fully with `AI_PROVIDER=none`; schema validation test with intentionally malformed LLM output |
| 6 | `RecoveryAgent` | Returns only from the 6 allowed actions; cannot import gateway/db-write modules (enforced by a lint/import test) |
| 7 | `PolicyEngine` | Table-driven tests for every rule in spec §4, incl. retry-limit and opt-out |
| 8 | `ActionExecutor` (simulator + Razorpay Test Mode) | Simulator probabilities configurable via env/YAML; Test Mode path behind explicit flag |
| 9 | `AuditLogger` | Every state transition in §9's chain produces exactly one row; append-only enforced at repo layer |
| 10 | `EvaluationEngine` + `run_evaluation.py` | Runs on holdout split only (raises if given train split); outputs all 15 metrics |
| 11 | React dashboard (8 pages) | Talks to live API, no mocked frontend data once backend is up |
| 12 | Razorpay Test Mode webhook integration | Signature verification test with valid + tampered payloads |
| 13 | Failure injection suite | Each of the 7 failure scenarios in §10 has a reproducible test/demo trigger |
| 14 | Full test suite | Unit + integration for all items in spec §19 |
| 15 | Docker/compose | `docker compose up` reaches a working dashboard with zero manual steps |
| 16 | README + architecture diagram | All 19 sections from spec §20 present |
| 17 | 5-minute demo script | Scripted run-through of §22's two demo cases, timed |

At the end of every phase I will: run the tests for that phase, show the exact commands
used, list files changed, and flag anything left open — no silent skips.

---

## 7. Open Design Decisions Deferred to Later Phases

- Exact LLM prompt templates for `DiagnosisEngine`/`RecoveryAgent` — drafted in Phase 5/6
  alongside their Pydantic output schemas, not before, so the schema and prompt are
  designed together.
- Whether n8n webhook integration is a thin optional adapter or skipped entirely if time-
  constrained — default plan is to stub the webhook endpoint and document it as optional,
  prioritizing the core loop and evaluation honesty over integration breadth.
- Exact false-positive cost constants (support ticket cost, processing fee per attempt) —
  will live in `core/config.py` as named constants with a comment justifying each figure,
  not buried in code.

**Next step:** Phase 2 — FastAPI backend skeleton + SQLAlchemy models translating the
schema in §2, with `/api/health` wired to the provider abstraction. Say the word and I'll
build it.
