from src.models.db_models import AuditLog
from tests.conftest import (
    auth_header,
    make_member_with_permissions,
    make_membership,
    make_permission,
    make_role,
    make_tenant,
    make_user,
)


def test_no_token_returns_401(app_client, db_session):
    tenant = make_tenant(db_session)
    resp = app_client.get(f"/tenants/{tenant.id}/usage")
    assert resp.status_code == 401


def test_valid_token_no_membership_returns_403(app_client, db_session):
    tenant = make_tenant(db_session)
    user = make_user(db_session)
    resp = app_client.get(f"/tenants/{tenant.id}/usage", headers=auth_header(user))
    assert resp.status_code == 403

    log = (
        db_session.query(AuditLog)
        .filter(AuditLog.tenant_id == tenant.id, AuditLog.actor_user_id == user.id)
        .first()
    )
    assert log is not None
    assert log.decision == "denied"
    assert log.reason == "not a member"


def test_member_without_permission_returns_403(app_client, db_session):
    tenant = make_tenant(db_session)
    user = make_user(db_session)
    role = make_role(db_session, "NoPerms")
    make_membership(db_session, user, tenant, role)

    resp = app_client.get(f"/tenants/{tenant.id}/usage", headers=auth_header(user))
    assert resp.status_code == 403

    log = (
        db_session.query(AuditLog)
        .filter(AuditLog.tenant_id == tenant.id, AuditLog.actor_user_id == user.id)
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert log.decision == "denied"
    assert log.reason == "missing permission: usage.read"


def test_member_with_permission_passes(app_client, db_session):
    tenant = make_tenant(db_session)
    user, membership, role = make_member_with_permissions(db_session, tenant, ["usage.read"])

    resp = app_client.get(f"/tenants/{tenant.id}/usage", headers=auth_header(user))
    assert resp.status_code == 200


def test_unknown_tenant_returns_404(app_client, db_session):
    user = make_user(db_session)
    resp = app_client.get("/tenants/does-not-exist/usage", headers=auth_header(user))
    assert resp.status_code == 404


def test_cross_tenant_isolation(db_session):
    """A usage query for tenant A must never return tenant B's rows."""
    from src.repositories import usage_repository

    tenant_a = make_tenant(db_session, name="A")
    tenant_b = make_tenant(db_session, name="B")

    usage_repository.insert_usage_event(
        db_session,
        tenant_id=tenant_a.id,
        idempotency_key="k1",
        usage_type="api_call",
        quantity=5,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_cents=5,
    )
    usage_repository.insert_usage_event(
        db_session,
        tenant_id=tenant_b.id,
        idempotency_key="k1",  # same key, different tenant -- must not collide
        usage_type="api_call",
        quantity=9,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cost_cents=9,
    )
    db_session.commit()

    from datetime import datetime, timedelta, timezone

    start = datetime.now(timezone.utc) - timedelta(days=1)
    end = datetime.now(timezone.utc) + timedelta(days=1)

    totals_a = usage_repository.sum_usage_for_period(db_session, tenant_a.id, start, end)
    totals_b = usage_repository.sum_usage_for_period(db_session, tenant_b.id, start, end)

    assert totals_a["api_calls"] == 5
    assert totals_b["api_calls"] == 9

    event_a = usage_repository.get_usage_event_by_idempotency_key(db_session, tenant_a.id, "k1")
    event_b = usage_repository.get_usage_event_by_idempotency_key(db_session, tenant_b.id, "k1")
    assert event_a.tenant_id == tenant_a.id
    assert event_b.tenant_id == tenant_b.id
    assert event_a.id != event_b.id
