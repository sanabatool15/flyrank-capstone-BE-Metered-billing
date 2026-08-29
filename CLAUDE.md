# CLAUDE.md

## Project
Usage Metering & Billing Engine (FlyRank capstone)

## Stack
FastAPI + Postgres (Docker) + Stripe test mode

## Architecture
routers → services → repositories (strict layering, no skipping)

## Key rules
- Money in integer cents only
- Idempotency key required on billable actions
- Stripe test mode only
- Secrets in .env, never committed

## Current phase
Phase 1/2 (design + core billing logic)

## Reference docs
Design/spec docs live under `.claude/docs/` — read these before implementing
related code:
- `.claude/docs/DESIGN.md` — overall design: data model, plans/quotas,
  roles/permissions, API surface, idempotency strategy, cost calculation
- `.claude/docs/AUTHZ_DESIGN.md` — authorization request flow, layer
  responsibilities (router/service/repository), `require_permission()` spec
- `.claude/docs/API_CONTRACTS.md` — per-endpoint implementation spec:
  permissions, request/response shapes, status codes
- `.claude/docs/multitenant-auth-schema.md` — rationale for the identity/
  authorization schema (User, Membership, Role, Permission, RolePermission,
  AuditLog)
