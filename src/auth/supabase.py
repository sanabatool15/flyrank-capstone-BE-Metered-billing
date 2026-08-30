"""Supabase Auth token verification.

Verifies the `Authorization: Bearer <supabase_access_token>` header and
returns the decoded claims. This is the ONLY thing Supabase Auth replaces in
the request flow described in .claude/docs/AUTHZ_DESIGN.md — step 1
(authentication). Step 2 (actor identification: mapping the token's `sub`
claim to a local `User` row) and everything downstream (tenant resolution,
membership, permissions, audit log) is unchanged.

Supabase projects sign access tokens one of two ways, chosen per-project in
the dashboard (Settings -> API -> JWT Settings):
  - Legacy shared secret (HS256) — verified locally with SUPABASE_JWT_SECRET.
    This is what the test suite always uses (hermetic, no network calls).
  - JWT Signing Keys (ES256/RS256, asymmetric) — the newer default for
    projects created since Supabase introduced this feature. There is no
    shared secret to copy; instead we verify against the project's public
    JWKS (JSON Web Key Set) fetched from Supabase's
    /auth/v1/.well-known/jwks.json endpoint. PyJWKClient caches keys and
    only re-fetches on a kid it hasn't seen, so this stays cheap.

We pick the verification path from the token's own header (`alg`), so both
kinds of tokens work without configuration beyond having SUPABASE_JWT_SECRET
(for HS256 projects) and/or SUPABASE_URL (for asymmetric-key projects) set.
"""
from typing import Any

import jwt
from fastapi import HTTPException

from src.config import get_settings

# Supabase issues access tokens with this audience by default.
SUPABASE_AUDIENCE = "authenticated"

_HS_ALGORITHMS = {"HS256", "HS384", "HS512"}
_ASYMMETRIC_ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}

_jwks_client: "jwt.PyJWKClient | None" = None


def _get_jwks_client(supabase_url: str) -> "jwt.PyJWKClient":
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json")
    return _jwks_client


def decode_supabase_jwt(token: str) -> dict[str, Any]:
    """Decodes and verifies a Supabase Auth access token.

    Raises HTTPException(401) on any verification failure (bad signature,
    expired, wrong audience, malformed, unsupported alg, or missing config
    for the alg the token was actually signed with).
    Returns the token's claims on success (includes at least `sub`, the
    Supabase auth.users.id for this user).
    """
    settings = get_settings()

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from None

    alg = header.get("alg")

    try:
        if alg in _HS_ALGORITHMS:
            secret = settings.SUPABASE_JWT_SECRET
            if not secret:
                raise HTTPException(
                    status_code=401, detail="Supabase JWT secret not configured"
                )
            claims = jwt.decode(
                token,
                secret,
                algorithms=[alg],
                audience=SUPABASE_AUDIENCE,
            )
        elif alg in _ASYMMETRIC_ALGORITHMS:
            if not settings.SUPABASE_URL:
                raise HTTPException(
                    status_code=401, detail="SUPABASE_URL not configured for JWKS verification"
                )
            signing_key = _get_jwks_client(settings.SUPABASE_URL).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=[alg],
                audience=SUPABASE_AUDIENCE,
            )
        else:
            raise HTTPException(status_code=401, detail=f"unsupported token alg: {alg}")
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from None

    if not claims.get("sub"):
        raise HTTPException(status_code=401, detail="token missing sub claim")

    return claims
