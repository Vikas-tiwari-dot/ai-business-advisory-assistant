# 5-Minute Demo Script

Rehearsed against a real run of this codebase (`seed=42`, `AI_PROVIDER=none`).
Numbers below are real observed output, not invented — see the "reproducible
vs. varies" note at each step for which numbers you can expect to see exactly
and which will vary slightly run to run.

**Setup, before the timer starts:** `docker compose up`, wait for all three
services healthy, have http://localhost:5173 open in a browser tab.

---

### 0:00 – 0:30 — The one-line pitch

> "RazorRecover AI takes failed payments, figures out why they failed, decides
> what to safely do about it, and shows you the entire decision chain — not
> a black box. Everything you're about to see is either running on real
> deterministic business rules, or a real LLM call with a documented
> fallback if that call fails. No real money moves at any point."

Point at the sidebar: 8 pages, each doing one job in the pipeline.

### 0:30 – 1:15 — Generate live traffic

On **Overview**, click **"Generate 300 payments."**

> "This runs 300 synthetic payment events through the full five-stage
> pipeline right now — risk detection, AI diagnosis, a bounded recovery
> proposal, a policy safety gate, and execution — and persists every step."

Watch the stat cards populate. Say the numbers as they land:

- **Revenue at risk**: ~₹28.8 lakh (*reproducible* — event generation and
  risk scoring are fully deterministic under this seed)
- **Human escalations**: exactly **114** (*reproducible* — diagnosis and
  policy decisions are deterministic)
- **Blocked actions**: exactly **59** (*reproducible*, same reason)
- **Revenue recovered**: ~₹3.6–3.8 lakh (*varies slightly run to run* — live
  execution outcomes use real randomness; only the offline evaluation script
  is fully seeded end-to-end)

> "Notice escalations and blocked actions are identical every single time
> you run this with seed 42 — those come from deterministic rules. Revenue
> recovered wiggles a little because the actual retry outcomes are genuinely
> randomized, the same way real payment retries are uncertain."

### 1:15 – 2:15 — Scenario 1: a clean recovery, full audit trail

Go to **Payments**, filter by status = `recovered`, click into any one.

> "Here's the decision chain for one payment, top to bottom, immutable."

Walk the **Decision timeline** stage chain out loud:
`PAYMENT_FAILED → RISK_DETECTED → AI_DIAGNOSED → AI_FALLBACK_USED →
ACTION_PROPOSED → POLICY_APPROVED → ACTION_EXECUTED → PAYMENT_RECOVERED`

> "AI_FALLBACK_USED shows up because we're running with no API key configured
> right now — that's the deterministic fallback engine, and it's not a lesser
> mode: on held-out data it hits 94.2% diagnosis accuracy on its own."

Scroll to **AI analyses** — point at `reasoning_summary` — note it's capped
at 300 characters by the schema itself, not just a prompt request.

### 2:15 – 3:15 — Scenario 2: safety catches a repeated failure

Go to **Recovery Queue**.

> "114 payments landed here instead of being auto-retried. Let's look at
> why."

Click into the top item — diagnosis is `repeated_failure` at ~90%
confidence, proposed action is `ESCALATE_HUMAN`, policy says
`Policy checks passed.`

> "This payment already failed 3+ times. The system doesn't need an LLM to
> know that's a bad sign — attempt count alone triggers this. The agent
> itself proposed escalating rather than retrying again, and the policy
> engine agreed. This is defense in depth: two independent layers landed on
> 'don't touch this automatically,' not just one."

Back on the Queue page, hit **Approve** on one item, **Reject** on another —
show the queue count drop, then open one of those payments and point at the
new `HUMAN_DECISION` entry in its timeline.

### 3:15 – 4:15 — Evaluation: honest metrics, not vanity metrics

Go to **Evaluation**. If it says "no report yet," run in a terminal:

```bash
python scripts/run_evaluation.py --input data/synthetic/payments_seed42.jsonl --seed 42
```

then reload. Point at:

- **AI diagnosis accuracy: 94.2%**, **action selection accuracy: 94.5%** —
  scored only against the held-out 30% split, never touched by anything else
  in this codebase.
- **False positive rate: 50.6%.**

> "This number looks bad out of context, and I want to be upfront about it
> rather than hide it. When a failure category has, say, a 30% base recovery
> probability, correctly attempting recovery on all of them still means
> roughly 70% of those individual attempts won't pan out — that's the honest
> cost of trying anything short of a sure thing. I checked: the math from
> the category probabilities alone predicts about 60% for this same slice.
> A system that only attempted certain wins would show a prettier number and
> recover less real revenue. I'd rather show you the true number."

Point at **Net recovered value** vs. **Revenue recovered** — explain the
false-positive cost model exists specifically to keep this honest, even
though at current unit-cost assumptions it barely moves the number.

### 4:15 – 5:00 — Close: what's real, what's safe, wrap

Go to **Settings**.

> "Quick honesty check, since this matters in fintech: payment data is
> always synthetic. Money movement is always simulated. AI calls are real
> when a key is configured, deterministic fallback otherwise — both paths
> fully audited. Razorpay Test Mode, when configured, creates real Payment
> Links, but since Razorpay has no 'retry a failed payment' concept, we
> report that as *initiated*, not *recovered* — we don't overclaim.
>
> Every decision you saw today has a full paper trail. That's the actual
> pitch: not 'the AI is smart,' but 'you can verify exactly why the system
> did what it did, every time, and the safety rules are enforced whether or
> not the AI cooperates.'"

---

## If something goes wrong during the live demo

- **Queue is empty after generating**: seed 42 at 300 records reliably
  produces 114 escalations (deterministic) — if it's empty, the generation
  call likely failed; check the terminal running `docker compose logs backend`.
- **Evaluation page says unavailable**: the offline script hasn't been run
  yet against this container's database — run the command in §3:15 above.
- **Numbers don't match this script exactly**: revenue-recovered figures
  are expected to vary (see the reproducibility note above); escalation and
  blocked-action counts should not vary under seed 42 — if those differ,
  something changed in the diagnosis/policy code since this script was
  rehearsed against the current version.
