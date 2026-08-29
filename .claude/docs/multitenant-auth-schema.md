# Multi-Tenant Auth Schema — Design Rationale

This document extends your original billing/usage schema (`Tenant`, `Subscription`,
`UsageEvent`, `StripeEvent`) with the identity/authorization layer that was missing:
`User`, `Membership`, `Role`, `Permission`, `RolePermission`, and `AuditLog`.

Every design decision below is tied back to a specific principle from the lecture
("Trust Issues: A Practical Guide to Auth & Multi-tenancy" — Admir Šaheta, FlyRank).
Nothing here is arbitrary — each table exists because a specific slide said it had to.

---

## 1. The core insight the whole schema is built around

> **Authenticated ≠ Authorized ≠ Allowed to touch this tenant's data.**

These are three separate questions, and this schema deliberately keeps them as
three separate *concerns* in the data model, not one blob:

| Question | Answered by |
|---|---|
| Who are you? | `User` (+ your auth provider, e.g. Supabase) |
| Which org are you acting in, and with what role? | `Membership` |
| What is that role actually allowed to do? | `Role` → `RolePermission` → `Permission` |
| Does this specific resource belong to your org? | `tenant_id` column on every tenant-owned table (already in your `UsageEvent`, `Subscription`) |

If you collapse these into one table (e.g. a `role` column directly on `User`),
you lose the ability to represent one user belonging to multiple tenants with
different roles in each — which the lecture calls out explicitly as a real,
common scenario (a consultant working across three client orgs).

---

## 2. `User` — Authentication identity

```python
class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    email = Column(String, nullable=False, unique=True)
    auth_provider_id = Column(String, nullable=False, unique=True)  # e.g. Supabase auth.users.id
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    memberships = relationship("Membership", back_populates="user")
```

**Why it exists at all:** the original schema had `Tenant`, `Subscription`,
`UsageEvent` — all *tenant*-scoped data — but no representation of an actual
*person*. The lecture is explicit: "authentication answers who you are" — you
need a table for "who," separate from "which org."

**Why `auth_provider_id` and not a password hash column:** per the lecture's
underlying philosophy (and your own assignment, which uses Supabase Auth as
Identity Provider) — you should not be storing or hashing passwords yourself.
Supabase (or Auth0/Clerk/whatever IdP) owns credential verification. Your
database only needs to know "this row corresponds to that IdP's user ID" so you
can join your own domain data (memberships, usage, billing) against it.

**Why `email` is duplicated here even though Supabase also has it:** convenience
for queries/joins without hitting the IdP API on every read. This is a common,
accepted denormalization — just remember Supabase remains the source of truth
for whether that email is *verified*, not this table.

---

## 3. `Membership` — the authorization bridge (the most important table)

```python
class Membership(Base):
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
        # composite uniqueness: one membership row per (user, tenant) pair
        UniqueConstraint("user_id", "tenant_id", name="uq_user_tenant"),
        Index("ix_membership_tenant", "tenant_id"),
        Index("ix_membership_user", "user_id"),
    )
```

**Direct quote this implements:** *"Membership is the authorization bridge — the
row that proves 'this user belongs here, with this role.'"*

**Why `UniqueConstraint("user_id", "tenant_id")` and not a composite primary key:**
Both are valid per the lecture discussion. I used a separate `id` (surrogate key)
+ a unique constraint on the pair, rather than making `(user_id, tenant_id)`
itself the primary key. Reasoning: a surrogate key gives you a stable single
column to reference from other tables later (e.g. an `AuditLog` row that wants
to say "this action was taken under membership X") without needing a composite
foreign key everywhere. The unique constraint still gives you the same DB-enforced
guarantee — "user_id + tenant_id together must be unique" — just without forcing
every downstream foreign key to be composite too.

**Why `user_id` alone is not enough (and this is the whole point of the table):**
Per the "User ≠ Tenant" slide: *"user.id is not enough context. You need
user.id + tenant.id."* A user can have zero, one, or many `Membership` rows —
one per organization they belong to, each with a potentially different role.

**Why this table is queried on every single authorization check:** given a
request from an authenticated user, acting in a specific tenant context (resolved
from the URL/subdomain/session — never trusted from a client-sent field), the
very first thing your `authorize()` function does is:

