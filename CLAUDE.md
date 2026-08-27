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
