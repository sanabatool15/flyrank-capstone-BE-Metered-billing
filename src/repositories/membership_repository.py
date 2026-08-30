"""Repository for `memberships`, `roles`, and `users` lookups needed by the
member-invite flow.
"""
from typing import Optional

from sqlalchemy.orm import Session

from src.models.db_models import Membership, Permission, Role, RolePermission, User


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


# ---------------------------------------------------------------------------
# Global-role seeding (see .claude/docs/TENANT_CREATION.md) — get-or-create,
# same idempotent shape as conftest.py's make_role/make_permission helpers so
# it's safe to call on every tenant-creation request, not just once at
# startup.
# ---------------------------------------------------------------------------


def get_or_create_permission(db: Session, name: str) -> Permission:
    perm = db.query(Permission).filter(Permission.name == name).first()
    if perm is not None:
        return perm
    perm = Permission(name=name)
    db.add(perm)
    db.flush()
    return perm


def get_or_create_global_role(db: Session, name: str) -> Role:
    role = db.query(Role).filter(Role.tenant_id.is_(None), Role.name == name).first()
    if role is not None:
        return role
    role = Role(tenant_id=None, name=name)
    db.add(role)
    db.flush()
    return role


def grant_permission_if_missing(db: Session, role_id: str, permission_id: str) -> None:
    exists = (
        db.query(RolePermission)
        .filter(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission_id,
        )
        .first()
    )
    if exists is not None:
        return
    db.add(RolePermission(role_id=role_id, permission_id=permission_id))
    db.flush()
