"""Auth router — POST /auth/sync only. No business logic here; the upsert
is trivial enough that AUTHZ_DESIGN.md/API_CONTRACTS.md do not require a
dedicated service module for it, but we still go through a tiny service-like
function to keep routers free of raw SQL / session queries.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.db.session import get_db
from src.models.db_models import User
from src.schemas import AuthSyncRequest, AuthSyncResponse

router = APIRouter(tags=["auth"])


@router.post("/auth/sync", response_model=AuthSyncResponse)
async def sync_user(body: AuthSyncRequest, db: Session = Depends(get_db)) -> AuthSyncResponse:
    user = db.query(User).filter(User.auth_provider_id == body.auth_provider_id).first()
    if user:
        user.email = body.email
        user.last_login_at = datetime.utcnow()
    else:
        user = User(
            auth_provider_id=body.auth_provider_id,
            email=body.email,
            last_login_at=datetime.utcnow(),
        )
        db.add(user)
    db.commit()
    db.refresh(user)
    return AuthSyncResponse(user_id=user.id, email=user.email)
