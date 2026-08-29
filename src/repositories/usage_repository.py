"""Usage event repository. Every function requires tenant_id and scopes its
query by it — per AUTHZ_DESIGN.md, tenant_id is never optional here.
"""
from datetime import datetime

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from src.models.db_models import UsageEvent


def get_usage_event_by_idempotency_key(
    db: Session, tenant_id: str, idempotency_key: str
) -> UsageEvent | None:
    return (
        db.query(UsageEvent)
        .filter(
            UsageEvent.tenant_id == tenant_id,
            UsageEvent.idempotency_key == idempotency_key,
        )
        .first()
    )


def insert_usage_event(
    db: Session,
    tenant_id: str,
    idempotency_key: str,
    usage_type: str,
    quantity: int,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    cost_cents: int,
) -> UsageEvent:
    """Inserts a new UsageEvent row. Caller is responsible for catching the
    IntegrityError raised on a duplicate (tenant_id, idempotency_key) and
    handling it per the idempotency strategy in DESIGN.md — this function
    does not swallow it.
    """
    event = UsageEvent(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        usage_type=usage_type,
        quantity=quantity,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cost_cents=cost_cents,
    )
    db.add(event)
    db.flush()  # surfaces IntegrityError without committing the outer txn
    return event


def sum_usage_for_period(
    db: Session, tenant_id: str, period_start: datetime, period_end: datetime
) -> dict:
    """Returns summed counts for this tenant's usage events in [period_start,
    period_end). All values are ints (0 when there are no matching rows).
    """
    row = (
        db.query(
            func.coalesce(
                func.sum(
                    case((UsageEvent.usage_type == "api_call", UsageEvent.quantity), else_=0)
                ),
                0,
            ).label("api_calls"),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0).label("output_tokens"),
            func.coalesce(func.sum(UsageEvent.reasoning_tokens), 0).label("reasoning_tokens"),
        )
        .filter(
            UsageEvent.tenant_id == tenant_id,
            UsageEvent.created_at >= period_start,
            UsageEvent.created_at < period_end,
        )
        .one()
    )

    return {
        "api_calls": int(row.api_calls),
        "input_tokens": int(row.input_tokens),
        "cached_input_tokens": int(row.cached_input_tokens),
        "output_tokens": int(row.output_tokens),
        "reasoning_tokens": int(row.reasoning_tokens),
    }
