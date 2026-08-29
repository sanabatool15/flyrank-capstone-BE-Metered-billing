"""Stripe integration service: checkout session creation and webhook handling.

Stripe test mode only — see CLAUDE.md. No plan/tenant mutation happens in the
checkout path; only the verified webhook mutates billing state, per
API_CONTRACTS.md.
"""
import os

import stripe
from sqlalchemy.orm import Session

from src.models.db_models import Tenant
from src.repositories import stripe_repository, subscription_repository, tenant_repository

try:
    from src.config import get_settings

    _settings = get_settings()
    STRIPE_SECRET_KEY = _settings.STRIPE_SECRET_KEY
    STRIPE_WEBHOOK_SECRET = _settings.STRIPE_WEBHOOK_SECRET
    STRIPE_PRICE_ID_PRO = _settings.STRIPE_PRICE_ID_PRO
except Exception:
    # Fallback if src/config.py isn't present/importable yet when this file
    # is loaded — read directly from the environment instead. Noted in the
    # final report; reconcile with src/config.py once it's stable.
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_ID_PRO = os.environ.get("STRIPE_PRICE_ID_PRO", "")

stripe.api_key = STRIPE_SECRET_KEY


def create_checkout_session(db: Session, tenant: Tenant, target_plan: str) -> str:
    """Create a Stripe Checkout Session (test mode) for upgrading to `target_plan`.

    Creates/reuses `stripe_customer_id` on the Tenant. Does not mutate
    tenant.plan or create a Subscription row — that only happens via the
    verified webhook, per API_CONTRACTS.md.
    """
    if target_plan != "pro":
        raise ValueError(f"unsupported target_plan: {target_plan}")

    stripe_customer_id = tenant.stripe_customer_id
    if not stripe_customer_id:
        customer = stripe.Customer.create(
            name=tenant.name,
            metadata={"tenant_id": tenant.id},
        )
        stripe_customer_id = customer["id"]
        tenant_repository.update_tenant_stripe_customer_id(
            db, tenant.id, stripe_customer_id
        )
        db.commit()

    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=stripe_customer_id,
        line_items=[{"price": STRIPE_PRICE_ID_PRO, "quantity": 1}],
        success_url=os.environ.get(
            "STRIPE_CHECKOUT_SUCCESS_URL", "https://example.com/billing/success"
        ),
        cancel_url=os.environ.get(
            "STRIPE_CHECKOUT_CANCEL_URL", "https://example.com/billing/cancel"
        ),
        metadata={"tenant_id": tenant.id},
    )
    return session["url"]


def verify_and_construct_event(payload: bytes, sig_header: str) -> "stripe.Event":
    """Verify the Stripe-Signature header and construct the Event object.

    Raises stripe.error.SignatureVerificationError (or ValueError on a
    malformed payload) on failure — caller maps that to 400.
    """
    return stripe.Webhook.construct_event(
        payload, sig_header, STRIPE_WEBHOOK_SECRET
    )


def handle_webhook_event(db: Session, event) -> None:
    """Dedup + dispatch a verified Stripe event, per API_CONTRACTS.md steps 3-6."""
    event_id = event["id"]
    event_type = event["type"]

    inserted = stripe_repository.try_insert_stripe_event(db, event_id, event_type)
    if not inserted:
        # Already processed (duplicate delivery or race) — nothing else to do.
        return

    data_object = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(db, data_object)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(db, data_object)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(db, data_object)
    # Unrecognized event types: dedup row is already recorded; nothing to dispatch.

    db.commit()


def _handle_checkout_completed(db: Session, session_obj) -> None:
    stripe_customer_id = session_obj.get("customer")
    tenant = tenant_repository.get_tenant_by_stripe_customer_id(db, stripe_customer_id)
    if tenant is None:
        # No matching tenant — nothing we can safely mutate. Event stays
        # deduped; Stripe won't retry since we return 200 either way.
        return

    tenant_repository.update_tenant_plan(db, tenant.id, "pro")

    stripe_subscription_id = session_obj.get("subscription")
    if stripe_subscription_id:
        subscription_repository.upsert_subscription(
            db,
            tenant_id=tenant.id,
            stripe_subscription_id=stripe_subscription_id,
            status="active",
            plan="pro",
            current_period_end=None,
        )


def _handle_subscription_updated(db: Session, sub_obj) -> None:
    from datetime import datetime, timezone

    stripe_subscription_id = sub_obj.get("id")
    stripe_customer_id = sub_obj.get("customer")
    status = sub_obj.get("status", "active")
    current_period_end_ts = sub_obj.get("current_period_end")
    current_period_end = (
        datetime.fromtimestamp(current_period_end_ts, tz=timezone.utc)
        if current_period_end_ts
        else None
    )

    existing = subscription_repository.get_subscription_by_stripe_id(
        db, stripe_subscription_id
    )
    if existing is not None:
        tenant_id = existing.tenant_id
    else:
        tenant = tenant_repository.get_tenant_by_stripe_customer_id(
            db, stripe_customer_id
        )
        if tenant is None:
            return
        tenant_id = tenant.id

    # Map Stripe status -> our plan/status vocabulary. "canceled" plan
    # rollback is handled by the dedicated deleted-subscription event.
    plan = "pro" if status in ("active", "past_due", "trialing") else "free"
    sub_status = status if status in ("active", "past_due", "canceled") else "active"

    subscription_repository.upsert_subscription(
        db,
        tenant_id=tenant_id,
        stripe_subscription_id=stripe_subscription_id,
        status=sub_status,
        plan=plan,
        current_period_end=current_period_end,
    )


def _handle_subscription_deleted(db: Session, sub_obj) -> None:
    stripe_subscription_id = sub_obj.get("id")
    existing = subscription_repository.get_subscription_by_stripe_id(
        db, stripe_subscription_id
    )
    if existing is None:
        return

    subscription_repository.upsert_subscription(
        db,
        tenant_id=existing.tenant_id,
        stripe_subscription_id=stripe_subscription_id,
        status="canceled",
        plan=existing.plan,
        current_period_end=existing.current_period_end,
    )
    tenant_repository.update_tenant_plan(db, existing.tenant_id, "free")
