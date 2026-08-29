"""Repository for `memberships`, `roles`, and `users` lookups needed by the
member-invite flow.
"""
from typing import Optional

from sqlalchemy.orm import Session

from src.models.db_models import Membership, Role, User


def get_membership(db: Session, user_id: str, tenant_id: str) -> Optional[Membership]:
    return (
        db.query(Membership)
        .filter(Membership.user_id == user_id, Membership.tenant_id == tenant_id)
        .first()
    )


def create_membership(
    db: Session, tenant_id: str, user_id: str, role_id: str
) -> Membership:
    membership = Membership(tenant_id=tenant_id, user_id=user_id, role_id=role_id)
    db.add(membership)
    db.flush()
    return membership


def get_role_by_name(
    db: Session, tenant_id: Optional[str], name: str
) -> Optional[Role]:
    """Resolve a Role by name, scoped to a tenant-specific custom role first
    (if tenant_id is given and such a role exists), falling back to the
    global role of that name (tenant_id IS NULL).
    """
    if tenant_id is not None:
        role = (
            db.query(Role)
            .filter(Role.tenant_id == tenant_id, Role.name == name)
            .first()
        )
        if role is not None:
            return role
    return db.query(Role).filter(Role.tenant_id.is_(None), Role.name == name).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()
