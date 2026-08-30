# Capstone Manifest Design — `capstone.yaml`

The PDF spec's automated evaluator expects a machine-readable manifest at
repo root (`./capstone.yaml`) describing how to run, seed, and test this
project, and where to reach it once running. This doc specifies exactly what
that file must contain for this FastAPI + uv + pytest project, so it can be
authored without further research.

## File location

`capstone.yaml` at repo root, alongside `pyproject.toml`, `Dockerfile`,
`compose.yaml`.

## Required top-level keys

```yaml
name: flyrank-capstone-be-metered-billing
description: Usage Metering & Billing Engine (FastAPI + Supabase + Stripe test mode)

run:
  # Preferred: docker compose (matches Dockerfile/compose.yaml already in repo)
  command: docker compose up --build
  # Fallback for evaluators without Docker: uv-based local run
  local_command: uv run uvicorn src.main:app --host 0.0.0.0 --port 8000
  port: 8000
  health_check: GET /docs   # FastAPI's auto-generated OpenAPI UI; 200 = app is up
  env_file: .env            # must exist, copied from .env.example — see README

base_url: http://localhost:8000

seed:
  command: uv run python scripts/seed.py
  description: >
    Creates a demo tenant ("Acme Demo"), a demo user + admin membership,
    and prints a ready-to-use bearer token + tenant_id for curl-ing the API.
    Idempotent — safe to re-run (see SEED_SCRIPT_DESIGN.md).

test:
  command: uv run pytest
  coverage_command: uv run pytest --cov=src --cov-report=term-missing

migrate:
  command: uv run alembic upgrade head
  description: >
    Applies Alembic migrations against DATABASE_URL. Not required for the
    app to boot (src/app.py's lifespan still runs create_all as a fallback
    for local/sqlite convenience) but is the source of truth for schema
    changes going forward — see MIGRATIONS_DESIGN.md.

background_job:
  description: >
    Subscription reconciliation job — reconciles local Subscription/Tenant
    plan state against Stripe's source of truth to catch missed/late
    webhooks. See BACKGROUND_JOBS_DESIGN.md.
  command: uv run python -m src.jobs.reconcile_subscriptions
  schedule: "0 */6 * * *"   # every 6 hours; also runnable on demand

docs:
  readme: README.md
  architecture: README.md#architecture
  design_docs_dir: .claude/docs/

stack:
  language: python
  python_version: ">=3.11"
  framework: fastapi
  package_manager: uv
  db: postgresql (Supabase)
  test_framework: pytest
  payments: stripe (test mode)

evidence:
  file: md/EVIDENCE.MD
```

## Field-by-field rationale

- **`run.command` vs `run.local_command`**: the repo already ships a
  `Dockerfile` + `compose.yaml`, so `docker compose up --build` is the
  zero-setup path an evaluator or stranger should use. `local_command` is a
  fallback for environments without Docker (uv is already the project's
  dependency manager per `pyproject.toml`).
- **`health_check: GET /docs`**: FastAPI serves interactive Swagger docs at
  `/docs` with no auth required — a reliable "is the app up" probe without
  needing a bearer token, unlike every real endpoint in `API_CONTRACTS.md`.
- **`base_url`**: matches the port exposed by both `Dockerfile` (`EXPOSE
  8000`) and `compose.yaml` (`"8000:8000"`).
- **`seed.command`**: points at the new `scripts/seed.py` — see
  `SEED_SCRIPT_DESIGN.md` for its exact behavior and output format.
- **`test.command`**: `uv run pytest` picks up `[tool.pytest.ini_options]
  testpaths = ["tests"]` already configured in `pyproject.toml`; no extra
  flags needed. `coverage_command` is optional/secondary since `pytest-cov`
  is already a dev dependency.
- **`migrate.command`**: only meaningful once `MIGRATIONS_DESIGN.md` is
  implemented; documented here now so the manifest doesn't need a second
  edit later. Marked non-blocking because `create_all()` in `src/app.py`'s
  lifespan still works for local/dev/test convenience.
- **`background_job`**: names the one background job this project ships
  (see `BACKGROUND_JOBS_DESIGN.md`) so the evaluator's "≥1 background job"
  checklist item can be verified by inspecting this file plus
  `src/jobs/reconcile_subscriptions.py`.
- **`evidence.file`**: cross-references the existing `md/EVIDENCE.MD`
  convention already used by this repo (see CLAUDE.md Phase 4 gate).

## Validation checklist for whoever implements this

- [ ] File parses as valid YAML (no tabs, correct indentation).
- [ ] Every `command`/`local_command`/`test.command`/`seed.command` is a
      literal, copy-pasteable shell command — no placeholders like `<...>`.
- [ ] `base_url` + `run.port` agree with `Dockerfile`/`compose.yaml`.
- [ ] `seed.command` only works after `scripts/seed.py` exists
      (SEED_SCRIPT_DESIGN.md) — land both in the same commit/PR.
- [ ] `migrate.command` only works after Alembic is wired up
      (MIGRATIONS_DESIGN.md) — land both in the same commit/PR.
