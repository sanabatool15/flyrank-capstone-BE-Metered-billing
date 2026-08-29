"""Evidence-generating tests for Phase 2 gates (idempotency + quota boundary)
and the past_due -> 402 gate. Run with:

    pytest tests/test_evidence_gates.py -v -s

These reuse the existing app_client/db_session fixtures and factories from
tests/conftest.py. See EVIDENCE.md at repo root for the captured output.
"""
import uuid

from sqlalchemy import func

from src.models.db_models import Subscription, UsageEvent
from tests.conftest import auth_header, make_member_with_permissions, make_tenant


def test_gate_a_idempotent_duplicate_creates_one_event(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    user, _m, _r = make_member_with_permissions(db_session, tenant, ["api.use"])
    key = "gate-a-idem-key"

    resp1 = app_client.post(
        f"/tenants/{tenant.id}/generate",
        json={"usage_type": "api_call"},
        headers={**auth_header(user), "Idempotency-Key": key},
    )
    resp2 = app_client.post(
        f"/tenants/{tenant.id}/generate",
        json={"usage_type": "api_call"},
        headers={**auth_header(user), "Idempotency-Key": key},
    )

    print("GATE A resp1:", resp1.status_code, resp1.json())
    print("GATE A resp2:", resp2.status_code, resp2.json())

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["usage_event_id"] == resp2.json()["usage_event_id"]

    count = (
        db_session.query(func.count(UsageEvent.id))
        .filter(
            UsageEvent.tenant_id == tenant.id,
            UsageEvent.idempotency_key == key,
        )
        .scalar()
    )
    print("GATE A DB count WHERE idempotency_key =", key, "->", count)
    assert count == 1


def test_gate_b_quota_boundary_1000_ok_1001_rejected(app_client, db_session):
    tenant = make_tenant(db_session, plan="free")
    user, _m, _r = make_member_with_permissions(db_session, tenant, ["api.use"])

    # Seed 999 existing usage_events directly via the ORM (fast, no HTTP).
    for i in range(999):
        db_session.add(
            UsageEvent(
                id=str(uuid.uuid4()),
                tenant_id=tenant.id,
                idempotency_key=f"seed-{i}",
                usage_type="api_call",
                quantity=1,
                cost_cents=1,
            )
        )
    db_session.commit()

    pre_count = (
        db_session.query(func.count(UsageEvent.id))
        .filter(UsageEvent.tenant_id == tenant.id)
        .scalar()
    )
    print("GATE B pre-seeded usage_events count:", pre_count)
    assert pre_count == 999

    # Request #1000 (999 existing + this one == the 1000 limit) -> 200.
    resp_1000 = app_client.post(
        f"/tenants/{tenant.id}/generate",
        json={"usage_type": "api_call"},
        headers={**auth_header(user), "Idempotency-Key": "boundary-1000"},
    )
    print("GATE B request #1000:", resp_1000.status_code, resp_1000.json())
    assert resp_1000.status_code == 200
    assert resp_1000.json()["remaining_quota"]["api_calls"] == 0

    # Request #1001 (would push to 1001 > 1000 limit) -> 429.
    resp_1001 = app_client.post(
        f"/tenants/{tenant.id}/generate",
        json={"usage_type": "api_call"},
        headers={**auth_header(user), "Idempotency-Key": "boundary-1001"},
    )
    print("GATE B request #1001:", resp_1001.status_code, resp_1001.json())
    assert resp_1001.status_code == 429
    body = resp_1001.json()["detail"]
    assert body["error"] == "quota_exceeded"
    assert "1000/1000" in body["message"]


def test_gate_c_past_due_subscription_returns_402_regardless_of_quota(app_client, db_session):
    tenant = make_tenant(db_session, plan="pro")
    user, _m, _r = make_member_with_permissions(db_session, tenant, ["api.use"])

    db_session.add(
        Subscription(
            id=str(uuid.uuid4()),
            tenant_id=tenant.id,
            stripe_subscription_id=f"sub_{uuid.uuid4()}",
            status="past_due",
            plan="pro",
        )
    )
    db_session.commit()

    resp = app_client.post(
        f"/tenants/{tenant.id}/generate",
        json={"usage_type": "api_call"},
        headers={**auth_header(user), "Idempotency-Key": "past-due-check"},
    )
    print("GATE C past_due response:", resp.status_code, resp.json())
    assert resp.status_code == 402
    body = resp.json()["detail"]
    assert body["error"] == "payment_required"
    assert "past_due" in body["message"]
