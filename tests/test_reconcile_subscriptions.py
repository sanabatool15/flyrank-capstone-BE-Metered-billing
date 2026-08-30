"""Unit tests for src/jobs/reconcile_subscriptions.py.

Mocks stripe.Subscription.list the same way tests/test_webhooks.py mocks
stripe.Webhook.construct_event.
"""
from unittest.mock import patch

import stripe
import pytest

from src.jobs import reconcile_subscriptions as job
from src.models.db_models import AuditLog
from src.repositories import subscription_repository
from tests.conftest import make_tenant


def _stripe_sub(sub_id, status, current_period_end=None):
    return {"id": sub_id, "status": status, "current_period_end": current_period_end}


def test_no_drift_zero_corrections(db_session):
    tenant = make_tenant(db_session, plan="pro", stripe_customer_id="cus_ok")
    subscription_repository.upsert_subscription(
        db_session,
        tenant_id=tenant.id,
        stripe_subscription_id="sub_ok_1",
        status="active",
        plan="pro",
        current_period_end=None,
    )
    db_session.commit()

    with patch(
        "src.jobs.reconcile_subscriptions.stripe.Subscription.list",
        return_value={"data": [_stripe_sub("sub_ok_1", "active")]},
    ):
        summary = job.reconcile_all(db_session)

    assert summary.checked == 1
    assert summary.corrected == 0
    assert summary.failed == 0
    assert db_session.query(AuditLog).count() == 0


def test_local_past_due_stripe_now_active_corrects_and_audits(db_session):
    tenant = make_tenant(db_session, plan="pro", stripe_customer_id="cus_pd")
    subscription_repository.upsert_subscription(
        db_session,
        tenant_id=tenant.id,
        stripe_subscription_id="sub_pd_1",
        status="past_due",
        plan="pro",
        current_period_end=None,
    )
    db_session.commit()

    with patch(
        "src.jobs.reconcile_subscriptions.stripe.Subscription.list",
        return_value={"data": [_stripe_sub("sub_pd_1", "active")]},
    ):
        summary = job.reconcile_all(db_session)

    assert summary.checked == 1
    assert summary.corrected == 1
    assert summary.failed == 0

    sub = subscription_repository.get_subscription_by_stripe_id(db_session, "sub_pd_1")
    assert sub.status == "active"
    assert sub.plan == "pro"

    logs = db_session.query(AuditLog).filter(AuditLog.action == "subscription.reconciled").all()
    assert len(logs) == 1
    assert logs[0].resource_id == "sub_pd_1"
    assert logs[0].decision == "allowed"


def test_transient_error_then_success_on_third_attempt(db_session):
    tenant = make_tenant(db_session, plan="free", stripe_customer_id="cus_retry")

    call_count = {"n": 0}

    def flaky_list(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise stripe.error.APIConnectionError("network blip")
        return {"data": [_stripe_sub("sub_retry_1", "active")]}

    with patch(
        "src.jobs.reconcile_subscriptions.stripe.Subscription.list",
        side_effect=flaky_list,
    ), patch("src.jobs.reconcile_subscriptions.time.sleep", return_value=None):
        summary = job.reconcile_all(db_session)

    assert call_count["n"] == 3
    assert summary.failed == 0
    assert summary.corrected == 1
    assert db_session.query(AuditLog).filter(AuditLog.action.like("job.%failed")).count() == 0


def test_exhausts_retries_marks_failed_and_alerts(db_session):
    make_tenant(db_session, plan="free", stripe_customer_id="cus_dead")

    with patch(
        "src.jobs.reconcile_subscriptions.stripe.Subscription.list",
        side_effect=stripe.error.APIConnectionError("down"),
    ), patch("src.jobs.reconcile_subscriptions.time.sleep", return_value=None):
        summary = job.reconcile_all(db_session)

    assert summary.failed == 1
    assert summary.corrected == 0
    assert len(summary.errors) == 1

    # run() (not reconcile_all directly) is what writes the failure AuditLog
    # row -- exercise that path too, reusing the same DB session/mocks.
    with patch("src.jobs.reconcile_subscriptions.SessionLocal", return_value=db_session), patch(
        "src.jobs.reconcile_subscriptions.stripe.Subscription.list",
        side_effect=stripe.error.APIConnectionError("down"),
    ), patch("src.jobs.reconcile_subscriptions.time.sleep", return_value=None), patch.object(
        db_session, "close", lambda: None
    ):
        result = job.run()

    assert result.failed == 1
    alert_logs = db_session.query(AuditLog).filter(
        AuditLog.action == "job.reconcile_subscriptions.failed"
    ).all()
    assert len(alert_logs) == 1
    assert "failed reconciliation" in alert_logs[0].reason


def test_stripe_has_no_subscription_reverts_to_canceled_and_free(db_session):
    tenant = make_tenant(db_session, plan="pro", stripe_customer_id="cus_gone")
    subscription_repository.upsert_subscription(
        db_session,
        tenant_id=tenant.id,
        stripe_subscription_id="sub_gone_1",
        status="active",
        plan="pro",
        current_period_end=None,
    )
    db_session.commit()

    with patch(
        "src.jobs.reconcile_subscriptions.stripe.Subscription.list",
        return_value={"data": []},
    ):
        summary = job.reconcile_all(db_session)

    assert summary.corrected == 1
    sub = subscription_repository.get_subscription_by_stripe_id(db_session, "sub_gone_1")
    assert sub.status == "canceled"
    db_session.refresh(tenant)
    assert tenant.plan == "free"


def test_non_retryable_error_fails_immediately_without_retry(db_session):
    make_tenant(db_session, plan="free", stripe_customer_id="cus_bad")

    call_count = {"n": 0}

    def bad_request(*args, **kwargs):
        call_count["n"] += 1
        raise stripe.error.InvalidRequestError("bad", param=None)

    with patch(
        "src.jobs.reconcile_subscriptions.stripe.Subscription.list",
        side_effect=bad_request,
    ):
        summary = job.reconcile_all(db_session)

    assert call_count["n"] == 1
    assert summary.failed == 1
