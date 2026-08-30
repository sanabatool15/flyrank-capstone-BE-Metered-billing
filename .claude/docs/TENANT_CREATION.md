# Tenant Creation — Implementation Spec for Claude Code

`POST /tenants` fills a gap `API_CONTRACTS.md` didn't previously cover: how a
tenant comes into existence in the first place. Every other endpoint in that
doc assumes a tenant already exists and a Membership already ties the caller
to it. This one is the bootstrap step before any of that is true.

---

## `POST /tenants`
**Permission required:** none — see the note below on why this is the one
documented exception to AUTHZ_DESIGN.md's normal flow.

**Headers**
- `Authorization: Bearer <token>` (required)

**Body**
```json
{ "name": "Acme Inc", "plan": "free" }
```

**201 response**
```json
{
  "tenant_id": "uuid",
  "name": "Acme Inc",
  "plan": "free",
  "membership": { "role": "admin" }
}
```

### Step 1 note — why no `require_permission()` here

AUTHZ_DESIGN.md's request flow starts at "RESOLVE TENANT" (step 3) and
"CHECK MEMBERSHIP" (step 4) — both assume a `tenant_id` already exists to
resolve and a `memberships` row that could exist to check. Neither is true
here: this is the request that *creates* the tenant_id and the first
membership row. There is nothing to be "not a member" of yet.

So this route depends only on `get_current_actor()` (AUTHZ_DESIGN.md steps
1-2: authenticate the bearer token, resolve it to a local `User` row) and
skips straight to step 7 (perform the action) and step 8 (audit log). This is
the one endpoint in the system where "authenticated" is sufficient —
everything downstream of tenant creation still goes through the full
authenticate → membership → permission chain as normal.

### Flow

1. Authenticate the caller via `get_current_actor()` — invalid/missing token
   → `401`.
2. Create a `Tenant` row. `plan` is **always** forced to `"free"` server-side
   (see below), regardless of what the request body says.
3. Look up the seeded global admin `Role` row (`tenant_id IS NULL`, name
   `"admin"`) — do not create roles here as a side effect of arbitrary
   requests; role/permission seeding is a startup-time concern, not a
   per-request one. (Note: at the time this spec was written no dedicated
   seed script existed outside test fixtures — see the "Role seeding" section
   below for how that gap was closed.)
4. Create a `Membership(user_id=actor.id, tenant_id=new_tenant.id,
   role_id=admin_role.id)` — the caller is always the new tenant's first
   admin.
5. Write an `AuditLog` row: `decision="allowed"`, `action="tenant.create"`,
   `resource_type="tenant"`, `resource_id=new_tenant.id`,
   `actor_user_id=actor.id`, using the same `audit_log`/`audit_log_sync`
   helper every other endpoint uses (`src/auth/dependencies.py`) — don't
   reinvent audit writing here.
6. Return `201` with the shape above.

### Plan is always Free-first

The request body accepts a `plan` field (defaulting to `"free"`) purely so
the request shape is self-documenting and forward-compatible, but the server
**ignores whatever value is sent** and always creates the tenant on `"free"`.
There is no self-service path to create a tenant already on `"pro"` — the
only way to reach `"pro"` is `POST /tenants/{id}/checkout` followed by the
verified Stripe webhook (`checkout.session.completed`), per
`API_CONTRACTS.md`. This keeps "how a tenant gets billed" a single code path
instead of two.

### Role seeding

`Role.tenant_id` is nullable specifically to support global/system roles
(see `db_models.py`'s comment on `Role`). A seeded global `"admin"` role
carrying `api.use`, `usage.read`, `billing.manage`, `members.invite` is
assumed to exist by this endpoint. Before this endpoint existed, that role
was only ever materialized ad hoc by test fixtures
(`tests/conftest.py::make_role` / `make_member_with_permissions`) — there was
no seed path in application code. This endpoint closes that gap with an
idempotent get-or-create (`tenant_service._ensure_global_admin_role`,
backed by `membership_repository.get_or_create_global_role` /
`get_or_create_permission` / `grant_permission_if_missing`) rather than a
one-off migration, so it self-heals in every environment (including a fresh
Supabase Postgres database) without a separate manual seeding step.

### Zero-membership behavior (why this endpoint has to exist)

A user who has authenticated (completed `POST /auth/sync`) but has zero
memberships anywhere is, by design, not implicitly given any tenant. There is
no fallback "personal tenant" auto-created at signup. `require_permission()`
denies such a user `403` on every tenant-scoped route (`/generate`,
`/usage`, `/checkout`, `/members`) — default-deny, per AUTHZ_DESIGN.md. The
only way out of that state is this endpoint: create a tenant, become its
admin, and from then on the normal membership/permission chain applies.

---

## Permission matrix addition

| Endpoint | Permission | admin | member |
|---|---|---|---|
| `POST /tenants` | none (creates the first membership) | — | — |
