"""Tenant-scoped forecast data append and automatic retraining endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession
from ..errors import BusinessError
from ..schemas import SuccessEnvelope
from ..services.forecast_training_service import ForecastTrainingService
from ..services.job_dispatch import enqueue_job

router = APIRouter(prefix="/api/v1/forecast", tags=["Sales Forecast"])


def _service(request: Request) -> ForecastTrainingService:
    return ForecastTrainingService(request.app.state.settings,
                                   request.app.state.forecast_runtime)


async def _run_training(app, dispatch: dict) -> None:
    try:
        async with app.state.database.session_factory() as session:
            await ForecastTrainingService(app.state.settings, app.state.forecast_runtime).execute(
                session, **dispatch)
    except Exception:
        app.state.logger.exception("forecast_training_background_failed",
                                   extra={"training_uuid": dispatch["training_uuid"]})


@router.post("/append", operation_id="API-FRC-07", status_code=202,
             summary="追加销量数据并自动重新训练")
async def append_forecast_data(
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    orders_file: Annotated[UploadFile, File(description="必选的订单 .xlsx 文件")],
    inventory_file: Annotated[UploadFile | None, File(description="可选的库存 .xlsx 文件")] = None,
):
    orders_content = await orders_file.read()
    inventory_content = await inventory_file.read() if inventory_file else None
    try:
        status_code, data = await _service(request).accept(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            idempotency_key=idempotency_key,
            orders_filename=orders_file.filename or "orders.xlsx",
            orders_content=orders_content,
            inventory_filename=inventory_file.filename if inventory_file else None,
            inventory_content=inventory_content)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if status_code == 202:
        dispatch = {"tenant_id": principal.tenant_id,
                    "training_uuid": data["training_uuid"]}
        if request.app.state.job_queue is not None:
            await enqueue_job(request.app, "forecast_training", dispatch,
                              job_id=f"forecast-training:{data['training_uuid']}")
        else:
            background_tasks.add_task(_run_training, request.app, dispatch)
    content = SuccessEnvelope(data=data, request_id=request.state.request_id).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=content)


@router.get("/training-runs", operation_id="API-FRC-08",
            summary="查询销量数据追加历史")
async def list_training_runs(
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    limit: int = Query(default=20, ge=1, le=100),
):
    items = await _service(request).list(session, tenant_id=principal.tenant_id, limit=limit)
    return SuccessEnvelope(data={"items": items}, request_id=request.state.request_id)


@router.get("/training-runs/{training_uuid}", operation_id="API-FRC-09",
            summary="查询销量数据追加任务")
async def get_training_run(
    training_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    data = await _service(request).get(session, tenant_id=principal.tenant_id,
                                       training_uuid=str(training_uuid))
    if data is None:
        raise BusinessError("FORECAST_TRAINING_NOT_FOUND", "追加任务不存在或不可访问",
                            status_code=404)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)