```sql
SELECT role_id FROM memberships WHERE user_id = ? AND tenant_id = ?
```

No row → deny immediately (default deny). Row found → proceed to check that
role's permissions.

---

## 4. `Role` and `Permission` — granular permissions, not role explosion

```python
class Role(Base):
    __tablename__ = "roles"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=True)
    # nullable tenant_id = a global/system role (e.g. "Owner", "Viewer")
    # non-null tenant_id = a tenant-specific custom role, if you ever support that
    name = Column(String, nullable=False)  # "Owner", "Admin", "Editor", "Viewer"

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_tenant_role_name"),
    )


class Permission(Base):
    __tablename__ = "permissions"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    # naming convention from the lecture: resource.action
    name = Column(String, nullable=False, unique=True)
    # e.g. "projects.read", "projects.delete", "billing.manage", "members.invite"


class RolePermission(Base):
    """junction table: which permissions does a role actually bundle"""
    __tablename__ = "role_permissions"
    role_id = Column(UUID(as_uuid=False), ForeignKey("roles.id"), primary_key=True)
    permission_id = Column(UUID(as_uuid=False), ForeignKey("permissions.id"), primary_key=True)
```

**Direct quote this implements:** *"Roles are a bundle. Permissions are the
primitive."* and *"You need explicit permissions, not more roles."*

**Why `Permission.name` follows `resource.action`:** this is the exact naming
convention shown on the "Permissions" slide (`projects.read`, `billing.manage`,
`members.invite`). It's explicit and unambiguous — no guessing what a role
secretly includes.

**Why `RolePermission` is a plain junction table with a composite primary key
(no surrogate `id`):** unlike `Membership`, nothing else in the system needs to
reference "this specific role-permission link" individually — it's a pure
many-to-many bridge with no independent identity of its own. A composite PK
`(role_id, permission_id)` is the textbook-correct shape for that.

**Why this prevents "role explosion":** per the lecture's "RBAC gets messy"
slide — `Admin, but not billing`, `Admin, except production`, etc. — the fix is:
*don't create new roles for exceptions.* With this structure, "Admin minus
billing" is not a new `Role` row. It's just: create a role, attach every
permission *except* `billing.manage`/`billing.read` via `RolePermission`. If
tomorrow you need "Admin minus billing, EU only," that's a policy-layer
condition applied at request time (see §7), not a new role in the database.

**Why `Role.tenant_id` is nullable:** most SaaS products start with a small,
fixed set of global roles (Owner/Admin/Editor/Viewer) shared across all tenants.
Making `tenant_id` nullable lets you support that default case cleanly, while
leaving the door open for a tenant to define its own custom role later without
a schema migration.

---

## 5. Tying it back to your original billing tables

Your original `Tenant`, `Subscription`, `UsageEvent`, `StripeEvent` tables are
**unchanged** — they were already correctly designed per the lecture's core
rule: *"every tenant-owned table needs a tenant boundary."* `tenant_id` was
already present and indexed on both `Subscription` and `UsageEvent`. Nothing
about adding the user/membership/role layer requires touching those.

