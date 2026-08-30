"""Background job: reconcile local Subscription/Tenant state against Stripe.

Stripe delivers webhooks at-least-once but not guaranteed-instantly; a
dropped/delayed delivery can leave `Tenant.plan` or `Subscription.status`
stale (see .claude/docs/BACKGROUND_JOBS_DESIGN.md). This job polls Stripe
for the true status of every tenant with a `stripe_customer_id` and
corrects any drift.

Run on demand:      uv run python -m src.jobs.reconcile_subscriptions
Run on a schedule:   host-level cron, see BACKGROUND_JOBS_DESIGN.md /
                      capstone.yaml's background_job.schedule.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import stripe
from sqlalchemy.orm import Session

from src.db.session import SessionLocal
from src.models.db_models import AuditLog, Tenant
from src.repositories import subscription_repository, tenant_repository
from src.services.stripe_service import map_status_to_plan

logger = logging.getLogger("src.jobs.reconcile_subscriptions")


@dataclass
class ReconciliationSummary:
    started_at: datetime
    finished_at: datetime = None
    checked: int = 0
    corrected: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def run() -> ReconciliationSummary:
    """Top-level entry: open a DB session, call reconcile_all(), log summary,
    write an alert row if failed > 0, close the session. Never raises —
    catches everything so a cron invocation always exits 0 unless the whole
    process itself is broken (e.g. can't connect to the DB at all, in which
    case it should exit non-zero so cron/alerting notices).
    """
    started_at = datetime.now(timezone.utc)
    try:
        db = SessionLocal()
    except Exception:
        logger.critical("reconcile_subscriptions: could not open DB session", exc_info=True)
        raise

    try:
        summary = reconcile_all(db)
    except Exception as exc:  # noqa: BLE001 - top-level catch-all per design
        logger.critical(
            "subscription reconciliation run crashed unexpectedly: %s", exc, exc_info=True
        )
        summary = ReconciliationSummary(
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            failed=1,
            errors=[str(exc)],
        )
    else:
        summary.finished_at = datetime.now(timezone.utc)
    finally:
        try:
            if summary.failed > 0:
                _write_failure_alert(db, summary)
        except Exception:
            logger.critical(
                "reconcile_subscriptions: failed to write failure-alert AuditLog row",
                exc_info=True,
            )
        db.close()

    logger.info(
        "subscription reconciliation finished: checked=%d corrected=%d failed=%d",
        summary.checked,
        summary.corrected,
        summary.failed,
    )
    return summary


def _write_failure_alert(db: Session, summary: "ReconciliationSummary") -> None:
    reason = f"{summary.failed} tenant(s) failed reconciliation: {'; '.join(summary.errors[:5])}"
    logger.critical("subscription reconciliation run had failures: %s", reason)
    db.add(
        AuditLog(
            tenant_id=None,
            actor_user_id=None,
            action="job.reconcile_subscriptions.failed",
            resource_type="background_job",
            resource_id=None,
            # `decision` is otherwise an authz-decision field ("allowed" /
            # "denied"); repurposed here to mean "job did not fully succeed"
            # per BACKGROUND_JOBS_DESIGN.md, rather than inventing a new
            # column/table for a test-mode capstone.
            decision="denied",
            reason=reason,
        )
    )
    db.commit()


def reconcile_all(db: Session) -> ReconciliationSummary:
    """Iterate every Tenant with a non-null stripe_customer_id, call
    reconcile_one() per tenant with retry, aggregate results."""
    started_at = datetime.now(timezone.utc)
    summary = ReconciliationSummary(started_at=started_at)

    tenants = tenant_repository.get_tenants_with_stripe_customer(db)
    for tenant in tenants:
        summary.checked += 1
        try:
            corrected = _with_retry(lambda t=tenant: reconcile_one(db, t))
            if corrected:
                summary.corrected += 1
        except Exception as exc:  # noqa: BLE001 - isolate one tenant's failure
            summary.failed += 1
            summary.errors.append(f"tenant={tenant.id}: {exc}")
            logger.critical(
                "subscription reconciliation failed for tenant=%s after retries: %s",
                tenant.id,
                exc,
            )

    summary.finished_at = datetime.now(timezone.utc)
    return summary


def reconcile_one(db: Session, tenant: Tenant) -> bool:
    """Reconcile a single tenant against Stripe. Returns True if a
    correction was made, False if already in sync. Raises on unrecoverable
    Stripe API errors (network, 5xx) — caller (reconcile_all) applies
    retry-with-backoff around this call, not inside it, so retry policy is
    isolated from reconciliation logic.
    """
    subs = stripe.Subscription.list(customer=tenant.stripe_customer_id, limit=1, status="all")
    stripe_subs = subs.get("data") if hasattr(subs, "get") else subs["data"]

    existing = subscription_repository.get_subscription_by_stripe_id(
        db, stripe_subs[0]["id"]
    ) if stripe_subs else None
    if existing is None:
        existing = subscription_repository.get_subscription_by_tenant_id(db, tenant.id)

    if not stripe_subs:
        # Stripe has no subscription at all for this customer.
        if existing is not None and existing.status != "canceled":
            subscription_repository.upsert_subscription(
                db,
                tenant_id=tenant.id,
                stripe_subscription_id=existing.stripe_subscription_id,
                status="canceled",
                plan=existing.plan,
                current_period_end=existing.current_period_end,
            )
            tenant_repository.update_tenant_plan(db, tenant.id, "free")
            _write_corrected_audit_log(db, tenant.id, existing.stripe_subscription_id)
            db.commit()
            return True
        db.commit()
        return False

    remote = stripe_subs[0]
    remote_status = remote.get("status") if hasattr(remote, "get") else remote["status"]
    remote_plan = map_status_to_plan(remote_status)
    period_end_ts = remote.get("current_period_end") if hasattr(remote, "get") else remote["current_period_end"]
    remote_period_end = (
        datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if period_end_ts else None
    )
    remote_sub_id = remote.get("id") if hasattr(remote, "get") else remote["id"]

    drift = (
        existing is None
        or existing.status != remote_status
        or existing.plan != remote_plan
        or existing.current_period_end != remote_period_end
    )

    if not drift:
        db.commit()
        return False

    subscription_repository.upsert_subscription(
        db,
        tenant_id=tenant.id,
        stripe_subscription_id=remote_sub_id,
        status=remote_status if remote_status in ("active", "past_due", "canceled") else "active",
        plan=remote_plan,
        current_period_end=remote_period_end,
    )
    tenant_repository.update_tenant_plan(db, tenant.id, remote_plan)
    _write_corrected_audit_log(db, tenant.id, remote_sub_id)
    db.commit()
    return True


def _write_corrected_audit_log(db: Session, tenant_id: str, stripe_subscription_id: str) -> None:
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=None,
            action="subscription.reconciled",
            resource_type="subscription",
            resource_id=stripe_subscription_id,
            decision="allowed",
            reason="drift corrected: local status/plan did not match Stripe",
        )
    )


def _with_retry(fn, *, max_attempts: int = 3, base_delay_s: float = 1.0):
    """Generic retry-with-exponential-backoff wrapper: base_delay_s * 2**n,
    n = 0,1,2 -> 1s, 2s, 4s. Retries only on transient errors
    (stripe.error.APIConnectionError, stripe.error.APIError with
    http_status >= 500); re-raises immediately on
    stripe.error.InvalidRequestError / AuthenticationError (non-retryable —
    bad data or bad creds won't fix itself by waiting).
    """
    attempt = 0
    while True:
        try:
            return fn()
        except (stripe.error.InvalidRequestError, stripe.error.AuthenticationError):
            raise
        except stripe.error.APIConnectionError:
            attempt += 1
            if attempt >= max_attempts:
                raise
            time.sleep(base_delay_s * (2 ** (attempt - 1)))
        except stripe.error.APIError as exc:
            http_status = getattr(exc, "http_status", None)
            if http_status is not None and http_status >= 500:
                attempt += 1
                if attempt >= max_attempts:
                    raise
                time.sleep(base_delay_s * (2 ** (attempt - 1)))
            else:
                raise


if __name__ == "__main__":
    run()
