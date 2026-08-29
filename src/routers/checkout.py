"""POST /tenants/{tenant_id}/checkout — admin-only, creates a Stripe Checkout
Session (test mode) for upgrading Free -> Pro.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.auth.dependencies import TenantContext, require_permission
from src.db.session import get_db
from src.repositories import tenant_repository
from src.schemas import CheckoutRequest, CheckoutResponse
from src.services import stripe_service

router = APIRouter(tags=["billing"])


@router.post("/tenants/{tenant_id}/checkout", response_model=CheckoutResponse)
async def create_checkout(
    tenant_id: str,
    body: CheckoutRequest,
    ctx: TenantContext = Depends(require_permission("billing.manage")),
    db: Session = Depends(get_db),
):
    tenant = tenant_repository.get_tenant_by_id(db, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="tenant not found")

    checkout_url = stripe_service.create_checkout_session(db, tenant, body.target_plan)
    return CheckoutResponse(checkout_url=checkout_url)
