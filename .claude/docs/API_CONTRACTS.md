# API Contracts — Implementation Spec for Claude Code

Every endpoint below states its required permission explicitly. Implement each
via `Depends(require_permission("<name>"))` per `AUTHZ_DESIGN.md` — no route
skips this except the two marked "no auth," plus `POST /tenants` (documented
exception, no tenant to resolve yet — see `TENANT_CREATION.md`).

---

## `POST /tenants`
**Permission required:** none — see `TENANT_CREATION.md` for the full spec
and why this is the one documented exception to the normal
authenticate → membership → permission chain.

Creates a new tenant, always on plan `"free"` regardless of what the request
body's `plan` field says, and makes the caller its `admin` via a new
`Membership`.

**Body**
```json
{ "name": "Acme Inc", "plan": "free" }
```

**201 response**
```json
{
  "tenant_id": "uuid",
  "name": "Acme Inc",
  "plan": "free",
  "membership": { "role": "admin" }
}
```

- No valid bearer token → `401`.
- No `403` case — every authenticated user may create a tenant.

---

## `POST /tenants/{tenant_id}/generate`
**Permission required:** `api.use`

The dummy billable action. Meters usage, checks quota, calculates cost — all
in one idempotent operation.

**Headers**
- `Authorization: Bearer <token>` (required)
- `Idempotency-Key: <client-generated uuid>` (required — 400 if missing)

**Body**
```json
{
  "usage_type": "api_call" | "ai_tokens",
  "input_tokens": 0,
  "cached_input_tokens": 0,
  "output_tokens": 0,
  "reasoning_tokens": 0
}
```
(token fields ignored/defaulted to 0 when `usage_type` is `api_call`)

**Flow**
1. Resolve tenant + check `api.use` permission (see AUTHZ_DESIGN.md)
2. Quota check: current period usage + this request's quantity vs plan limit
   - subscription status is `past_due`/`canceled` → `402`
   - subscription active but over quota → `429`
3. Attempt insert into `usage_events` with `(tenant_id, idempotency_key)`
   - `IntegrityError` (duplicate key) → fetch existing row, return identical
     200 body, do NOT insert again, do NOT recompute cost
4. On successful insert: compute `cost_cents` for this event, store, return

**200 response**
```json
{
  "usage_event_id": "uuid",
  "cost_cents": 1234,
  "remaining_quota": { "api_calls": 998, "ai_tokens": 95000 }
}
```

**429 response**
```json
{ "error": "quota_exceeded", "message": "monthly API call limit reached (1000/1000)", "retry_after": null }
```

**402 response**
```json
{ "error": "payment_required", "message": "subscription is past_due — update payment method", "upgrade_url": "..." }
```

---

## `GET /tenants/{tenant_id}/usage`
**Permission required:** `usage.read` (both `admin` and `member` have this)

Rollup of current billing-period usage.

**200 response**
```json
{
  "period_start": "2026-08-01T00:00:00Z",
  "period_end": "2026-08-31T23:59:59Z",
  "api_calls": { "used": 340, "limit": 1000 },
  "ai_tokens": {
    "input": 12000, "cached_input": 4000, "output": 8000, "reasoning": 1500,
    "used_total": 25500, "limit": 100000
  },
  "cost_cents": 456
}
```
Cost here is computed from **summed token totals**, not summed per-event
`cost_cents` — see DESIGN.md's rounding-drift note. Recompute via the same
`calc_cost_cents()` used at write time.

---

## `POST /tenants/{tenant_id}/checkout`
**Permission required:** `billing.manage` (admin only)

Creates a Stripe Checkout session (test mode) for upgrading Free → Pro.

**Body**
```json
{ "target_plan": "pro" }
```

**200 response**
```json
{ "checkout_url": "https://checkout.stripe.com/..." }
```

Backend creates/reuses `stripe_customer_id` on `Tenant`, creates a Checkout
Session with the Pro price ID, returns the hosted URL. No plan/tenant mutation
here — that only happens via the verified webhook below.

---

## `POST /webhooks/stripe`
**No auth — public endpoint, but signature-verified**

Do NOT put this behind `require_permission`. Stripe calls this directly; there
is no user session. Security comes from signature verification, not auth.

**Flow**
1. Read raw request body (not parsed JSON — signature needs raw bytes)
2. Verify `Stripe-Signature` header against `STRIPE_WEBHOOK_SECRET`
   - invalid → `400`, do nothing else, do not log as a processed event
3. Check `stripe_events` table for `event.id`
   - already exists → `200` immediately, skip all processing (dedup)
4. Insert `StripeEvent(id=event.id, event_type=event.type)` — insert failure
   here (race: two webhook deliveries at once) also means "already handled,"
   return `200`
5. Handle by type:
   - `checkout.session.completed` → look up tenant by `stripe_customer_id`,
     set `plan = pro`, create/update `Subscription` row
   - `customer.subscription.updated` → sync `Subscription.status`, `.plan`,
     `.current_period_end`
   - `customer.subscription.deleted` → set `Subscription.status = canceled`,
     `Tenant.plan` back to `free`
6. Return `200` (Stripe retries on anything else)

---

## `POST /tenants/{tenant_id}/members`
**Permission required:** `members.invite` (admin only)

Adds an existing `User` (by email) as a `Membership` on this tenant.

**Body**
```json
{ "email": "user@example.com", "role": "member" | "admin" }
```

**200 response**
```json
{ "membership_id": "uuid", "user_id": "uuid", "role": "member" }
```

- Email not found in `users` → `404` (or `409` if you want an invite-flow
  stub instead — pick one, document it, don't half-implement both)
- Already a member of this tenant → `409`

---

## Auth endpoints (login/session)

Handled by your IdP (Supabase or equivalent) — this backend does not
implement password/credential verification. The only backend-side piece is:

## `POST /auth/sync`
**No auth beyond a valid IdP token — this endpoint creates the local User row**

Called once after IdP login succeeds, to ensure a local `users` row exists
matching `auth_provider_id`. Idempotent by nature (upsert on
`auth_provider_id`) — no separate idempotency key needed since it's not a
billable/metered action.

---

## Summary — permission matrix

| Endpoint | Permission | admin | member |
|---|---|---|---|
| `POST /tenants` | none (creates the first membership) | — | — |
| `POST /generate` | `api.use` | ✅ | ✅ |
| `GET /usage` | `usage.read` | ✅ | ✅ |
| `POST /checkout` | `billing.manage` | ✅ | ❌ |
| `POST /members` | `members.invite` | ✅ | ❌ |
| `POST /webhooks/stripe` | none (signature only) | — | — |
| `POST /auth/sync` | none (valid IdP token only) | — | — |
</content>
