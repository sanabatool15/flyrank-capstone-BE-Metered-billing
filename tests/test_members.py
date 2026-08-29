import uuid

from src.models.db_models import Membership
from tests.conftest import (
    auth_header,
    make_member_with_permissions,
    make_role,
    make_tenant,
    make_user,
)


def test_invite_unknown_email_returns_404(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    admin, _m, _r = make_member_with_permissions(db_session, tenant, ["members.invite"])
    make_role(db_session, "member")  # global role must exist to resolve role name

    resp = app_client.post(
        f"/tenants/{tenant.id}/members",
        json={"email": "nobody@example.com", "role": "member"},
        headers=auth_header(admin),
    )
    assert resp.status_code == 404


def test_invite_already_existing_member_returns_409(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    admin, _m, _r = make_member_with_permissions(db_session, tenant, ["members.invite"])
    role = make_role(db_session, "member")

    target = make_user(db_session, email="existing@example.com")
    db_session.add(
        Membership(id=str(uuid.uuid4()), user_id=target.id, tenant_id=tenant.id, role_id=role.id)
    )
    db_session.commit()

    resp = app_client.post(
        f"/tenants/{tenant.id}/members",
        json={"email": "existing@example.com", "role": "member"},
        headers=auth_header(admin),
    )
    assert resp.status_code == 409


def test_successful_invite_creates_membership(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    admin, _m, _r = make_member_with_permissions(db_session, tenant, ["members.invite"])
    make_role(db_session, "member")

    target = make_user(db_session, email="newmember@example.com")

    resp = app_client.post(
        f"/tenants/{tenant.id}/members",
        json={"email": "newmember@example.com", "role": "member"},
        headers=auth_header(admin),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == target.id
    assert data["role"] == "member"

    membership = (
        db_session.query(Membership)
        .filter(Membership.user_id == target.id, Membership.tenant_id == tenant.id)
        .first()
    )
    assert membership is not None
