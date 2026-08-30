# EVIDENCE.md — Phase 2 Gate Proofs

Evidence generated from real `pytest` runs against the project's existing
in-memory SQLite + FastAPI `TestClient` test infrastructure
(`tests/conftest.py`), not a live docker/Postgres server. New tests were
added in `tests/test_evidence_gates.py`; no production code changes were
required — the existing implementation in `src/services/quota_service.py`
and `src/services/meter_service.py` already satisfies the boundary and
idempotency contracts exactly as specified in `.claude/docs/API_CONTRACTS.md`.

Endpoint under test: `POST /tenants/{tenant_id}/generate`
(permission `api.use`, header `Idempotency-Key` required, body
`{"usage_type": "api_call"}`), per `.claude/docs/API_CONTRACTS.md`.

Command run:

```
pytest tests/test_evidence_gates.py -v -s
```

Full captured output:

```
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-9.1.1, pluggy-1.6.0 -- /usr/local/bin/python
cachedir: .pytest_cache
rootdir: /home/user/flyrank-capstone-BE-Metered-billing
configfile: pyproject.toml
plugins: anyio-4.14.2, cov-7.1.0
collecting ... collected 3 items

tests/test_evidence_gates.py::test_gate_a_idempotent_duplicate_creates_one_event
GATE A resp1: 200 {'usage_event_id': 'c6154532-dcd8-4b80-b679-aef666f4c866', 'cost_cents': 1, 'remaining_quota': {'api_calls': 999, 'ai_tokens': 100000}}
GATE A resp2: 200 {'usage_event_id': 'c6154532-dcd8-4b80-b679-aef666f4c866', 'cost_cents': 1, 'remaining_quota': {'api_calls': 999, 'ai_tokens': 100000}}
GATE A DB count WHERE idempotency_key = gate-a-idem-key -> 1
PASSED
tests/test_evidence_gates.py::test_gate_b_quota_boundary_1000_ok_1001_rejected GATE B pre-seeded usage_events count: 999
GATE B request #1000: 200 {'usage_event_id': 'a6148801-b107-40ef-aba5-9ba07cb5bbbe', 'cost_cents': 1, 'remaining_quota': {'api_calls': 0, 'ai_tokens': 100000}}
GATE B request #1001: 429 {'detail': {'error': 'quota_exceeded', 'message': 'monthly API call limit reached (1000/1000)', 'retry_after': None}}
PASSED
tests/test_evidence_gates.py::test_gate_c_past_due_subscription_returns_402_regardless_of_quota GATE C past_due response: 402 {'detail': {'error': 'payment_required', 'message': 'subscription is past_due — update payment method', 'upgrade_url': '/tenants/{tenant_id}/checkout'}}
PASSED

=============================== warnings summary ===============================
../../../usr/local/lib/python3.11/dist-packages/fastapi/testclient.py:1
  /usr/local/lib/python3.11/dist-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
========================= 3 passed, 1 warning in 0.93s =========================
```

---

## Proof 1: Idempotency (Phase 2 gate — "duplicate request creates one event")

Test: `test_gate_a_idempotent_duplicate_creates_one_event`
(`tests/test_evidence_gates.py`)

Sent `POST /tenants/{tenant_id}/generate` twice with the identical header
`Idempotency-Key: gate-a-idem-key` and identical body
`{"usage_type": "api_call"}`.

- Response 1: `200`, `usage_event_id = c6154532-dcd8-4b80-b679-aef666f4c866`
- Response 2: `200`, `usage_event_id = c6154532-dcd8-4b80-b679-aef666f4c866`
  (same event id — the second call hit the `IntegrityError` branch in
  `meter_service.record_usage`, fetched the existing row via
  `usage_repository.get_usage_event_by_idempotency_key`, and returned the
  identical body without recomputing cost or inserting again)
