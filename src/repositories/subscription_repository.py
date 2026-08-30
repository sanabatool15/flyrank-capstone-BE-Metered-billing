"""Repository for the `subscriptions` table.

Per AUTHZ_DESIGN.md's repository rule, every method that reads/writes a
tenant-owned row takes `tenant_id` as a required argument. The one exception
is `get_subscription_by_stripe_id`: at webhook-delivery time we only have
Stripe's `stripe_subscription_id`/`stripe_customer_id`, not our internal
tenant_id — that lookup is inherently cross-tenant (Stripe is the source of
truth for which subscription an event belongs to), so tenant scoping happens
*after* this lookup, not during it.
"""
from typing import Optional

from sqlalchemy.orm import Session

from src.models.db_models import Subscription


def get_subscription_by_stripe_id(
    db: Session, stripe_subscription_id: str
) -> Optional[Subscription]:
    """Cross-tenant lookup by Stripe's own subscription id — see module docstring."""
    return (
        db.query(Subscription)
        .filter(Subscription.stripe_subscription_id == stripe_subscription_id)
        .first()
    )


def get_by_tenant(db: Session, tenant_id: str) -> Optional[Subscription]:
    return (
        db.query(Subscription)
        .filter(Subscription.tenant_id == tenant_id)
        .first()
    )


def get_subscription_by_tenant_id(db: Session, tenant_id: str) -> Optional[Subscription]:
    """Alias of get_by_tenant with an explicit name — used by the
    reconciliation job (src/jobs/reconcile_subscriptions.py) when no
    Subscription row exists yet keyed by stripe_subscription_id.
    """
    return get_by_tenant(db, tenant_id)


def get_active_subscription(db: Session, tenant_id: str) -> Optional[Subscription]:
    """Most recently updated subscription row for this tenant (used by
    quota_service for the 402 past_due/canceled check). None means the
    tenant has never had a subscription row — treated as an implicit free
    plan with no subscription-status gate.
    """
    return (
        db.query(Subscription)
        .filter(Subscription.tenant_id == tenant_id)
        .order_by(Subscription.updated_at.desc())
        .first()
    )


def upsert_subscription(
    db: Session,
    tenant_id: str,
    stripe_subscription_id: str,
    status: str,
    plan: str,
    current_period_end=None,
) -> Subscription:
    """Create or update the Subscription row for this tenant, keyed by
    stripe_subscription_id. Does not commit — caller controls the transaction
    boundary (the webhook handler commits once per event).
    """
    sub = get_subscription_by_stripe_id(db, stripe_subscription_id)
    if sub is None:
        sub = Subscription(
            tenant_id=tenant_id,
            stripe_subscription_id=stripe_subscription_id,
            status=status,
            plan=plan,
            current_period_end=current_period_end,
        )
        db.add(sub)
    else:
        sub.tenant_id = tenant_id
        sub.status = status
        sub.plan = plan
        sub.current_period_end = current_period_end
    db.flush()
    return sub
