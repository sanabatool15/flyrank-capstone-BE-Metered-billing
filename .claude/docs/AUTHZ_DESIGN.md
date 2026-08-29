# Authorization Design — Implementation Spec for Claude Code

Read this before touching any route. Every protected endpoint MUST go through
the full chain below, in order. No shortcuts, no "trust the frontend."

## Core rule

> Authenticated ≠ Authorized ≠ Allowed to touch this tenant's data.

Three separate checks, three separate failure modes:
1. Not authenticated → `401`
2. Authenticated but no membership in this tenant → `403` (deny, don't leak tenant existence)
3. Member but role lacks the required permission → `403` with a `reason`

## Request flow (every protected endpoint)

```
1. AUTHENTICATION
   → verify bearer token / session (via IdP, e.g. Supabase JWT)
   → extract auth_provider_id
   → no valid token → 401

2. IDENTIFY ACTOR
   → SELECT * FROM users WHERE auth_provider_id = ?
   → no match → 401 (token valid but no local user row = broken state, treat as unauth)

3. RESOLVE TENANT
   → from URL path param (/tenants/{tenant_id}/...) or subdomain
   → NEVER from request body, NEVER from a header the client sets themselves
   → tenant_id doesn't exist → 404 (don't distinguish "doesn't exist" vs "not yours" in the message)

4. CHECK MEMBERSHIP
   → SELECT role_id FROM memberships WHERE user_id = ? AND tenant_id = ?
   → no row → 403, log to AuditLog (decision=denied, reason="not a member")

5. CHECK PERMISSION
   → SELECT 1 FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
     WHERE rp.role_id = ? AND p.name = '<required_permission>'
   → no row → 403, log to AuditLog (decision=denied, reason="missing permission: <name>")

6. SCOPE EVERY QUERY BY tenant_id
   → every repository method takes tenant_id as a required, non-optional argument
   → never fetch-then-check-owner in application code — bake tenant_id into the WHERE clause

7. ACTION → perform the operation

8. AUDIT LOG → INSERT audit_logs row, decision=allowed, on every request that reached step 7
   (steps 4/5 already logged their own denials before returning)
```

Steps 4 and 5 are separate on purpose. A user can be a real member (4 passes)
but lack a specific permission (5 fails) — collapsing these loses that
distinction in both behavior and audit trail.

## Layer responsibilities (Router → Service → Repository)

**Router (FastAPI route handler / equivalent)**
- Owns steps 1–3 only: authentication, actor identification, tenant resolution
- Implemented as a reusable dependency, e.g.:
  ```python
  async def get_current_actor(token: str = Depends(oauth2_scheme)) -> User: ...
  async def get_tenant_context(
      tenant_id: str,
      actor: User = Depends(get_current_actor),
  ) -> TenantContext:  # dataclass: {user, tenant_id}
      ...
  ```
- Route signature declares the required permission explicitly, e.g.:
  ```python
  @router.post("/tenants/{tenant_id}/generate")
  async def generate(
      ctx: TenantContext = Depends(require_permission("api.use")),
      body: GenerateRequest = ...,
  ):
      return await usage_service.record(ctx, body)
  ```
- Router NEVER contains business logic, quota math, or raw SQL.

**Service layer**
- Owns steps 4–5 (via a shared `require_permission(name)` dependency/decorator
  the router calls, OR the service re-validates if you prefer defense-in-depth —
  pick one place and be consistent, don't split the check across both layers)
- Owns business rules: quota checks, cost calculation, idempotency handling
- Calls repository methods with `tenant_id` always explicit, never implicit
- Writes the AuditLog entry (both allow and deny paths)

**Repository layer**
- Owns step 6 exclusively: every method signature requires `tenant_id`
- No method may query a tenant-owned table without a `WHERE tenant_id = ?` clause
- Example: `get_usage_events(tenant_id: str, ...)` — not `get_usage_events(...)`
  with tenant filtering left to the caller. Make it structurally impossible to
  forget.

## `require_permission` — the one function that matters most

```python
def require_permission(permission_name: str):
    async def _check(
        tenant_id: str,
        actor: User = Depends(get_current_actor),
        db: Session = Depends(get_db),
    ) -> TenantContext:
        membership = db.query(Membership).filter_by(
            user_id=actor.id, tenant_id=tenant_id
        ).first()
        if not membership:
            await audit_log(db, tenant_id, actor.id, action="access_check",
                             decision="denied", reason="not a member")
            raise HTTPException(403, "not a member of this tenant")

        has_perm = db.query(RolePermission).join(Permission).filter(
            RolePermission.role_id == membership.role_id,
            Permission.name == permission_name,
        ).first()
        if not has_perm:
            await audit_log(db, tenant_id, actor.id, action="access_check",
                             decision="denied", reason=f"missing permission: {permission_name}")
            raise HTTPException(403, f"missing permission: {permission_name}")

        return TenantContext(user=actor, tenant_id=tenant_id, membership=membership)
    return _check
```

Every protected route depends on this, parameterized with the one permission
it needs. Nothing else implements permission logic ad hoc.

## Explicit non-negotiables

- Tenant ID is **never** trusted from request body or a client-set header —
  path param (or subdomain) + server-side lookup only.
- No route handler queries the DB directly — always through service → repository.
- No repository method has an optional/omittable `tenant_id` parameter.
- Every deny (403/401) still gets logged to `AuditLog` — denials are often more
  security-relevant than allows.
- `billing.manage` and `members.invite` are admin-only; `api.use` and
  `usage.read` are available to both `admin` and `member` roles (see
  DESIGN.md for the full role/permission matrix).
