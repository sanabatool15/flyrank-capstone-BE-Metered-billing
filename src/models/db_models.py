"""SQLAlchemy ORM models.

Billing/usage core: Tenant, Subscription, UsageEvent, StripeEvent.
Identity/authorization layer (see .claude/docs/multitenant-auth-schema.md):
User, Membership, Role, Permission, RolePermission, AuditLog.

Money is always stored as integer cents (never float) per CLAUDE.md.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import relationship

from src.db.session import Base


def uid() -> str:
    """Generates a string UUID4, used as the default for every primary key."""
    return str(uuid.uuid4())


plan_enum = ENUM("free", "pro", name="plan_type")
usage_type_enum = ENUM("api_call", "ai_tokens", name="usage_type")
sub_status_enum = ENUM("active", "past_due", "canceled", name="sub_status")


# ---------------------------------------------------------------------------
# Billing / usage core
# ---------------------------------------------------------------------------


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    name = Column(String, nullable=False)
    plan = Column(plan_enum, nullable=False, default="free")
    stripe_customer_id = Column(String, unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    usage_events = relationship("UsageEvent", back_populates="tenant")
    memberships = relationship("Membership", back_populates="tenant")


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    stripe_subscription_id = Column(String, unique=True, nullable=False)
    status = Column(sub_status_enum, nullable=False, default="active")
    plan = Column(plan_enum, nullable=False)
    current_period_end = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_sub_tenant", "tenant_id"),)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    idempotency_key = Column(String, nullable=False)
    usage_type = Column(usage_type_enum, nullable=False)

    # api_call: qty = 1 usually. ai_tokens: split by category, cents-safe ints
    quantity = Column(Integer, nullable=False, default=0)          # api_call count
    input_tokens = Column(Integer, nullable=False, default=0)
    cached_input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    reasoning_tokens = Column(Integer, nullable=False, default=0)

    cost_cents = Column(BigInteger, nullable=False, default=0)     # computed at write time
    created_at = Column(DateTime, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="usage_events")

    __table_args__ = (
        # THE idempotency guarantee — same tenant + same key = one row, DB-enforced not app-enforced
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_tenant_idempotency"),
        Index("ix_usage_tenant_created", "tenant_id", "created_at"),
    )


class StripeEvent(Base):
    """webhook dedup — Stripe event.id is globally unique"""
    __tablename__ = "stripe_events"
    id = Column(String, primary_key=True)  # evt_... from Stripe, no uuid needed
    event_type = Column(String, nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------------------
# Identity / authorization layer
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    email = Column(String, nullable=False, unique=True)
    auth_provider_id = Column(String, nullable=False, unique=True)  # e.g. Supabase auth.users.id
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    memberships = relationship("Membership", back_populates="user")


class Membership(Base):
    """The authorization bridge: proves 'this user belongs here, with this role.'"""

    __tablename__ = "memberships"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    role_id = Column(UUID(as_uuid=False), ForeignKey("roles.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="memberships")
    tenant = relationship("Tenant", back_populates="memberships")
    role = relationship("Role")

    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
        Index("ix_membership_tenant", "tenant_id"),
        Index("ix_membership_user", "user_id"),
    )


class Role(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=True)
    # nullable tenant_id = a global/system role (e.g. "Owner", "Viewer")
    # non-null tenant_id = a tenant-specific custom role, if ever supported
    name = Column(String, nullable=False)  # "Owner", "Admin", "Editor", "Viewer"

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tenant_role_name"),
    )


class Permission(Base):
    __tablename__ = "permissions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    # naming convention: resource.action, e.g. "projects.read", "billing.manage"
    name = Column(String, nullable=False, unique=True)


class RolePermission(Base):
    """Junction table: which permissions a role actually bundles."""

    __tablename__ = "role_permissions"

    role_id = Column(UUID(as_uuid=False), ForeignKey("roles.id"), primary_key=True)
    permission_id = Column(UUID(as_uuid=False), ForeignKey("permissions.id"), primary_key=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    # nullable: some events are system/job-initiated and are not scoped to a
    # single tenant (e.g. the reconcile_subscriptions background job's
    # run-level failure-alert row — see
    # .claude/docs/BACKGROUND_JOBS_DESIGN.md). Deviation from the original
    # NOT NULL: that job's summary alert genuinely has no single tenant to
    # attribute it to (it can span many tenants' failures in one row).
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=True)
    actor_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    # nullable actor: some events are system-initiated (webhooks, cron)
    action = Column(String, nullable=False)  # e.g. "invoice.delete"
    resource_type = Column(String, nullable=False)  # e.g. "invoice"
    resource_id = Column(String, nullable=True)
    decision = Column(String, nullable=False)  # "allowed" | "denied"
    reason = Column(String, nullable=True)  # e.g. "missing permission: billing.manage"
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
    )
