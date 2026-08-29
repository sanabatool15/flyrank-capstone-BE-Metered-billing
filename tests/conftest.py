"""Shared pytest fixtures: in-memory SQLite DB + FastAPI TestClient + factories."""
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.session import Base, get_db
from src.models.db_models import (
    Membership,
    Permission,
    Role,
    RolePermission,
    Tenant,
    User,
)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def db_session(engine):
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def app_client(db_session):
    from src.app import app

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def make_tenant(db, name="Acme", plan="free", stripe_customer_id=None):
    tenant = Tenant(id=str(uuid.uuid4()), name=name, plan=plan, stripe_customer_id=stripe_customer_id)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def make_user(db, email=None, auth_provider_id=None):
    email = email or f"{uuid.uuid4()}@example.com"
    auth_provider_id = auth_provider_id or str(uuid.uuid4())
    user = User(id=str(uuid.uuid4()), email=email, auth_provider_id=auth_provider_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_role(db, name, tenant_id=None):
    role = db.query(Role).filter(Role.tenant_id == tenant_id, Role.name == name).first()
    if role:
        return role
    role = Role(id=str(uuid.uuid4()), tenant_id=tenant_id, name=name)
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def make_permission(db, name):
    perm = db.query(Permission).filter(Permission.name == name).first()
    if perm:
        return perm
    perm = Permission(id=str(uuid.uuid4()), name=name)
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return perm


def grant_permission(db, role, permission):
    exists = (
        db.query(RolePermission)
        .filter(RolePermission.role_id == role.id, RolePermission.permission_id == permission.id)
        .first()
    )
    if exists:
        return
    db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    db.commit()


def make_membership(db, user, tenant, role):
    membership = Membership(
        id=str(uuid.uuid4()), user_id=user.id, tenant_id=tenant.id, role_id=role.id
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return membership


def make_member_with_permissions(db, tenant, permission_names, role_name="TestRole"):
    """Creates a User + Role + Permissions + Membership on `tenant`, all granted
    the given permission names. Returns (user, membership, role).
    """
    user = make_user(db)
    role = make_role(db, role_name, tenant_id=None)
    for pname in permission_names:
        perm = make_permission(db, pname)
        grant_permission(db, role, perm)
    membership = make_membership(db, user, tenant, role)
    return user, membership, role


def auth_header(user):
    return {"Authorization": f"Bearer {user.auth_provider_id}"}
