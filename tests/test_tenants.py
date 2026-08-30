"""Tests for POST /tenants — see .claude/docs/TENANT_CREATION.md."""
from tests.conftest import auth_header, make_user


def test_create_tenant_makes_caller_admin(app_client, db_session):
    user = make_user(db_session)

    resp = app_client.post(
        "/tenants",
        json={"name": "Acme Inc"},
        headers=auth_header(user),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Acme Inc"
    assert data["plan"] == "free"
    assert data["membership"]["role"] == "admin"
    assert data["tenant_id"]


def test_create_tenant_forces_plan_free_even_if_pro_requested(app_client, db_session):
    user = make_user(db_session)

    resp = app_client.post(
        "/tenants",
        json={"name": "Acme Inc", "plan": "pro"},
        headers=auth_header(user),
    )
    assert resp.status_code == 201
    assert resp.json()["plan"] == "free"


def test_create_tenant_requires_auth(app_client, db_session):
    resp = app_client.post("/tenants", json={"name": "Acme Inc"})
    assert resp.status_code == 401


def test_create_tenant_grants_working_membership_end_to_end(app_client, db_session):
    user = make_user(db_session)

    create_resp = app_client.post(
        "/tenants",
        json={"name": "Acme Inc"},
        headers=auth_header(user),
    )
    tenant_id = create_resp.json()["tenant_id"]

    usage_resp = app_client.get(
        f"/tenants/{tenant_id}/usage",
        headers=auth_header(user),
    )
    assert usage_resp.status_code == 200


def test_zero_membership_user_gets_403_not_401_on_other_tenant(app_client, db_session):
    owner = make_user(db_session)
    outsider = make_user(db_session)

    create_resp = app_client.post(
        "/tenants",
        json={"name": "Acme Inc"},
        headers=auth_header(owner),
    )
    tenant_id = create_resp.json()["tenant_id"]

    for method, path, body in [
        ("get", f"/tenants/{tenant_id}/usage", None),
        (
            "post",
            f"/tenants/{tenant_id}/generate",
            {"usage_type": "api_call"},
        ),
        ("post", f"/tenants/{tenant_id}/checkout", {"target_plan": "pro"}),
        (
            "post",
            f"/tenants/{tenant_id}/members",
            {"email": "someone@example.com", "role": "member"},
        ),
    ]:
        kwargs = {"headers": auth_header(outsider)}
        if body is not None:
            kwargs["json"] = body
        if method == "post" and path.endswith("/generate"):
            kwargs["headers"] = {**kwargs["headers"], "Idempotency-Key": "test-key"}
        resp = getattr(app_client, method)(path, **kwargs)
        assert resp.status_code == 403, f"{method.upper()} {path} -> {resp.status_code}"
