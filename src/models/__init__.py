"""Import all models so Base.metadata.create_all() (or Alembic autogenerate,
if added later) sees every table."""
from src.models.db_models import (  # noqa: F401
    AuditLog,
    Membership,
    Permission,
    Role,
    RolePermission,
    StripeEvent,
    Subscription,
    Tenant,
    UsageEvent,
    User,
)
