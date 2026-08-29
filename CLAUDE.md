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
Phases 1–4 core implementation complete (design, core billing logic, Stripe
integration, cost/finalization) — landed together in commit `1b01818` rather
than as separate phase commits. From this point forward, each phase's work
must land in its own commit (or PR) so gates can be reviewed independently:
- Phase 2 (core billing logic): idempotent usage tracking, quota enforcement
  — GATE: duplicate request creates one event; boundary returns 429/402.
- Phase 3 (Stripe integration): Checkout flow, webhook verify + dedup,
  subscription sync — GATE: test Checkout flips a tenant Free → Pro via webhook.
- Phase 4 (cost & finalization): cost rollups, README + diagram, EVIDENCE.md
  — GATE: /usage numbers match pinned pricing constants.

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
