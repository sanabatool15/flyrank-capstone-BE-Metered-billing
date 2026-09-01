# BUILDLOG

Honest account of how this project was built, and where/how AI (Claude) was used.

## Approach: design docs first, then implementation

I started by writing the design docs myself, using Claude as a drafting tool
against the capstone PDF brief rather than having Claude invent the design
from scratch. The loop for each area was:

1. I fed Claude the capstone PDF plus my own notes on what that area needed.
2. Claude drafted a design `.md` for it.
3. I read, edited, and corrected the draft — schema names, field choices,
   flow steps — until it matched what I actually wanted, not just what Claude
   guessed.
4. Only once the doc was in a state I'd signed off on did I hand it back to
   Claude Code to implement.

This produced the docs under `.claude/docs/`:
- `multitenant-auth-schema.md` / `AUTHZ_DESIGN.md` — authentication and
  authorization (User/Membership/Role/Permission/RolePermission/AuditLog,
  `require_permission()` flow).
- `DESIGN.md` — the metering API: usage events, quotas, idempotency.
- API/Stripe-related design notes — the Checkout + webhook flow, dedup, and
  subscription sync.

Implementation was done via Claude Code, deliberately using **subagents** for
the actual coding work: subagents run in their own context, so a big
implementation pass (e.g. "read this design doc and build it") doesn't
consume the main session's context budget the way inline generation would.
I stayed in the loop as the reviewer/supervisor of what came back — I wasn't
handing off unsupervised.

## Build order (roughly matching git history)

1. **Scaffold + schema design** (`dcd0458`, `f69e704`, `18d5622`, `d7c7a50`) —
   base project structure, then the multi-tenant identity/authorization
   schema design doc, then the actual models.
2. **Phase 1 design + core billing** (`58a39f7`, `83247e6`, `1b01818`) — the
   first real implementation pass: billing/usage endpoints, the authz layer,
   and unit tests. This bundled what were meant to be separate phases (2–4)
   into one commit — a process mistake I corrected going forward by requiring
   each phase to land as its own commit (`cefda0b`).
3. **First evidence pass** (`fcbb233`) — proved the Phase 2 gates (idempotent
   duplicate requests, quota boundary at 429) against the early SQLite-backed
   setup.
4. **SQLite → Supabase migration** (`04ad7f2`) — the whole project was
   originally built on SQLite with a self-rolled bearer-token auth stand-in.
   I moved it to Supabase (Postgres + Supabase Auth) because I wanted a real
   cloud-based architecture instead of a local file DB and homegrown auth.
   This meant swapping `get_current_actor()` to verify real Supabase Auth JWTs
   instead of a fake token, while keeping the SQLAlchemy models/migrations
   layer wire-compatible.
5. **Evidence file relocated** (`aebc112`, `91ba6ac`) — moved `EVIDENCE.md`
   into `md/` alongside this buildlog.
6. **Dockerization** (`0513aac`) — added `compose.yaml` and a `Dockerfile` so
   the whole stack (app + schema bootstrap) comes up with one `docker compose
   up`, instead of requiring manual environment setup.
7. **Post-clone breakage and fixes** (`bfe037d` → `8c5fa56`) — when I cloned
   this repo fresh onto another machine to test it like a stranger would, it
   didn't run: the `DATABASE_URL` I'd been using locally didn't match what
   Claude had assumed/generated, and there was no way to bootstrap a tenant
   from scratch. I fixed the Supabase connection string mismatch directly and
   added a missing `POST /tenants` endpoint so a fresh clone could actually
   create its first tenant instead of assuming one already existed.
8. **Gap analysis against the PDF** — once the core was stable, I compared
   the PDF brief against the actual codebase end-to-end (this is where the
   subagent-based review process really paid off — the codebase had grown
   large enough that a single-pass read wasn't going to catch everything).
   This surfaced that `EVIDENCE.md` was effectively empty for the Stripe/
   webhook gate — Claude had written the *code*, but nobody had actually
   proven the Checkout → webhook → plan-flip flow against real Stripe test
   mode.
9. **Real Stripe verification, done by me, not simulated** — I ran the actual
   flow myself: started the app, ran `stripe listen` to forward real webhook
   events to my local server, completed a real Stripe test-mode Checkout
   session with the `4242 4242 4242 4242` test card, and watched the webhook
   flip the tenant from `free` to `pro` in the live database. I also forged
   an invalid webhook signature to confirm it's rejected with 400, and used
   `stripe events resend` to confirm a replayed event is deduplicated (one
   row in `stripe_events`, handler body doesn't re-run). I wrote these results
   into `EVIDENCE.md` myself as Proofs 1–4 for the Phase 3 gate.
10. **Closing the remaining PDF gaps** (`287cf2b`, `3de0782`) — after
    re-comparing against the PDF, what was left was: `README.md`, an
    architecture diagram, `capstone.yaml`, a background job, real Alembic
    migrations (the app was using `create_all()`), a seed script, and test
    coverage for the `customer.subscription.updated` webhook branch (the
    handler existed but had never been tested). I had Claude Code write design
    docs for each of these first, then a separate implementation pass build
    them against those docs, then a third independent pass verify everything
    by actually running it (Alembic against a real Postgres, the seed script
    against a running app, the full test suite) rather than trusting the
    implementer's own summary.

## Where AI was used vs. where I did the work myself

- **AI-drafted, human-edited**: design docs (schema, authz flow, API
  contracts), most of the FastAPI route/service/repository code, the Alembic
  wiring, the background reconciliation job, the seed script.
- **Done by me, not simulated by AI**: the actual live Stripe test-mode
  Checkout run, `stripe listen`, the forged-signature test, and the
  `stripe events resend` dedup check — these needed a real Stripe account,
  a real webhook secret, and a running server on my machine, which isn't
  something an AI session can do on its own. I captured the real output and
  wrote it into `EVIDENCE.md`.
- **Bugs I found and had fixed**: the Supabase connection string mismatch
  after cloning fresh, and the missing tenant-bootstrap endpoint — both only
  surfaced because I actually tried running the project as a stranger would,
  not just reading the code.
