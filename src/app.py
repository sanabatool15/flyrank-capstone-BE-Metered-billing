"""FastAPI application factory/instance."""
from fastapi import FastAPI

from src.routers import auth as auth_router
from src.routers import checkout as checkout_router
from src.routers import members as members_router
from src.routers import usage as usage_router
from src.routers import webhooks as webhooks_router


def create_app() -> FastAPI:
    app = FastAPI(title="Usage Metering & Billing Engine")

    app.include_router(auth_router.router)
    app.include_router(usage_router.router)
    app.include_router(webhooks_router.router)
    app.include_router(checkout_router.router)
    app.include_router(members_router.router)

    return app


app = create_app()
