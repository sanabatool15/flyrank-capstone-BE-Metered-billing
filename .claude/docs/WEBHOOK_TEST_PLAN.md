# Webhook Test Plan — `customer.subscription.updated`

`src/services/stripe_service.py::_handle_subscription_updated` (lines
132-170) is dispatched from `handle_webhook_event` but has zero test
coverage in `tests/test_webhooks.py` — only `checkout.session.completed`
and `customer.subscription.deleted` are tested. This doc specifies the test
cases to add, following the exact patterns already established in
`tests/test_webhooks.py` (`_stripe_event()` helper, `patch(
"src.services.stripe_service.stripe.Webhook.construct_event",
return_value=event)`, `make_tenant` fixture from `tests/conftest.py`).

## Where to add these

Append to `tests/test_webhooks.py` (do not create a new file — this project
keeps all webhook tests in one file per the existing convention).

## The logic under test

```python
plan = "pro" if status in ("active", "past_due", "trialing") else "free"
sub_status = status if status in ("active", "past_due", "canceled") else "active"
```

Two independent mappings to verify, plus the tenant-resolution branch
(existing `Subscription` row found by `stripe_subscription_id` vs. falling
back to `stripe_customer_id` lookup) and the `current_period_end`
timestamp conversion.

## Test cases to add

### 1. `test_subscription_updated_new_subscription_creates_row`
No local `Subscription` row exists yet for this `stripe_subscription_id`
(simulates receiving `customer.subscription.updated` before/without ever
seeing `checkout.session.completed` — a valid real-world ordering since
Stripe doesn't guarantee event delivery order). Tenant found via
`stripe_customer_id`.

- Setup: `make_tenant(db_session, plan="free", stripe_customer_id="cus_new")`
- Event: `sub_obj = {"id": "sub_new_1", "customer": "cus_new", "status":
  "active", "current_period_end": <some future unix ts>}`
- Assert: a `Subscription` row now exists with `stripe_subscription_id ==
  "sub_new_1"`, `status == "active"`, `plan == "pro"`,
  `current_period_end` matches the converted timestamp (UTC). Assert
  `tenant.plan` — **note**: `_handle_subscription_updated` does NOT call
  `tenant_repository.update_tenant_plan` (only `_handle_checkout_completed`
  and `_handle_subscription_deleted` touch `Tenant.plan` directly per the
  current code) — so assert `tenant.plan` is **unchanged** (stays
  `"free"`) even though the `Subscription.plan` is now `"pro"`. This is a
  real behavior worth locking in with a test and flagging in the PR
  description as a possible follow-up inconsistency (a tenant could have
  `Subscription.plan == "pro"` while `Tenant.plan` still reads `"free"`
  until a `checkout.session.completed` event arrives) — but per this task's
  scope, test the code as it exists, don't silently "fix" it.

### 2. `test_subscription_updated_active_to_past_due`
Status transition on an existing subscription.

- Setup: tenant + existing `Subscription(stripe_subscription_id="sub_pd_1",
  status="active", plan="pro")` via
  `subscription_repository.upsert_subscription` (same pattern as
  `test_subscription_deleted_reverts_plan_to_free`), `db_session.commit()`.
- Event: `sub_obj = {"id": "sub_pd_1", "customer": tenant.stripe_customer_id,
  "status": "past_due", "current_period_end": <ts>}`
- Assert: `Subscription.status == "past_due"`, `Subscription.plan ==
  "pro"` (per the mapping, `past_due` still counts as `pro` — this is the
  key business rule: a past-due tenant keeps Pro access/limits until
  Stripe actually cancels, it's just flagged so `POST /generate` can
  return `402` per `API_CONTRACTS.md`'s quota-check step, which checks
  `Subscription.status` for `past_due`/`canceled` — **not**
  `Subscription.plan`).

### 3. `test_subscription_updated_past_due_to_active`
Recovery transition (customer fixed their card).

- Setup: existing `Subscription(status="past_due", plan="pro")`.
- Event: `status="active"`.
- Assert: `status == "active"`, `plan == "pro"` (unchanged — was already
  pro).

### 4. `test_subscription_updated_trialing_to_active`
Trial conversion — exercises the `trialing` branch of the plan mapping
specifically (distinct from `active`/`past_due`).

- Setup: existing `Subscription(status="active", plan="pro")` — actually,
  to exercise `trialing` meaningfully, first send one event with
  `status="trialing"` and assert `plan == "pro"`, `sub_status` falls to
  `"active"` **because `"trialing"` is not in `("active", "past_due",
  "canceled")`** — this is the second, separate mapping's fallback branch:
  `sub_status = status if status in (...) else "active"`. So assert the
  stored `Subscription.status` is `"active"` (not literally `"trialing"`,
  since that value isn't in the app's status vocabulary at all — see
  `sub_status_enum` in `db_models.py`, which only defines `active`,
  `past_due`, `canceled`). This is an important, easy-to-miss case: Stripe
  can send `trialing` but the local enum has no such value, and the
  current code silently maps it to `"active"` rather than raising — assert
  that mapping explicitly so a future refactor can't silently break it.
  Then send a second event with `status="active"` and assert nothing
  surprising changes (still `active`/`pro`).

### 5. `test_subscription_updated_unrecognized_status_falls_back_to_free_and_active`
Defends the `else` branches of both mappings simultaneously with a status
Stripe could plausibly send that isn't explicitly handled, e.g.
`"incomplete_expired"`.

- Setup: existing `Subscription(status="active", plan="pro")`.
- Event: `status="incomplete_expired"`.
- Assert: `plan == "free"` (not in the pro-tuple), `status == "active"`
  (not in the sub_status-tuple, falls back to `"active"` — document in a
  test comment that this fallback-to-"active" for an unrecognized status
  is arguably a bug worth flagging, since "incomplete_expired" should
  probably read as inactive/canceled, not active — but again, test current
  behavior, note it as a follow-up, don't fix it silently in a test-plan
  task).

### 6. `test_subscription_updated_plan_change_price_id_irrelevant`
Per API_CONTRACTS.md's callout ("plan/price change" is one of the
transitions that matters) — this codebase's `_handle_subscription_updated`
does not actually branch on price/product ID at all, only on `status`. So
this test's purpose is to **document that a Stripe price/plan change alone
(status unchanged, e.g. still `"active"`) does not change local `plan`, or
`current_period_end`, unless it happens to also be carried in the same
event's `current_period_end`/`status` fields**. Concretely:

- Setup: existing `Subscription(status="active", plan="pro",
  current_period_end=<old_ts>)`.
- Event: `status="active"` (unchanged), `current_period_end=<new_ts>`
  (Stripe sends this on every renewal/plan-change event even without a
  status change).
- Assert: `current_period_end` is updated to `<new_ts>` (this DOES get
  synced — `_handle_subscription_updated` unconditionally upserts
  `current_period_end` every time), `plan` stays `"pro"` (status-derived,
  unaffected by the period-end bump).

### 7. `test_subscription_updated_missing_current_period_end`
Defends the `None`-handling branch:
```python
current_period_end = (
    datetime.fromtimestamp(current_period_end_ts, tz=timezone.utc)
    if current_period_end_ts else None
)
```
- Event: `current_period_end` key omitted entirely (or explicitly `None`).
- Assert: no exception raised, `Subscription.current_period_end` is
  `None`, other fields still sync correctly.

## Payload shape reference

All `customer.subscription.updated` test events use this shape (matching
`_stripe_event()`'s existing signature — `data.object` is a plain dict in
tests, real Stripe sends a `StripeObject`, already handled by
`handle_webhook_event`'s `to_dict()` conversion):

```python
def _sub_updated_event(event_id, sub_id, customer_id, status, current_period_end=None):
    return _stripe_event(
        event_id,
        "customer.subscription.updated",
        {
            "id": sub_id,
            "customer": customer_id,
            "status": status,
            "current_period_end": current_period_end,
        },
    )
```
Add this helper alongside the existing `_stripe_event()` at the top of
`tests/test_webhooks.py`.

## Coverage summary table

| Test | status in event | plan mapping exercised | sub_status mapping exercised |
|---|---|---|---|
| 1 new subscription | active | pro | active |
| 2 active→past_due | past_due | pro | past_due |
| 3 past_due→active | active | pro | active |
| 4 trialing→active | trialing, then active | pro (trialing branch) | active (fallback branch) |
| 5 unrecognized status | incomplete_expired | free (else branch) | active (else branch) |
| 6 plan/period change, status same | active | pro (unchanged) | active (unchanged); current_period_end updated |
| 7 missing current_period_end | active | pro | active; current_period_end stays None |

Every branch of both ternaries in `stripe_service.py:154-155` is hit by at
least one test above (`active`, `past_due`, `trialing` on the pro side;
`active`, `past_due`, `canceled`-not-tested-here-since-deleted-event-owns-
that on the sub_status side, plus the `else` fallback on both).
