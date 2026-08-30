# Background Jobs Design — Subscription Reconciliation

The capstone spec requires ≥1 background job: slow/bulk work run off the
request path, with retries and a failure alert. This project's natural fit
is **Stripe subscription reconciliation** — polling Stripe for the true
status of every tenant with a `stripe_customer_id` and correcting local
`Subscription`/`Tenant.plan` drift caused by a missed or delayed webhook
delivery (webhooks are the fast path per `API_CONTRACTS.md`; this job is the
slow, periodic safety net).

## Why this job (not a usage rollup)

Usage rollups are already computed on-demand and cheaply in
`GET /usage` (per `DESIGN.md`'s cost-calculation note: computed from summed
token totals at read time, not a precomputed table) — there's no drift or
staleness to fix there, so a background rollup job would be manufactured
work. Subscription state, by contrast, is a real correctness risk already
called out in `API_CONTRACTS.md`'s webhook section: Stripe delivers
webhooks at-least-once but not guaranteed-instantly, and a dropped/delayed
delivery leaves `Tenant.plan` or `Subscription.status` stale until the next
webhook arrives — which might never come if, e.g., the subscription just
sits in `past_due` with no further Stripe-side event. Polling closes that
gap.

## File layout

```
src/jobs/
├── __init__.py
└── reconcile_subscriptions.py
```

New top-level package `src/jobs/`, sibling to `src/services/`,
`src/repositories/`, etc. — it is *not* a router or a service in the
routers→services→repositories request path; it's an out-of-band entrypoint
that itself calls into services/repositories the same way a router would.

## Entry point

```python
# src/jobs/reconcile_subscriptions.py
"""Background job: reconcile local Subscription/Tenant state against Stripe.

Run on demand:      python -m src.jobs.reconcile_subscriptions
Run on a schedule:   see "Scheduling" below.
"""

def run() -> "ReconciliationSummary":
    """Entry point invoked by `python -m src.jobs.reconcile_subscriptions`."""
    ...

if __name__ == "__main__":
    run()
```

### Function signatures

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class ReconciliationSummary:
    started_at: datetime
    finished_at: datetime
    checked: int = 0
    corrected: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def run() -> ReconciliationSummary:
    """Top-level entry: open a DB session, call reconcile_all(), log summary,
    write an alert row if failed > 0, close the session. Never raises —
    catches everything so a cron invocation always exits 0 unless the whole
    process itself is broken (e.g. can't connect to the DB at all, in which
    case it should exit non-zero so cron/alerting notices)."""


def reconcile_all(db: "Session") -> ReconciliationSummary:
    """Iterate every Tenant with a non-null stripe_customer_id, call
    reconcile_one() per tenant with retry, aggregate results."""


def reconcile_one(db: "Session", tenant: "Tenant") -> bool:
    """Reconcile a single tenant against Stripe. Returns True if a
    correction was made, False if already in sync. Raises on unrecoverable
    Stripe API errors (network, 5xx) — caller (reconcile_all) applies
    retry-with-backoff around this call, not inside it, so retry policy is
    isolated from reconciliation logic."""


def _with_retry(fn, *, max_attempts: int = 3, base_delay_s: float = 1.0):
    """Generic retry-with-exponential-backoff wrapper: base_delay_s * 2**n,
    n = 0,1,2 → 1s, 2s, 4s. Retries only on transient errors
    (stripe.error.APIConnectionError, stripe.error.APIError with
    http_status >= 500); re-raises immediately on
    stripe.error.InvalidRequestError / AuthenticationError (non-retryable —
    bad data or bad creds won't fix itself by waiting)."""
```

## Reconciliation logic (`reconcile_one`)

For each `Tenant` with `stripe_customer_id IS NOT NULL`:

1. `stripe.Subscription.list(customer=tenant.stripe_customer_id, limit=1,
   status="all")` — fetch the tenant's current Stripe subscription (test
   mode: one subscription per test-mode customer is the expected shape for
   this capstone; if Stripe returns zero subscriptions, treat as "no active
   subscription," see step 4).
2. Compare against the local `Subscription` row
   (`subscription_repository.get_subscription_by_stripe_id` or, if none
   exists locally, `get_subscription_by_tenant_id` — add this lookup to
   `src/repositories/subscription_repository.py` if it doesn't already
   exist) on three fields: `status`, `plan` (derived the same way
   `stripe_service._handle_subscription_updated` derives it — see below),
   `current_period_end`.
3. If any field differs, call
   `subscription_repository.upsert_subscription(...)` and
   `tenant_repository.update_tenant_plan(...)` — reuse the **exact same**
   plan-mapping rule already in `src/services/stripe_service.py:154-155`
   (`plan = "pro" if status in ("active", "past_due", "trialing") else
   "free"`) so the job and the webhook handler can never disagree on what a
   given Stripe status means. Extract that one-line rule into a shared
   helper (`src/services/stripe_service.py::map_status_to_plan(status:
   str) -> str`) and import it from the job, rather than duplicating the
   tuple literal in two files.
4. If Stripe has no subscription for that customer at all and a local
   `Subscription` row exists in a non-`canceled` status, treat it like a
   `customer.subscription.deleted` event: set local status to `canceled`
   and `Tenant.plan` to `free`.
5. Commit per-tenant (not one giant transaction for the whole run) so one
   tenant's failure doesn't roll back corrections already made for others.
6. Write an `AuditLog` row when a correction is made:
   `action="subscription.reconciled"`, `resource_type="subscription"`,
   `resource_id=stripe_subscription_id`, `actor_user_id=None` (system-
   initiated, per the existing nullable-actor comment on `AuditLog` in
   `db_models.py`), `decision="allowed"`, `reason="drift corrected: local
   status/plan did not match Stripe"`.

