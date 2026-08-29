"""Authentication + authorization dependencies.

Implements the request flow from .claude/docs/AUTHZ_DESIGN.md:
  1-2. authentication + actor identification -> get_current_actor()
  3.   tenant resolution (from path param) -> require_permission()
  4-5. membership + permission checks -> require_permission()
  6.   tenant scoping -> enforced in the repository layer, not here

Router layer owns steps 1-3 (via these dependencies); require_permission()
here also performs steps 4-5 as recommended by AUTHZ_DESIGN.md ("one place,
be consistent"). Step 8 (allow audit log) is written by the SERVICE layer,
not here — see meter_service.py / usage read path.
"""
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.models.db_models import AuditLog, Membership, Permission, RolePermission, Tenant, User


# ---------------------------------------------------------------------------
# Step 1-2: authentication + actor identification
# ---------------------------------------------------------------------------


async def get_current_actor(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Resolves the bearer token to a local User row.

    STAND-IN IMPLEMENTATION: there is no real IdP wired in yet. We treat the
    raw bearer token as the `auth_provider_id` directly. In production this
    would instead verify a Supabase (or other IdP) JWT's signature/expiry and
    extract the `sub` claim as auth_provider_id — swap that in here without
    touching any downstream code, since everything else only depends on the
    returned `User` row.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing or malformed bearer token")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")

    # STAND-IN: token IS the auth_provider_id (see docstring above).
    auth_provider_id = token

    user = db.query(User).filter(User.auth_provider_id == auth_provider_id).first()
    if not user:
        # Token well-formed but no matching local user = broken/unsynced state; treat as unauth.
        raise HTTPException(status_code=401, detail="no user found for token")

    return user


# ---------------------------------------------------------------------------
# TenantContext + audit_log helper
# ---------------------------------------------------------------------------


@dataclass
class TenantContext:
    user: User
    tenant_id: str
    membership: Membership


def audit_log_sync(
    db: Session,
    tenant_id: str,
    actor_user_id: str | None,
    action: str,
    resource_type: str = "tenant",
    resource_id: str | None = None,
    decision: str = "denied",
    reason: str | None = None,
) -> None:
    """Inserts an AuditLog row (sync). Used for step 4/5 denials here, and
    for the step-8 allow entry (written by the service layer, per
    AUTHZ_DESIGN.md).
    """
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        decision=decision,
        reason=reason,
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    db.commit()


async def audit_log(
    db: Session,
    tenant_id: str,
    actor_user_id: str | None,
    action: str,
    resource_type: str = "tenant",
    resource_id: str | None = None,
    decision: str = "denied",
    reason: str | None = None,
) -> None:
    """Async wrapper kept for call sites that `await audit_log(...)` per
    AUTHZ_DESIGN.md's example code shape. Delegates to the sync version.
    """
    audit_log_sync(
        db, tenant_id, actor_user_id, action, resource_type, resource_id, decision, reason
    )


# ---------------------------------------------------------------------------
# Step 3-5: require_permission — the one function that matters most
# ---------------------------------------------------------------------------


def require_permission(permission_name: str):
    async def _check(
        tenant_id: str,
        actor: User = Depends(get_current_actor),
        db: Session = Depends(get_db),
    ) -> TenantContext:
        # Step 3: resolve tenant (from URL path param only) — 404 if it doesn't exist.
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="not found")

        # Step 4: membership check.
        membership = (
            db.query(Membership)
            .filter_by(user_id=actor.id, tenant_id=tenant_id)
            .first()
        )
        if not membership:
            await audit_log(
                db, tenant_id, actor.id, action="access_check",
                decision="denied", reason="not a member",
            )
            raise HTTPException(status_code=403, detail="not a member of this tenant")

        # Step 5: permission check.
        has_perm = (
            db.query(RolePermission)
            .join(Permission, Permission.id == RolePermission.permission_id)
            .filter(
                RolePermission.role_id == membership.role_id,
                Permission.name == permission_name,
            )
            .first()
        )
        if not has_perm:
            await audit_log(
                db, tenant_id, actor.id, action="access_check",
                decision="denied", reason=f"missing permission: {permission_name}",
            )
            raise HTTPException(status_code=403, detail=f"missing permission: {permission_name}")

        return TenantContext(user=actor, tenant_id=tenant_id, membership=membership)

    return _check
