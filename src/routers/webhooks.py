"""Stripe webhook endpoint.

Public — no auth dependency. Security comes from signature verification, not
a bearer token, per API_CONTRACTS.md.
"""
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.services import stripe_service

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe_service.verify_and_construct_event(payload, sig_header)
    except (stripe.error.SignatureVerificationError, ValueError):
        raise HTTPException(status_code=400, detail="invalid signature")

    stripe_service.handle_webhook_event(db, event)

    return {"received": True}