## Retry policy

Applied in `reconcile_all` around each `reconcile_one(db, tenant)` call via
`_with_retry`: up to 3 attempts, exponential backoff (1s, 2s, 4s), retrying
only transient Stripe API errors. A tenant that still fails after 3
attempts is recorded in `ReconciliationSummary.errors` and counted in
`.failed`; the loop continues to the next tenant (one tenant's persistent
failure must not abort reconciliation for the rest).

## Failure alert mechanism

Kept intentionally simple for a test-mode capstone — no external paging
service. Two layers:

1. **Structured CRITICAL log line** on any failure, via the standard
   `logging` module:
   ```python
   import logging
   logger = logging.getLogger("src.jobs.reconcile_subscriptions")
   ...
   logger.critical(
       "subscription reconciliation failed for tenant=%s after %d attempts: %s",
       tenant.id, max_attempts, exc,
   )
   ```
2. **Persisted alert row**, reusing `AuditLog` rather than inventing a new
   table (keeps the schema minimal, matches "keep it appropriate... not
   overengineered"): when `run()` finishes with `summary.failed > 0`, write
   one additional `AuditLog` row summarizing the run:
   `action="job.reconcile_subscriptions.failed"`,
   `resource_type="background_job"`, `resource_id=None`,
   `actor_user_id=None`, `decision="denied"` (repurposed here to mean
   "job did not fully succeed" — document this repurposing inline as a
   comment since `decision` is otherwise an authz-decision field),
   `reason=f"{summary.failed} tenant(s) failed reconciliation: {'; '.join(summary.errors[:5])}"`.
   This makes failures queryable (`SELECT * FROM audit_logs WHERE action =
   'job.reconcile_subscriptions.failed'`) without a new table or an external
   integration (email/Slack/PagerDuty) that would be overkill for Stripe
   test mode.

Both the log line and the `AuditLog` row happen inside `run()`'s top-level
`try/except`, so a bug in the alerting path itself can't crash the job.

## Scheduling (how it runs off the request path)

No process manager (Celery/APScheduler) is required for this capstone — the
job is a plain script invoked out-of-band, per `capstone.yaml`'s
`background_job.schedule` field (`"0 */6 * * *"`, i.e. every 6 hours). Two
supported invocation modes, both already implied by the `if __name__ ==
"__main__"` entry point:

- **Manual/demo**: `uv run python -m src.jobs.reconcile_subscriptions` — run
  it directly to show it working for grading/demo purposes.
- **Scheduled**: host-level cron (or `docker compose exec app uv run python
  -m src.jobs.reconcile_subscriptions` from an external cron on the Docker
  host) running the same command on the `capstone.yaml` schedule. Document
  this as a `crontab` line in the README rather than shipping a scheduler
  dependency:
  ```
  0 */6 * * * cd /path/to/repo && uv run python -m src.jobs.reconcile_subscriptions >> /var/log/reconcile.log 2>&1
  ```

This keeps the job dependency-free (no new package beyond what's already in
`pyproject.toml` — `stripe` and `sqlalchemy` are already dependencies) while
satisfying "off the request path" (it never runs inside a FastAPI request
handler) and "retries + failure alert" (per above).

## DB session handling

Reuses the same `SessionLocal` from `src/db/session.py` as the app does —
no separate DB config needed:

```python
from src.db.session import SessionLocal

def run() -> ReconciliationSummary:
    db = SessionLocal()
    try:
        summary = reconcile_all(db)
    finally:
        db.close()
    ...
```

## Testing notes (for the implementing engineer, not written here)

Unit tests belong at `tests/test_reconcile_subscriptions.py`, mocking
`stripe.Subscription.list` the same way `tests/test_webhooks.py` mocks
`stripe.Webhook.construct_event` (via `unittest.mock.patch` on
`src.jobs.reconcile_subscriptions.stripe...`). Cases to cover: (a) no drift
→ 0 corrections, (b) local `past_due` but Stripe now `active` → corrected +
`AuditLog` row written, (c) Stripe API raises a transient error on attempt 1
and 2, succeeds on attempt 3 → still counts as success, no failure alert,
(d) exhausts all 3 attempts → `failed` incremented, alert `AuditLog` row
written, CRITICAL log emitted.