- Raw DB assertion: `SELECT COUNT(*) FROM usage_events WHERE tenant_id = ? AND idempotency_key = 'gate-a-idem-key'` → **1**

## Proof 2: Quota boundary (429) at exactly 1000/1000

Test: `test_gate_b_quota_boundary_1000_ok_1001_rejected`
(`tests/test_evidence_gates.py`)

Free plan limit is `1_000` API calls/month (`PLAN_LIMITS["free"]["api_calls"]`
in `src/services/quota_service.py`). 999 `usage_events` rows were seeded
directly via the ORM for the tenant, then:

- Request **#1000** (999 existing + 1 new = 1000, the limit itself):
  `200`, `remaining_quota.api_calls = 0`
- Request **#1001** (would be 1001 > 1000):
  `429`, body:
  `{"error": "quota_exceeded", "message": "monthly API call limit reached (1000/1000)", "retry_after": null}`

The enforcement is `used_api_calls + request_quantity > limits["api_calls"]`
(`src/services/quota_service.py::check_quota`) — a request that lands
exactly on the limit is allowed, and only the request that would exceed it
is rejected. This already matched the spec exactly; **no code change was
required** to make this gate pass.

## Proof 3: past_due subscription → 402 (regardless of quota)

Test: `test_gate_c_past_due_subscription_returns_402_regardless_of_quota`
(`tests/test_evidence_gates.py`)

Created a `pro`-plan tenant with a `Subscription` row whose `status =
"past_due"` (well under its 50,000/month quota, proving the 402 gate fires
before/independent of the quota check). Hit `POST /generate`:

- Response: `402`, body:
  `{"error": "payment_required", "message": "subscription is past_due — update payment method", "upgrade_url": "/tenants/{tenant_id}/checkout"}`

This is enforced by `quota_service.check_subscription_status`, called in
`meter_service.record_usage` *before* `quota_service.check_quota` — so a
past_due tenant always gets 402, never 429, matching
`.claude/docs/API_CONTRACTS.md`'s flow step 2.

---

## Summary

All three gates passed against the existing implementation with **no
production code changes**. The quota boundary check already used the
correct comparison (`used + request > limit`, not `>=`), so the 1000th
call succeeds and the 1001st is rejected exactly as specified.

---

# EVIDENCE.md — Phase 3: Stripe Integration & Webhook Handling

Evidence generated from a real, live run: `docker compose up` against the
project's actual Supabase Postgres + Supabase Auth instance, the Stripe CLI
(`stripe listen` / `stripe trigger` / `stripe events resend`) in **test
mode**, and the actual Stripe-hosted Checkout page with card `4242 4242 4242
4242`. Not a mocked/unit-test run — this is the real end-to-end flow.

Two real bugs were found and fixed while producing this evidence (both
already committed to `claude/new-session-wikeph`):

1. **Missing `sslmode=require`** on the Supabase Postgres connection
   (`src/db/session.py`) — Supabase requires TLS; connections failed
   without it.
2. **`StripeObject.get(...)` no longer supported** by the pinned `stripe`
   SDK version — every webhook handler (`checkout.session.completed`,
   `customer.subscription.updated`, `customer.subscription.deleted`) used
   dict-style `.get()` on the event's `data.object`, which now raises
   `AttributeError` instead of returning the value. This meant **every
   real webhook delivery crashed with 500** and silently left tenants
   stuck on Free even after a successful Stripe payment. Fixed in
   `src/services/stripe_service.py` by converting to a plain dict via
   `.to_dict()` once at the top of `handle_webhook_event`, before any
   handler runs.

## Tenant Information

* **Tenant ID (`TID`)**: `608e422a-5449-4521-adf8-dbf9798d1161`
* **Initial Plan**: `free` (1,000 API calls / 100,000 AI tokens)
* **Final Plan**: `pro` (50,000 API calls / 5,000,000 AI tokens)

## Proof 1 — Checkout Flow Initiated

