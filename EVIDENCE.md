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
