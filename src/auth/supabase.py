"""Supabase Auth token verification.

Verifies the `Authorization: Bearer <supabase_access_token>` header against
Supabase's JWT signing secret (HS256) and returns the decoded claims. This
is the ONLY thing Supabase Auth replaces in the request flow described in
.claude/docs/AUTHZ_DESIGN.md — step 1 (authentication). Step 2 (actor
identification: mapping the token's `sub` claim to a local `User` row) and
everything downstream (tenant resolution, membership, permissions, audit
log) is unchanged.

We verify locally with PyJWT + the project's JWT secret rather than calling
out to Supabase over the network, so request auth stays fast and the test
suite stays hermetic (tests sign tokens with a known test secret and point
SUPABASE_JWT_SECRET at it).
"""
from typing import Any

import jwt
from fastapi import HTTPException

from src.config import get_settings

# Supabase issues access tokens with this audience by default.
SUPABASE_AUDIENCE = "authenticated"


def decode_supabase_jwt(token: str) -> dict[str, Any]:
    """Decodes and verifies a Supabase Auth access token.

    Raises HTTPException(401) on any verification failure (bad signature,
    expired, wrong audience, malformed, or secret not configured).
    Returns the token's claims on success (includes at least `sub`, the
    Supabase auth.users.id for this user).
    """
    settings = get_settings()
    secret = settings.SUPABASE_JWT_SECRET
    if not secret:
        raise HTTPException(status_code=401, detail="Supabase JWT secret not configured")

    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=SUPABASE_AUDIENCE,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from None

    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="token missing sub claim")

    return claims
