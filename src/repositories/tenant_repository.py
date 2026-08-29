"""Repository for the `tenants` table.

NOTE: per orchestration instructions this file is primarily owned by another
engineer and is read-only for us; this stub adds only the one function the
checkout/webhook flow needs, since the file was empty (0 bytes) when this
work started. Do not remove or edit functions the other engineer adds here.
"""
from typing import Optional

from sqlalchemy.orm import Session

from src.models.db_models import Tenant


def get_tenant_by_id(db: Session, tenant_id: str) -> Optional[Tenant]:
    return db.query(Tenant).filter(Tenant.id == tenant_id).first()


def get_tenant_by_stripe_customer_id(
    db: Session, stripe_customer_id: str
) -> Optional[Tenant]:
    """Cross-tenant lookup by Stripe's own customer id — used only at webhook
    delivery time, where Stripe gives us its customer id, not our tenant_id.
    """
    return (
        db.query(Tenant)
        .filter(Tenant.stripe_customer_id == stripe_customer_id)
        .first()
    )


def update_tenant_stripe_customer_id(
    db: Session, tenant_id: str, stripe_customer_id: str
) -> Optional[Tenant]:
    tenant = get_tenant_by_id(db, tenant_id)
    if tenant is None:
        return None
    tenant.stripe_customer_id = stripe_customer_id
    db.flush()
    return tenant


def update_tenant_plan(db: Session, tenant_id: str, plan: str) -> Optional[Tenant]:
    tenant = get_tenant_by_id(db, tenant_id)
    if tenant is None:
        return None
    tenant.plan = plan
    db.flush()
    return tenant
