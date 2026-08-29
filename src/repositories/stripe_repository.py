"""Repository for the `stripe_events` table — webhook dedup only.

Per API_CONTRACTS.md's webhook flow: insert-first dedup. Caller inserts a
StripeEvent row; an IntegrityError (unique PK on `id`, or a race between two
concurrent deliveries of the same event) means "already handled" — treat as
success and return 200 without processing again.
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.db_models import StripeEvent


def try_insert_stripe_event(db: Session, event_id: str, event_type: str) -> bool:
    """Attempt to record this Stripe event id as processed.

    Returns True if this call inserted it (i.e. this is the first time we've
    seen this event — proceed with processing). Returns False if it already
    existed (duplicate delivery or race) — caller should skip processing.
    """
    db.add(StripeEvent(id=event_id, event_type=event_type))
    try:
        db.flush()
        return True
    except IntegrityError:
        db.rollback()
        return False
