"""POST /tenants/{tenant_id}/members — admin-only, adds an existing User (by
email) as a Membership on this tenant.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.auth.dependencies import TenantContext, require_permission
from src.db.session import get_db
from src.schemas import InviteMemberRequest, InviteMemberResponse
from src.services import membership_service

router = APIRouter(tags=["members"])


@router.post("/tenants/{tenant_id}/members", response_model=InviteMemberResponse)
async def invite_member(
    tenant_id: str,
    body: InviteMemberRequest,
    ctx: TenantContext = Depends(require_permission("members.invite")),
    db: Session = Depends(get_db),
):
    result = membership_service.invite_member(db, tenant_id, body.email, body.role)
    return InviteMemberResponse(
        membership_id=result.membership_id,
        user_id=result.user_id,
        role=result.role,
    )
