#!/usr/bin/env python
"""Seed demo data: a tenant, user, admin membership — so a stranger running
this project can immediately curl the API. Idempotent: safe to re-run.

Intended for local/demo/Supabase-dev projects only, not shared production
data — this script does not enforce that, it just documents the assumption
(see .claude/docs/SEED_SCRIPT_DESIGN.md's "Guardrails" section).

Assumes the schema already exists (either the app has booted at least once,
via its create_all() fallback, or `uv run alembic upgrade head` has been
run) — this script does not create tables itself.

Usage:
    uv run python scripts/seed.py
"""
import sys
import time

from src.config import get_settings
from src.db.session import SessionLocal
from src.repositories import membership_repository
from src.models.db_models import Tenant, User
from src.services.tenant_service import _ADMIN_PERMISSIONS, _ADMIN_ROLE_NAME

DEMO_USER_EMAIL = "demo@flyrank.local"
DEMO_AUTH_PROVIDER_ID = "seed-demo-user"
DEMO_TENANT_NAME = "Acme Demo"


def get_or_create_demo_user(db) -> User:
    user = (
        db.query(User)
        .filter(User.auth_provider_id == DEMO_AUTH_PROVIDER_ID)
        .first()
    )
    if user is not None:
        return user
    user = User(email=DEMO_USER_EMAIL, auth_provider_id=DEMO_AUTH_PROVIDER_ID)
    db.add(user)
    db.flush()
    return user


def get_or_create_demo_tenant(db) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.name == DEMO_TENANT_NAME).first()
    if tenant is not None:
        return tenant
    tenant = Tenant(name=DEMO_TENANT_NAME, plan="free")
    db.add(tenant)
    db.flush()
    return tenant


def ensure_demo_admin_role(db):
    """Same idempotent global-admin-role seeding tenant_service uses for
    every real POST /tenants — reused here rather than reimplementing role
    seeding a third time.
    """
    role = membership_repository.get_or_create_global_role(db, _ADMIN_ROLE_NAME)
    for pname in _ADMIN_PERMISSIONS:
        perm = membership_repository.get_or_create_permission(db, pname)
        membership_repository.grant_permission_if_missing(db, role.id, perm.id)
    return role


def ensure_demo_membership(db, user: User, tenant: Tenant) -> None:
    role = ensure_demo_admin_role(db)
    existing = membership_repository.get_membership(db, user.id, tenant.id)
    if existing is not None:
        return
    membership_repository.create_membership(
        db, tenant_id=tenant.id, user_id=user.id, role_id=role.id
    )


def mint_demo_token(auth_provider_id: str) -> str:
    """Mint an HS256 demo bearer token, signed with the same
    SUPABASE_JWT_SECRET the running app verifies against — mirrors
    tests/conftest.py's make_supabase_jwt claim shape.
    """
    import jwt as pyjwt

    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": auth_provider_id,
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + 60 * 60 * 24 * 7,  # 7 days
    }
    return pyjwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


def print_summary(user: User, tenant: Tenant) -> None:
    settings = get_settings()
    print("Seed complete.")
    print(f"  tenant_id: {tenant.id}")
    print(f"  user_id:   {user.id}")
    print(f"  role:      {_ADMIN_ROLE_NAME}")
    print()

    if settings.SUPABASE_JWT_SECRET:
        token = mint_demo_token(user.auth_provider_id)
        print("Demo bearer token (valid 7 days):")
        print(f"  {token}")
        print()
        print("Try it:")
        print(
            f'  curl -s http://localhost:8000/tenants/{tenant.id}/usage \\\n'
            f'    -H "Authorization: Bearer {token}"'
        )
    else:
        print(
            "SUPABASE_JWT_SECRET is not set, so a demo token cannot be minted "
            "locally (this project is configured for asymmetric/JWKS "
            "verification instead)."
        )
        print(
            "The demo User/Tenant/Membership rows above were still created. "
            "To get a real bearer token, create a Supabase Auth user with "
            f"email {DEMO_USER_EMAIL!r} (matching auth_provider_id "
            f"{DEMO_AUTH_PROVIDER_ID!r} to the row's `sub`) and obtain a "
            "token via:\n"
            "  POST {SUPABASE_URL}/auth/v1/token?grant_type=password"
        )


def main() -> int:
    db = SessionLocal()
    try:
        user = get_or_create_demo_user(db)
        tenant = get_or_create_demo_tenant(db)
        ensure_demo_membership(db, user, tenant)
        db.commit()
        db.refresh(user)
        db.refresh(tenant)
    finally:
        db.close()

    print_summary(user, tenant)
    return 0


if __name__ == "__main__":
    sys.exit(main())
