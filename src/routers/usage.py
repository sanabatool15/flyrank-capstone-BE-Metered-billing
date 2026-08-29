"""Usage router — POST /tenants/{tenant_id}/generate, GET /tenants/{tenant_id}/usage.

No business logic or raw SQL here — only calls into meter_service.
"""
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from src.auth.dependencies import TenantContext, require_permission
from src.db.session import get_db
from src.schemas import GenerateRequest, GenerateResponse, UsageResponse
from src.services import meter_service

router = APIRouter(tags=["usage"])


@router.post("/tenants/{tenant_id}/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_permission("api.use")),
    db: Session = Depends(get_db),
) -> GenerateResponse:
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    result = meter_service.record_usage(db, ctx, body, idempotency_key)
    return GenerateResponse(**result)


@router.get("/tenants/{tenant_id}/usage", response_model=UsageResponse)
async def get_usage(
    ctx: TenantContext = Depends(require_permission("usage.read")),
    db: Session = Depends(get_db),
) -> UsageResponse:
    result = meter_service.get_usage_rollup(db, ctx)
    return UsageResponse(**result)
