"""POST /tenants — creates a new tenant and makes the caller its admin.

Documented exception to the normal AUTHZ_DESIGN.md flow: there is no tenant
to resolve or check membership against yet, so this route depends only on
get_current_actor() (steps 1-2), not require_permission(). See
.claude/docs/TENANT_CREATION.md.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.auth.dependencies import get_current_actor
from src.db.session import get_db
from src.models.db_models import User
from src.schemas import TenantCreateRequest, TenantCreateResponse, TenantMembershipSummary
from src.services import tenant_service

router = APIRouter(tags=["tenants"])


@router.post("/tenants", response_model=TenantCreateResponse, status_code=201)
async def create_tenant(
    body: TenantCreateRequest,
    actor: User = Depends(get_current_actor),
    db: Session = Depends(get_db),
) -> TenantCreateResponse:
    result = tenant_service.create_tenant(db, actor, body.name, body.plan)
    return TenantCreateResponse(
        tenant_id=result.tenant_id,
        name=result.name,
        plan=result.plan,
        membership=TenantMembershipSummary(role=result.role),
    )
