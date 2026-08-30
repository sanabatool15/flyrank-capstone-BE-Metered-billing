from unittest.mock import patch

from src.models.db_models import StripeEvent
from tests.conftest import make_tenant


def _stripe_event(event_id, event_type, data_object):
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": data_object},
    }


def _sub_updated_event(event_id, sub_id, customer_id, status, current_period_end=None):
    return _stripe_event(
        event_id,
        "customer.subscription.updated",
        {
            "id": sub_id,
            "customer": customer_id,
            "status": status,
            "current_period_end": current_period_end,
        },
    )


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


# ---------------------------------------------------------------------------
# customer.subscription.updated — see .claude/docs/WEBHOOK_TEST_PLAN.md
# ---------------------------------------------------------------------------


def test_subscription_updated_new_subscription_creates_row(app_client, db_session):
    import datetime

    from src.repositories import subscription_repository

    tenant = make_tenant(db_session, plan="free", stripe_customer_id="cus_new")
    future_ts = int(
        (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)).timestamp()
    )
    event = _sub_updated_event("evt_sub_new_1", "sub_new_1", "cus_new", "active", future_ts)

    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200

    sub = subscription_repository.get_subscription_by_stripe_id(db_session, "sub_new_1")
    assert sub is not None
    assert sub.status == "active"
    assert sub.plan == "pro"
    expected_period_end = datetime.datetime.fromtimestamp(future_ts, tz=datetime.timezone.utc)
    actual_period_end = sub.current_period_end
    if actual_period_end.tzinfo is None:
        expected_period_end = expected_period_end.replace(tzinfo=None)
    assert actual_period_end == expected_period_end

    db_session.refresh(tenant)
    # _handle_subscription_updated does NOT touch Tenant.plan (only
    # checkout.session.completed / subscription.deleted do) — real behavior,
    # locked in here per WEBHOOK_TEST_PLAN.md.
    assert tenant.plan == "free"


def test_subscription_updated_active_to_past_due(app_client, db_session):
    from src.repositories import subscription_repository

    tenant = make_tenant(db_session, plan="pro", stripe_customer_id="cus_pd")
    subscription_repository.upsert_subscription(
        db_session,
        tenant_id=tenant.id,
        stripe_subscription_id="sub_pd_1",
        status="active",
        plan="pro",
    )
    db_session.commit()

    event = _sub_updated_event("evt_sub_pd_1", "sub_pd_1", "cus_pd", "past_due")
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200

    sub = subscription_repository.get_subscription_by_stripe_id(db_session, "sub_pd_1")
    assert sub.status == "past_due"
    assert sub.plan == "pro"


def test_subscription_updated_past_due_to_active(app_client, db_session):
    from src.repositories import subscription_repository

    tenant = make_tenant(db_session, plan="pro", stripe_customer_id="cus_recover")
    subscription_repository.upsert_subscription(
        db_session,
        tenant_id=tenant.id,
        stripe_subscription_id="sub_recover_1",
        status="past_due",
        plan="pro",
    )
    db_session.commit()

    event = _sub_updated_event("evt_sub_recover_1", "sub_recover_1", "cus_recover", "active")
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200

    sub = subscription_repository.get_subscription_by_stripe_id(db_session, "sub_recover_1")
    assert sub.status == "active"
    assert sub.plan == "pro"


def test_subscription_updated_trialing_to_active(app_client, db_session):
    from src.repositories import subscription_repository

    tenant = make_tenant(db_session, plan="free", stripe_customer_id="cus_trial")
    subscription_repository.upsert_subscription(
        db_session,
        tenant_id=tenant.id,
        stripe_subscription_id="sub_trial_1",
        status="active",
        plan="free",
    )
    db_session.commit()

    trial_event = _sub_updated_event("evt_sub_trial_1", "sub_trial_1", "cus_trial", "trialing")
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=trial_event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200

    sub = subscription_repository.get_subscription_by_stripe_id(db_session, "sub_trial_1")
    # "trialing" maps to plan=pro, but the local sub_status enum has no
    # "trialing" value — the else-branch of the second mapping silently
    # falls back to "active". Asserted explicitly so a future refactor
    # can't silently break it.
    assert sub.plan == "pro"
    assert sub.status == "active"

    active_event = _sub_updated_event("evt_sub_trial_2", "sub_trial_1", "cus_trial", "active")
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=active_event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200

    db_session.refresh(sub)
    assert sub.status == "active"
    assert sub.plan == "pro"


def test_subscription_updated_unrecognized_status_falls_back_to_free_and_active(
    app_client, db_session
):
    from src.repositories import subscription_repository

    tenant = make_tenant(db_session, plan="pro", stripe_customer_id="cus_weird")
    subscription_repository.upsert_subscription(
        db_session,
        tenant_id=tenant.id,
        stripe_subscription_id="sub_weird_1",
        status="active",
        plan="pro",
    )
    db_session.commit()

    event = _sub_updated_event(
        "evt_sub_weird_1", "sub_weird_1", "cus_weird", "incomplete_expired"
    )
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200

    sub = subscription_repository.get_subscription_by_stripe_id(db_session, "sub_weird_1")
    assert sub.plan == "free"
    # NOTE: falling back to "active" here for an unrecognized status is
    # arguably a bug (an "incomplete_expired" subscription reads as active,
    # not canceled) — flagged as a follow-up, not fixed here per
    # WEBHOOK_TEST_PLAN.md's scope (test current behavior, don't silently
    # change it).
    assert sub.status == "active"


def test_subscription_updated_plan_change_price_id_irrelevant(app_client, db_session):
    import datetime

    from src.repositories import subscription_repository

    old_ts = int(
        (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).timestamp()
    )
    new_ts = int(
        (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=29)).timestamp()
    )
    old_period_end = datetime.datetime.fromtimestamp(old_ts, tz=datetime.timezone.utc)

    tenant = make_tenant(db_session, plan="pro", stripe_customer_id="cus_planchange")
    subscription_repository.upsert_subscription(
        db_session,
        tenant_id=tenant.id,
        stripe_subscription_id="sub_planchange_1",
        status="active",
        plan="pro",
        current_period_end=old_period_end,
    )
    db_session.commit()

    event = _sub_updated_event(
        "evt_sub_planchange_1", "sub_planchange_1", "cus_planchange", "active", new_ts
    )
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200

    sub = subscription_repository.get_subscription_by_stripe_id(db_session, "sub_planchange_1")
    assert sub.plan == "pro"
    expected_new_period_end = datetime.datetime.fromtimestamp(new_ts, tz=datetime.timezone.utc)
    if sub.current_period_end.tzinfo is None:
        expected_new_period_end = expected_new_period_end.replace(tzinfo=None)
    assert sub.current_period_end == expected_new_period_end
    assert sub.current_period_end != old_period_end


def test_subscription_updated_missing_current_period_end(app_client, db_session):
    from src.repositories import subscription_repository

    tenant = make_tenant(db_session, plan="pro", stripe_customer_id="cus_noperiod")
    event = _sub_updated_event(
        "evt_sub_noperiod_1", "sub_noperiod_1", "cus_noperiod", "active", None
    )
    with patch(
        "src.services.stripe_service.stripe.Webhook.construct_event", return_value=event
    ):
        resp = app_client.post(
            "/webhooks/stripe", content=b"{}", headers={"stripe-signature": "sig"}
        )
    assert resp.status_code == 200

    sub = subscription_repository.get_subscription_by_stripe_id(db_session, "sub_noperiod_1")
    assert sub is not None
    assert sub.current_period_end is None
    assert sub.status == "active"
    assert sub.plan == "pro"
