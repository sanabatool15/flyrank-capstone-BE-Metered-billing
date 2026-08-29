"""Core metering orchestration — POST /generate and GET /usage business
logic, per API_CONTRACTS.md. Owns steps 7-8 of AUTHZ_DESIGN.md (action +
allow audit log) for the /generate flow.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth.dependencies import TenantContext, audit_log_sync
from src.repositories import tenant_repository, usage_repository
from src.services import cost_service, quota_service


def record_usage(db: Session, ctx: TenantContext, body, idempotency_key: str) -> dict:
    """Implements the /generate flow: quota check -> idempotent insert ->
    cost calc -> allow audit log. Returns a dict matching GenerateResponse.
    """
    tenant = tenant_repository.get_tenant_by_id(db, ctx.tenant_id)
    plan = tenant.plan

    # Step: subscription status gate (402).
    quota_service.check_subscription_status(db, ctx.tenant_id, plan)

    quantity = 1 if body.usage_type == "api_call" else 0
    request_tokens = (
        body.input_tokens + body.cached_input_tokens + body.output_tokens + body.reasoning_tokens
        if body.usage_type == "ai_tokens"
        else 0
    )

    # Step: quota check (429), also returns pre-request usage totals.
    quota_info = quota_service.check_quota(
        db,
        ctx.tenant_id,
        plan,
        body.usage_type,
        request_quantity=quantity,
        request_tokens=request_tokens,
    )

    input_tokens = body.input_tokens if body.usage_type == "ai_tokens" else 0
    cached_input_tokens = body.cached_input_tokens if body.usage_type == "ai_tokens" else 0
    output_tokens = body.output_tokens if body.usage_type == "ai_tokens" else 0
    reasoning_tokens = body.reasoning_tokens if body.usage_type == "ai_tokens" else 0

    cost_cents = cost_service.calc_cost_cents(
        usage_type=body.usage_type,
        quantity=quantity,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
    )

    try:
        event = usage_repository.insert_usage_event(
            db,
            tenant_id=ctx.tenant_id,
            idempotency_key=idempotency_key,
            usage_type=body.usage_type,
            quantity=quantity,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cost_cents=cost_cents,
        )
        db.commit()
        db.refresh(event)

        # Step 8: allow audit log (service layer, per AUTHZ_DESIGN.md).
        audit_log_sync(
            db,
            ctx.tenant_id,
            ctx.user.id,
            action="generate",
            resource_type="usage_event",
            resource_id=event.id,
            decision="allowed",
            reason=None,
        )
    except IntegrityError:
        db.rollback()
        event = usage_repository.get_usage_event_by_idempotency_key(
            db, ctx.tenant_id, idempotency_key
        )
        # Duplicate request: return identical body, do NOT recompute cost or quota.
        return _to_response(event, quota_info, duplicate=True)

    return _to_response(event, quota_info, duplicate=False)


def _to_response(event, quota_info: dict, duplicate: bool) -> dict:
    limits = quota_info["limits"]
    used_api_calls = quota_info["used_api_calls"]
    used_ai_tokens = quota_info["used_ai_tokens"]

    if not duplicate:
        # This request's own quantity/tokens are already reflected once we
        # account for them below (they were NOT included in the pre-request
        # totals returned by check_quota).
        if event.usage_type == "api_call":
            used_api_calls += event.quantity
        else:
            used_ai_tokens += (
                event.input_tokens
                + event.cached_input_tokens
                + event.output_tokens
                + event.reasoning_tokens
            )

    remaining_api_calls = max(limits["api_calls"] - used_api_calls, 0)
    remaining_ai_tokens = max(limits["ai_tokens"] - used_ai_tokens, 0)

    return {
        "usage_event_id": event.id,
        "cost_cents": event.cost_cents,
        "remaining_quota": {
            "api_calls": remaining_api_calls,
            "ai_tokens": remaining_ai_tokens,
        },
    }


def get_usage_rollup(db: Session, ctx: TenantContext) -> dict:
    """GET /usage rollup per API_CONTRACTS.md. Cost is recomputed from
    summed token TOTALS, not summed per-event cost_cents (DESIGN.md
    rounding-drift note).
    """
    tenant = tenant_repository.get_tenant_by_id(db, ctx.tenant_id)
    plan = tenant.plan
    limits = quota_service.PLAN_LIMITS.get(plan, quota_service.PLAN_LIMITS["free"])
    period = quota_service.current_billing_period()

    totals = usage_repository.sum_usage_for_period(db, ctx.tenant_id, period.start, period.end)

    ai_used_total = (
        totals["input_tokens"]
        + totals["cached_input_tokens"]
        + totals["output_tokens"]
        + totals["reasoning_tokens"]
    )

    cost_cents = cost_service.calc_cost_cents(
        usage_type="ai_tokens",
        input_tokens=totals["input_tokens"],
        cached_input_tokens=totals["cached_input_tokens"],
        output_tokens=totals["output_tokens"],
        reasoning_tokens=totals["reasoning_tokens"],
    ) + cost_service.calc_cost_cents(
        usage_type="api_call",
        quantity=totals["api_calls"],
    )

    return {
        "period_start": period.start,
        "period_end": period.end,
        "api_calls": {"used": totals["api_calls"], "limit": limits["api_calls"]},
        "ai_tokens": {
            "input": totals["input_tokens"],
            "cached_input": totals["cached_input_tokens"],
            "output": totals["output_tokens"],
            "reasoning": totals["reasoning_tokens"],
            "used_total": ai_used_total,
            "limit": limits["ai_tokens"],
        },
        "cost_cents": cost_cents,
    }
