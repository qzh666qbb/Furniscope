"""Liveness and database readiness endpoints."""

from fastapi import APIRouter, Request

from ..errors import BusinessError
from ..schemas import SuccessEnvelope

router = APIRouter(prefix="/health", tags=["Infrastructure"])


@router.get("/live", response_model=SuccessEnvelope[dict[str, str]])
async def liveness(request: Request) -> SuccessEnvelope[dict[str, str]]:
    return SuccessEnvelope(data={"status": "ok"}, request_id=request.state.request_id)


@router.get("/ready", response_model=SuccessEnvelope[dict[str, str]])
async def readiness(request: Request) -> SuccessEnvelope[dict[str, str]]:
    try:
        await request.app.state.database.ping()
    except Exception as exc:
        raise BusinessError("DATABASE_UNAVAILABLE", "数据库暂不可用", status_code=503) from exc
    return SuccessEnvelope(data={"status": "ready", "database": "ok"}, request_id=request.state.request_id)
