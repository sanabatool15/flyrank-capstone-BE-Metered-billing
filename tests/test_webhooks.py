from unittest.mock import patch

from src.models.db_models import StripeEvent
from tests.conftest import make_tenant


def _stripe_event(event_id, event_type, data_object):
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": data_object},
    }


def test_invalid_signature_returns_400_no_stripe_event_row(app_client, db_session):
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event",
        side_effect=ValueError("bad payload"),
    ):
        resp = app_client.post(
            "/webhooks/stripe",
            content=b"{}",
            headers={"stripe-signature": "bad"},
        )
    assert resp.status_code == 400
    assert db_session.query(StripeEvent).count() == 0


def test_duplicate_event_id_is_noop(app_client, db_session):
    tenant = make_tenant(db_session, plan="free", stripe_customer_id="cus_123")
    event = _stripe_event(
        "evt_dup_1", "checkout.session.completed", {"customer": "cus_123", "subscription": None}
    )

    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=event
    ):
        resp1 = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
        resp2 = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert db_session.query(StripeEvent).filter(StripeEvent.id == "evt_dup_1").count() == 1

    db_session.refresh(tenant)
    assert tenant.plan == "pro"


def test_checkout_session_completed_sets_plan_pro(app_client, db_session):
    tenant = make_tenant(db_session, plan="free", stripe_customer_id="cus_abc")
    event = _stripe_event(
        "evt_checkout_1",
        "checkout.session.completed",
        {"customer": "cus_abc", "subscription": "sub_xyz"},
    )
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200
    db_session.refresh(tenant)
    assert tenant.plan == "pro"


def test_subscription_deleted_reverts_plan_to_free(app_client, db_session):
    import uuid

    from src.repositories import subscription_repository

    tenant = make_tenant(db_session, plan="pro", stripe_customer_id="cus_del")
    subscription_repository.upsert_subscription(
        db_session,
        tenant_id=tenant.id,
        stripe_subscription_id="sub_del_1",
        status="active",
        plan="pro",
    )
    db_session.commit()

    event = _stripe_event(
        "evt_deleted_1", "customer.subscription.deleted", {"id": "sub_del_1"}
    )
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200
    db_session.refresh(tenant)
    assert tenant.plan == "free"
