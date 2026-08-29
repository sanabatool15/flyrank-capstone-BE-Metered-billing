# Design — Usage Metering & Billing Engine

## Problem
Track per-tenant API/token usage, enforce plan quotas, calculate cost
(including tiered AI-token pricing), and sync subscription state via
Stripe test-mode webhooks — all idempotent under retries.

## Data model
See `src/models/db_models.py`. Core: Tenant, Subscription, UsageEvent,
StripeEvent. Auth layer: User, Membership, Role, Permission,
RolePermission, AuditLog. Tenant isolation via `tenant_id` FK + index on
every tenant-owned table.

## Plans & quotas
| Plan | API calls/mo | AI tokens/mo |
|---|---|---|
| Free | 1,000 | 100,000 |
| Pro  | 50,000 | 5,000,000 |

## Roles & permissions
- Permissions: `api.use`, `usage.read`, `billing.manage`, `members.invite`
- Roles: `admin` (all four), `member` (`api.use`, `usage.read`)

## API surface
- `POST /generate` — billable action, idempotent, quota-checked
- `GET /usage` — rollup: used, limit, cost (role: any member)
- `POST /checkout` — Stripe Checkout session (role: billing.manage)
- `POST /webhooks/stripe` — signature-verified, deduped event handler
- Auth endpoints (login/session resolution) — via chosen IdP

## Idempotency strategy
`UniqueConstraint(tenant_id, idempotency_key)` on `UsageEvent` — DB-enforced,
not app-checked. Duplicate insert → catch `IntegrityError` → fetch + return
original row. Same pattern for Stripe: `event.id` as PK on `StripeEvent`,
insert failure = already processed, skip.

## Cost calculation
Pricing pinned in config, cents per 1M tokens. Reasoning tokens merged into
output before pricing. Rollup cost computed from summed token totals, not
summed per-event costs, to avoid rounding drift across many small events.

## Layers
Route → Service → Repository → DB. Authorization resolved per request:
identify user → resolve tenant (never from client input) → check
Membership → check RolePermission → scope every query by tenant_id.

## Non-goals (out of scope for core)
No invoicing, proration, or overage billing. No real payments — Stripe
test mode only. AI tokens are simulated counts, no model calls.