`StripeEvent` correctly has **no** `tenant_id` — it's global webhook dedup
infrastructure (Stripe's own `event.id` as primary key), not tenant-owned data.
That was a correct call in the original schema and stays correct here.

```python
class Tenant(Base):
    # ... your existing fields (id, name, plan, stripe_customer_id, created_at) ...
    memberships = relationship("Membership", back_populates="tenant")
```
(Only addition: the `memberships` relationship, so you can query
"who belongs to this tenant" from the `Tenant` side too.)

---

## 6. `AuditLog` — checklist item #7

```python
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(UUID(as_uuid=False), primary_key=True, default=uid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False)
    actor_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
    # nullable actor: some events are system-initiated (webhooks, cron), not user-initiated
    action = Column(String, nullable=False)          # e.g. "invoice.delete"
    resource_type = Column(String, nullable=False)    # e.g. "invoice"
    resource_id = Column(String, nullable=True)
    decision = Column(String, nullable=False)          # "allowed" | "denied"
    reason = Column(String, nullable=True)              # e.g. "missing permission: billing.manage"
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_audit_tenant_created", "tenant_id", "created_at"),
    )
```

**Direct quote this implements:** the authorization checklist's 7th question —
*"is the operation auditable?"* — and the "Authorization Flow" diagram, where
`AUDIT LOG` is the terminal node every request flows into, **regardless of
whether the decision was allow or deny.**

**Why `decision` and `reason` are logged even on denials:** a denied request is
often more security-relevant than an allowed one — it's either a legitimate
user hitting a permission wall (useful for support/debugging) or someone probing
your system for weaknesses (useful for security monitoring). Logging only
successes would miss exactly the events you most want visibility into.

**Why `actor_user_id` is nullable:** background jobs, webhooks (Stripe events
processing into `UsageEvent`), and system-triggered actions aren't performed
by a logged-in user — but they still touch tenant data and should still be
auditable.

---

## 7. What this schema deliberately does NOT do (and why)

**No `role` column directly on `Membership` resolved to a hardcoded permission
set in application code.** I used the full `Role → RolePermission → Permission`
chain instead. This is slightly more setup than hardcoding
`if role == "admin": allow_everything()`, but it's exactly what the lecture
warns against — hardcoded role checks scattered through route handlers are
what causes "role explosion" pain later. The extra table is the cost of
avoiding that.

**No `tenant_id` on `User` or `Permission`.** A user is not owned by a tenant
(they can belong to many, per "User ≠ Tenant") — ownership lives in
`Membership`, not `User`. Permissions are global definitions (`projects.delete`
means the same thing everywhere) — only the *bundling* of permissions into
roles is tenant-specific, which is why `RolePermission` and `Role.tenant_id`
carry that nuance, not `Permission` itself.

**No enforcement logic lives in this file.** This is the schema layer only —
the `Repository` layer in the lecture's diagram (`Browser → API →
Authorization → Service → Repository → Database`). The actual
`authorize(actor, action, resource, tenant)` function, and the scoped queries
that use these tables (`WHERE tenant_id = ? AND ...`), belong in application
code, not in `models.py`. This file gives that code the structural building
blocks; it doesn't replace the need to write the checks.

**No Row-Level Security (RLS) policies shown here.** RLS is a Postgres-level
`CREATE POLICY` construct, not something expressible in SQLAlchemy models. If
you're on Postgres/Supabase, every tenant-scoped table above (`memberships`,
`usage_events`, `subscriptions`, `audit_logs`) should also have an RLS policy
restricting rows to `tenant_id = current_setting('app.tenant_id')` (or your
equivalent session variable) as the database-level backstop — per the
"Assume someone will eventually forget a check" principle. That's a follow-up
task, separate from this schema file.

---

## 8. The full authorization flow, using these tables

Matches the lecture's closing diagram exactly:

```
1. REQUEST            → incoming HTTP request
2. AUTHENTICATION      → verify Supabase JWT/session → get auth_provider_id
3. IDENTIFY ACTOR      → SELECT * FROM users WHERE auth_provider_id = ?
4. RESOLVE TENANT      → from URL/subdomain/session (never client body/header)
5. CHECK MEMBERSHIP    → SELECT role_id FROM memberships
                          WHERE user_id = ? AND tenant_id = ?
                          → no row = deny here (default deny)
6. CHECK PERMISSION    → SELECT 1 FROM role_permissions rp
                          JOIN permissions p ON p.id = rp.permission_id
                          WHERE rp.role_id = ? AND p.name = 'invoices.read'
                          → no row = deny here
7. SCOPE RESOURCE QUERY → SELECT * FROM invoices
                          WHERE id = ? AND tenant_id = ?
                          (tenant_id baked into the query itself — not a
                          separate ownership check after fetching)
8. ACTION               → return the data / perform the action
9. AUDIT LOG            → INSERT INTO audit_logs (..., decision='allowed', ...)
                          (runs on step 5/6 failures too, with decision='denied')
```

Steps 5 and 6 are two *separate* denials in the schema on purpose — a user can
be a legitimate member of a tenant (step 5 passes) but still lack a specific
permission (step 6 fails). Collapsing them into one check would lose that
distinction, which matters both for correct behavior and for meaningful audit
log `reason` messages.