```bash
curl -X POST "http://localhost:8000/tenants/608e422a-5449-4521-adf8-dbf9798d1161/checkout" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target_plan":"pro"}'
```

**Result**: Stripe-hosted Checkout Session generated and returned as
`checkout_url`. Payment completed on the real Stripe Checkout page using
test card `4242 4242 4242 4242`, any future expiry, any CVC.

## Proof 2 — Webhook Processing & Database Upgrade

`stripe listen` forwarded the real event to the local webhook endpoint:

```text
2026-08-30 15:43:43 --> checkout.session.completed [evt_1UA6DEJ3N87X90J7hXeUrvAH]
2026-08-30 15:43:44 <-- [200] POST http://localhost:8000/webhooks/stripe
```

Verified the actual DB state change via the `/usage` endpoint:

```json
GET http://localhost:8000/tenants/608e422a-5449-4521-adf8-dbf9798d1161/usage

{
    "period_start": "2026-08-01T00:00:00Z",
    "period_end": "2026-09-01T00:00:00Z",
    "api_calls": { "used": 0, "limit": 50000 },
    "ai_tokens": {
        "input": 0, "cached_input": 0, "output": 0, "reasoning": 0,
        "used_total": 0, "limit": 5000000
    },
    "cost_cents": 0
}
```

**Result**: `tenants.plan` flipped `free` → `pro` via the verified webhook
(never via the `/checkout` call itself, per `API_CONTRACTS.md`).
`api_calls.limit` is now 50,000 and `ai_tokens.limit` is now 5,000,000 —
matching the Pro plan's pinned quota constants.

## Proof 3 — Forged Signature Rejection

```bash
curl -X POST http://localhost:8000/webhooks/stripe \
  -H "Stripe-Signature: t=123,v1=fake" \
  -H "Content-Type: application/json" \
  -d '{"type":"customer.subscription.updated"}'
```

**Response**: `400 Bad Request`, body `{"detail": "invalid signature"}`.

**Result**: A forged/unsigned payload is rejected before
`stripe_service.handle_webhook_event` ever runs — `verify_and_construct_event`
raises on signature mismatch and the router maps that straight to 400. No DB
state changed (confirmed no change on a subsequent `/usage` call).

## Proof 4 — Replay & Event Deduplication

Resent the **same** already-processed event id (not a fresh `stripe
trigger`, which mints a new event id each time and wouldn't actually test
dedup) via the Stripe CLI:

```bash
stripe events resend evt_1UA6aaJ3N87X90J7YHrgEDdJ
```

Queried `stripe_events` directly inside the running container after the
second delivery:

```bash
docker compose exec app python -c "
from src.db.session import SessionLocal
from src.models.db_models import StripeEvent
db = SessionLocal()
rows = db.query(StripeEvent).filter(StripeEvent.id=='evt_1UA6aaJ3N87X90J7YHrgEDdJ').all()
print(len(rows), [r.id for r in rows])
"
```

**Output**:
```text
1 ['evt_1UA6aaJ3N87X90J7YHrgEDdJ']
```

**Result**: Exactly one row exists despite two deliveries of the same event
id — `try_insert_stripe_event`'s insert-first pattern against the unique
primary key on `stripe_events.id` correctly deduplicated the replay, and the
second delivery's handler body never re-ran (per `handle_webhook_event`'s
early return on a failed insert).

## Summary

| Gate | Result |
|---|---|
| Real Checkout session created (test mode) | ✅ Pass |
| Webhook verified + tenant flipped Free → Pro | ✅ Pass (after StripeObject fix) |
| Forged signature rejected with 400, no state change | ✅ Pass |
| Replayed event deduplicated (1 row, not 2) | ✅ Pass |

Phase 3 gate — "test Checkout flips a tenant Free → Pro via webhook" — is
satisfied end-to-end against live Supabase Postgres, Supabase Auth, and
Stripe test mode.
