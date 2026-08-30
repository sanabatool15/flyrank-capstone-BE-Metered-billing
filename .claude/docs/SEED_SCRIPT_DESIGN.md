# Seed / Demo Data Script Design — `scripts/seed.py`

No seed script exists today — only pytest fixtures (`tests/conftest.py`)
which build an in-memory SQLite DB per test and are not runnable against a
real Supabase database. Per the PDF spec, a stranger running this project
for the first time needs a way to get a demo tenant + user + valid bearer
token and immediately `curl` the API. This doc specifies `scripts/seed.py`.

## File location

`scripts/seed.py` at repo root (new top-level `scripts/` directory,
sibling to `src/` and `tests/`).

## The auth problem this script must solve

`get_current_actor()` (`src/auth/dependencies.py`) verifies a real Supabase
Auth access token (HS256 via `SUPABASE_JWT_SECRET`, or asymmetric via
Supabase's JWKS — see `src/auth/supabase.py`). There is no local
username/password login in this codebase; Supabase Auth issues tokens. A
seed script cannot create a *real* Supabase user session without calling
Supabase's Auth API. Two supported modes, auto-detected by what's
configured in `.env`:

1. **HS256 mode (`SUPABASE_JWT_SECRET` set, which is the common case for
   this project — see `.env.example`)**: the script mints its own
   HS256-signed demo token locally with `pyjwt` (already a dependency),
   signed with the same `SUPABASE_JWT_SECRET` the running app verifies
   against. This is exactly what `tests/conftest.py` already does for
   tests — the seed script reuses the same shape of claims, just against
   the real DB instead of SQLite. This is the default/primary path and
   needs no network call to Supabase.
2. **Asymmetric mode (JWKS, no local secret to sign with)**: the script
   cannot mint a valid token locally. It falls back to printing the demo
   user's email/`auth_provider_id` and instructions to obtain a real token
   via Supabase's `/auth/v1/token?grant_type=password` endpoint (requires a
   real Supabase Auth user with a password — out of scope to auto-create
   here since it needs the Supabase Admin API, not just DB writes). The
   script should still create the demo `User`/`Tenant`/`Membership` rows so
   the person only needs to obtain the token, not build out the fixtures.

Detection: `if get_settings().SUPABASE_JWT_SECRET: use_hs256_path() else
use_jwks_fallback_message()`.

## What the script creates (idempotent)

Reuses the DB session/engine exactly as the app does — `from
src.db.session import SessionLocal` — no separate connection config.

1. **Demo `User`**: `email="demo@flyrank.local"`,
   `auth_provider_id="seed-demo-user"` (a fixed, deterministic value —
   not a random UUID — so the script is safe to re-run: get-or-create by
   `auth_provider_id`, matching `get_current_actor()`'s lookup field).
2. **Global admin `Role` + permissions**: reuse
   `src.repositories.membership_repository.get_or_create_global_role` /
   `get_or_create_permission` / `grant_permission_if_missing` — the exact
   same idempotent helpers `tenant_service._ensure_global_admin_role` uses
   (see `TENANT_CREATION.md`) — do not reimplement role seeding a third
   time.
3. **Demo `Tenant`**: `name="Acme Demo"`, `plan="free"`. Get-or-create by
   name (query `Tenant` where `name == "Acme Demo"`; if none, create). Not
   a unique DB constraint, so the script does the lookup itself before
   inserting.
4. **`Membership`**: demo user as `admin` of the demo tenant — get-or-create
   via `membership_repository.get_membership` /
   `create_membership`, same pattern `POST /tenants` uses.
5. **Demo bearer token** (HS256 mode only): mint with `pyjwt`:
   ```python
   import time
   import jwt as pyjwt
   from src.config import get_settings

   def mint_demo_token(auth_provider_id: str) -> str:
       settings = get_settings()
       now = int(time.time())
       payload = {
           "sub": auth_provider_id,
           "aud": "authenticated",       # SUPABASE_AUDIENCE in src/auth/supabase.py
           "role": "authenticated",
           "iat": now,
           "exp": now + 60 * 60 * 24 * 7,  # 7 days — plenty for a demo session
       }
       return pyjwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")
   ```
   This mirrors `tests/conftest.py`'s existing token-minting helper — check
   that file for its exact fixture (likely `make_token`/`auth_headers`) and
   match its claim shape exactly so there's only one definition of "what a
   valid demo token looks like" to keep in sync mentally, even though the
   seed script's copy lives in production code and the conftest's lives in
   test code (they can't literally share code across `scripts/` and
   `tests/` without a shared test-utils module, which is unnecessary here).

## Script structure

```python
#!/usr/bin/env python
"""Seed demo data: a tenant, user, admin membership — so a stranger running
this project can immediately curl the API. Idempotent: safe to re-run.

Usage:
    uv run python scripts/seed.py
"""
import sys

from src.db.session import SessionLocal
from src.repositories import membership_repository
from src.models.db_models import Tenant, User

DEMO_USER_EMAIL = "demo@flyrank.local"
DEMO_AUTH_PROVIDER_ID = "seed-demo-user"
DEMO_TENANT_NAME = "Acme Demo"


def get_or_create_demo_user(db) -> User: ...
def get_or_create_demo_tenant(db) -> Tenant: ...
def ensure_demo_membership(db, user: User, tenant: Tenant) -> None: ...


def main() -> int:
    db = SessionLocal()
    try:
        user = get_or_create_demo_user(db)
        tenant = get_or_create_demo_tenant(db)
        ensure_demo_membership(db, user, tenant)
        db.commit()
    finally:
        db.close()

    print_summary(user, tenant)  # tenant_id, and either the minted token
                                  # (HS256 mode) or the JWKS fallback message
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## Expected console output (HS256 mode)

```
Seed complete.
  tenant_id: 3f9e2b7a-...
  user_id:   8c1d4f20-...
  role:      admin

Demo bearer token (valid 7 days):
  eyJhbGciOiJIUzI1NiIs...

Try it:
  curl -s http://localhost:8000/tenants/3f9e2b7a-.../usage \
    -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..."
```

This exact "Try it" curl block is what the README's Quickstart section
should reference (see README.md), so a stranger has a copy-pasteable path
from `docker compose up` → `uv run python scripts/seed.py` → a working
`curl` in under a minute.

## What it does NOT do

- Does not create a Stripe customer/subscription — the demo tenant starts
  on `free`, matching every other tenant's real lifecycle (`POST
  /tenants` always forces `free` too, per `TENANT_CREATION.md`). A demo
  Pro-plan walkthrough is a manual `POST /checkout` + Stripe test-mode
  Checkout completion, not something to fake by writing `plan="pro"`
  directly into the DB (that would bypass the one-true-path rule
  `TENANT_CREATION.md` establishes).
- Does not touch `usage_events` — leaves quota totals at zero so the
  demo tenant starts clean against `DESIGN.md`'s Free-plan limits (1,000
  API calls / 100,000 AI tokens).
- Does not run migrations or `create_all()` itself — assumes the app (or
  `alembic upgrade head`, once `MIGRATIONS_DESIGN.md` lands) has already
  created the schema. Document in the README that seed runs *after* the
  app has started at least once (or after migrations).

## Guardrails

Refuse to run if `DATABASE_URL` looks like it's pointed at something that
isn't a local/dev/test setup the user controls — this capstone doesn't need
that level of paranoia given Stripe test mode is the only "real" external
system involved, so skip building an environment allowlist; just document
in the script's docstring that it's meant for local/demo/Supabase-dev
projects, not shared production data.
