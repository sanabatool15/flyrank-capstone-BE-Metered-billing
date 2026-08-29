import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from src.models.db_models import Subscription
from src.services import quota_service
from tests.conftest import make_tenant


def test_free_plan_limits():
    assert quota_service.PLAN_LIMITS["free"] == {"api_calls": 1_000, "ai_tokens": 100_000}


def test_pro_plan_limits():
    assert quota_service.PLAN_LIMITS["pro"] == {"api_calls": 50_000, "ai_tokens": 5_000_000}


def test_under_quota_allowed(db_session):
    tenant = make_tenant(db_session, plan="free")
    result = quota_service.check_quota(
        db_session, tenant.id, "free", "api_call", request_quantity=1, request_tokens=0
    )
    assert result["used_api_calls"] == 0


def test_at_quota_rejected_429(db_session):
    tenant = make_tenant(db_session, plan="free")
    with pytest.raises(HTTPException) as exc_info:
        quota_service.check_quota(
            db_session, tenant.id, "free", "api_call", request_quantity=1001, request_tokens=0
        )
    assert exc_info.value.status_code == 429


def test_exactly_at_limit_allowed_boundary(db_session):
    tenant = make_tenant(db_session, plan="free")
    # exactly at the limit (not over) should be allowed
    result = quota_service.check_quota(
        db_session, tenant.id, "free", "api_call", request_quantity=1000, request_tokens=0
    )
    assert result is not None


def test_over_ai_token_quota_rejected_429(db_session):
    tenant = make_tenant(db_session, plan="free")
    with pytest.raises(HTTPException) as exc_info:
        quota_service.check_quota(
            db_session, tenant.id, "free", "ai_tokens", request_quantity=0, request_tokens=100_001
        )
    assert exc_info.value.status_code == 429


def test_pro_plan_higher_limits(db_session):
    tenant = make_tenant(db_session, plan="pro")
    # this would fail for free plan but should pass for pro
    result = quota_service.check_quota(
        db_session, tenant.id, "pro", "ai_tokens", request_quantity=0, request_tokens=200_000
    )
    assert result is not None


def _make_subscription(db, tenant_id, status, plan="pro"):
    sub = Subscription(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        stripe_subscription_id=f"sub_{uuid.uuid4()}",
        status=status,
        plan=plan,
        current_period_end=None,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(sub)
    db.commit()
    return sub


def test_subscription_past_due_returns_402(db_session):
    tenant = make_tenant(db_session, plan="pro")
    _make_subscription(db_session, tenant.id, status="past_due")
    with pytest.raises(HTTPException) as exc_info:
        quota_service.check_subscription_status(db_session, tenant.id, "pro")
    assert exc_info.value.status_code == 402


def test_subscription_canceled_returns_402(db_session):
    tenant = make_tenant(db_session, plan="pro")
    _make_subscription(db_session, tenant.id, status="canceled")
    with pytest.raises(HTTPException) as exc_info:
        quota_service.check_subscription_status(db_session, tenant.id, "pro")
    assert exc_info.value.status_code == 402


def test_subscription_active_no_error(db_session):
    tenant = make_tenant(db_session, plan="pro")
    _make_subscription(db_session, tenant.id, status="active")
    # should not raise
    quota_service.check_subscription_status(db_session, tenant.id, "pro")


def test_free_plan_never_gated_by_subscription_status(db_session):
    tenant = make_tenant(db_session, plan="free")
    # no subscription row at all, plan == "free" -> not gated
    quota_service.check_subscription_status(db_session, tenant.id, "free")
