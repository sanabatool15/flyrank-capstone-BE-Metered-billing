from src.auth.dependencies import TenantContext
from src.models.db_models import UsageEvent
from src.schemas import GenerateRequest
from src.services import meter_service
from tests.conftest import make_member_with_permissions, make_tenant


def _ctx(db, tenant):
    user, membership, _role = make_member_with_permissions(db, tenant, ["api.use"])
    return TenantContext(user=user, tenant_id=tenant.id, membership=membership)


def test_idempotent_same_key_returns_identical_row(db_session):
    tenant = make_tenant(db_session, plan="free")
    ctx = _ctx(db_session, tenant)
    body = GenerateRequest(usage_type="api_call")

    result1 = meter_service.record_usage(db_session, ctx, body, "key-123")
    result2 = meter_service.record_usage(db_session, ctx, body, "key-123")

    assert result1["usage_event_id"] == result2["usage_event_id"]
    assert result1["cost_cents"] == result2["cost_cents"]

    events = (
        db_session.query(UsageEvent)
        .filter(UsageEvent.tenant_id == tenant.id)
        .all()
    )
    assert len(events) == 1


def test_different_idempotency_key_inserts_second_event(db_session):
    tenant = make_tenant(db_session, plan="free")
    ctx = _ctx(db_session, tenant)
    body = GenerateRequest(usage_type="api_call")

    meter_service.record_usage(db_session, ctx, body, "key-A")
    meter_service.record_usage(db_session, ctx, body, "key-B")

    events = (
        db_session.query(UsageEvent)
        .filter(UsageEvent.tenant_id == tenant.id)
        .all()
    )
    assert len(events) == 2


def test_duplicate_request_does_not_recompute_cost(db_session):
    tenant = make_tenant(db_session, plan="free")
    ctx = _ctx(db_session, tenant)
    body = GenerateRequest(usage_type="ai_tokens", input_tokens=50_000)

    result1 = meter_service.record_usage(db_session, ctx, body, "same-key")
    # even if we could change pricing between calls, duplicate should just
    # re-read the persisted cost, not recompute
    result2 = meter_service.record_usage(db_session, ctx, body, "same-key")

    assert result1["cost_cents"] == 15
    assert result2["cost_cents"] == 15
