"""Pydantic v2 request/response schemas shared across routers."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# /auth/sync
# ---------------------------------------------------------------------------


class AuthSyncRequest(BaseModel):
    auth_provider_id: str
    email: str


class AuthSyncResponse(BaseModel):
    user_id: str
    email: str


# ---------------------------------------------------------------------------
# /tenants/{tenant_id}/generate
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    usage_type: Literal["api_call", "ai_tokens"]
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


class RemainingQuota(BaseModel):
    api_calls: int
    ai_tokens: int


class GenerateResponse(BaseModel):
    usage_event_id: str
    cost_cents: int
    remaining_quota: RemainingQuota


# ---------------------------------------------------------------------------
# /tenants/{tenant_id}/usage
# ---------------------------------------------------------------------------


class ApiCallsUsage(BaseModel):
    used: int
    limit: int


class AiTokensUsage(BaseModel):
    input: int
    cached_input: int
    output: int
    reasoning: int
    used_total: int
    limit: int


class UsageResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    api_calls: ApiCallsUsage
    ai_tokens: AiTokensUsage
    cost_cents: int


# ---------------------------------------------------------------------------
# /tenants/{tenant_id}/checkout
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    target_plan: Literal["pro"]


class CheckoutResponse(BaseModel):
    checkout_url: str


# ---------------------------------------------------------------------------
# /tenants/{tenant_id}/members
# ---------------------------------------------------------------------------


class InviteMemberRequest(BaseModel):
    email: str
    role: Literal["member", "admin"]


class InviteMemberResponse(BaseModel):
    membership_id: str
    user_id: str
    role: str
