"""Member-invite business logic.

Design decision (documented per API_CONTRACTS.md's note to pick one): we
implement the "email not found -> 404" branch, not an invite-flow stub. The
target user must already exist locally (i.e. have completed /auth/sync at
least once) before they can be added as a member of a tenant. A true
email-invitation flow (invite unregistered emails, send a signup link) is out
of scope for this phase.
"""
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.repositories import membership_repository


@dataclass
class InviteMemberResult:
    membership_id: str
    user_id: str
    role: str


def invite_member(db: Session, tenant_id: str, email: str, role_name: str) -> InviteMemberResult:
    user = membership_repository.get_user_by_email(db, email)
    if user is None:
        raise HTTPException(status_code=404, detail="no user found with that email")

    existing = membership_repository.get_membership(db, user.id, tenant_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="user is already a member of this tenant")

    role = membership_repository.get_role_by_name(db, tenant_id, role_name)
    if role is None:
        raise HTTPException(status_code=400, detail=f"unknown role: {role_name}")

    membership = membership_repository.create_membership(
        db, tenant_id=tenant_id, user_id=user.id, role_id=role.id
    )
    db.commit()

    return InviteMemberResult(
        membership_id=membership.id,
        user_id=user.id,
        role=role_name,
    )
