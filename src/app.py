"""FastAPI application factory/instance."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from src.routers import auth as auth_router
from src.routers import checkout as checkout_router
from src.routers import members as members_router
from src.routers import tenants as tenants_router
from src.routers import usage as usage_router
from src.routers import webhooks as webhooks_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Import models first so every table is registered on Base.metadata,
    # then create any missing tables against the real engine bound in
    # src.db.session. create_all is idempotent — it only creates missing
    # tables, never drops/alters existing ones — so it's safe on every
    # startup. Skipped under pytest (PYTEST_CURRENT_TEST is set by pytest
    # for the duration of each test): the test suite builds its own
    # in-memory SQLite engine per-test via conftest.py fixtures and must
    # never touch the real DATABASE_URL / make network calls.
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        import src.models  # noqa: F401
        from src.db.session import Base, engine

        try:
            Base.metadata.create_all(bind=engine)
        except OperationalError as exc:
            raise RuntimeError(
                "Unable to connect to DATABASE_URL. Verify the Supabase "
                "project is active and copy the current connection string "
                "from Dashboard > Connect. If this environment is IPv4-only, "
                "use the Supavisor session pooler URL (port 5432) instead of "
                "the direct db.<project-ref>.supabase.co URL."
            ) from exc
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Usage Metering & Billing Engine", lifespan=lifespan)

    app.include_router(auth_router.router)
    app.include_router(tenants_router.router)
    app.include_router(usage_router.router)
    app.include_router(webhooks_router.router)
    app.include_router(checkout_router.router)
    app.include_router(members_router.router)

    return app


app = create_app()
