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
    if request.app.state.job_queue is not None:
        try:
            await request.app.state.job_queue.ping()
        except Exception as exc:
            raise BusinessError("JOB_QUEUE_UNAVAILABLE", "任务队列暂不可用", status_code=503) from exc
    settings = request.app.state.settings
    data = {
        "status": "ready",
        "database": "ok",
        "model_router": "configured" if settings.has_model_router_key() else "missing",
        "chat_fallback": "deepseek" if settings.has_deepseek_key() else "none",
        "product_parse_mode": settings.product_parse_mode,
        "deployment_cell": settings.deployment_cell_code,
        "deployment_region": settings.deployment_region,
    }
    if request.app.state.job_queue is not None:
        data["job_queue"] = "ok"
    return SuccessEnvelope(data=data, request_id=request.state.request_id)
