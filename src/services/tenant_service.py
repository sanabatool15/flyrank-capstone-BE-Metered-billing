"""POST /tenants — tenant bootstrap (the documented exception to
AUTHZ_DESIGN.md's normal flow: there is no tenant yet to resolve/check
membership against). See .claude/docs/TENANT_CREATION.md.
"""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.auth.dependencies import audit_log_sync
from src.models.db_models import User
from src.repositories import membership_repository, tenant_repository

# The full permission set the seeded global "admin" role carries. Kept here
# (not in a migration) because there is currently no other seed mechanism in
# this codebase outside test fixtures — see TENANT_CREATION.md's note on the
# seeding gap this fills.
_ADMIN_ROLE_NAME = "admin"
_ADMIN_PERMISSIONS = ("api.use", "usage.read", "billing.manage", "members.invite")


@dataclass
class TenantCreateResult:
    tenant_id: str
    name: str
    plan: str
    role: str


def _ensure_global_admin_role(db: Session):
    """Get-or-create the seeded global admin Role (tenant_id=None) and make
    sure it carries every admin-only permission. Idempotent — safe to call on
    every tenant-creation request.
    """
    role = membership_repository.get_or_create_global_role(db, _ADMIN_ROLE_NAME)
    for pname in _ADMIN_PERMISSIONS:
        perm = membership_repository.get_or_create_permission(db, pname)
        membership_repository.grant_permission_if_missing(db, role.id, perm.id)
    return role


def create_tenant(db: Session, actor: User, name: str, plan: str = "free") -> TenantCreateResult:
    # Plan is always forced to "free" here regardless of what the caller
    # passed — per TENANT_CREATION.md, tenants always start on Free and
    # upgrade only through the verified Stripe webhook flow.
    tenant = tenant_repository.create_tenant(db, name=name, plan="free")

    admin_role = _ensure_global_admin_role(db)

    membership_repository.create_membership(
        db, tenant_id=tenant.id, user_id=actor.id, role_id=admin_role.id
    )

    audit_log_sync(
        db,
        tenant_id=tenant.id,
        actor_user_id=actor.id,
        action="tenant.create",
        resource_type="tenant",
        resource_id=tenant.id,
        decision="allowed",
    )

    db.commit()

    return TenantCreateResult(
        tenant_id=tenant.id, name=tenant.name, plan=tenant.plan, role=_ADMIN_ROLE_NAME
    )
