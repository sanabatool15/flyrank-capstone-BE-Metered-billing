"""Quota / subscription-status checks against the Free/Pro plan limits table
in DESIGN.md.
"""
from datetime import datetime, timezone
from typing import NamedTuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.repositories import subscription_repository, usage_repository

PLAN_LIMITS = {
    "free": {"api_calls": 1_000, "ai_tokens": 100_000},
    "pro": {"api_calls": 50_000, "ai_tokens": 5_000_000},
}


class BillingPeriod(NamedTuple):
    start: datetime
    end: datetime


def current_billing_period(now: datetime | None = None) -> BillingPeriod:
    """Calendar-month billing period, UTC. No proration (DESIGN.md non-goal)."""
    now = now or datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return BillingPeriod(start=start, end=end)


def check_subscription_status(db: Session, tenant_id: str, plan: str) -> None:
    """402 if the tenant's subscription is past_due or canceled.

    A tenant with no Subscription row at all (never upgraded, still on the
    implicit free plan) is not gated here.
    """
    if plan == "free":
        return

    subscription = subscription_repository.get_active_subscription(db, tenant_id)
    if subscription is None:
        return

    if subscription.status in ("past_due", "canceled"):
        raise HTTPException(
            status_code=402,
            detail={
                "error": "payment_required",
                "message": f"subscription is {subscription.status} — update payment method",
                "upgrade_url": "/tenants/{tenant_id}/checkout",
            },
        )


def check_quota(
    db: Session,
    tenant_id: str,
    plan: str,
    usage_type: str,
    request_quantity: int,
    request_tokens: int,
) -> dict:
    """429 if this request would push usage over the plan limit. Returns the
    current-period usage totals (pre-request) so the caller can compute
    remaining_quota after a successful insert without a second query.
    """
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    period = current_billing_period()
    totals = usage_repository.sum_usage_for_period(db, tenant_id, period.start, period.end)

    used_api_calls = totals["api_calls"]
    used_ai_tokens = (
        totals["input_tokens"]
        + totals["cached_input_tokens"]
        + totals["output_tokens"]
        + totals["reasoning_tokens"]
    )

    if usage_type == "api_call":
        if used_api_calls + request_quantity > limits["api_calls"]:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "quota_exceeded",
                    "message": (
                        f"monthly API call limit reached "
                        f"({used_api_calls}/{limits['api_calls']})"
                    ),
                    "retry_after": None,
                },
            )
    else:  # ai_tokens
        if used_ai_tokens + request_tokens > limits["ai_tokens"]:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "quota_exceeded",
                    "message": (
                        f"monthly AI token limit reached "
                        f"({used_ai_tokens}/{limits['ai_tokens']})"
                    ),
                    "retry_after": None,
                },
            )

    return {
        "period": period,
        "limits": limits,
        "used_api_calls": used_api_calls,
        "used_ai_tokens": used_ai_tokens,
        "totals": totals,
    }
