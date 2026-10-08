"""Tenant-scoped Sales Forecast V4 endpoints."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from ..auth import AuthenticatedPrincipal, require_permission, require_user
from ..database import bind_tenant_session
from ..dependencies import DatabaseSession, Pagination
from ..schemas import PageData, SuccessEnvelope
from ..schemas.forecasts import (ForecastJobCreateRequest, ForecastJobSummary,
                                 ForecastModelStatus, ForecastResultResponse)
from ..services.forecast_service import ForecastService
from ..services.job_dispatch import enqueue_job

router = APIRouter(prefix="/api/v1", tags=["Sales Forecast"])


def _service(request: Request) -> ForecastService:
    return ForecastService(request.app.state.settings, request.app.state.forecast_runtime)


async def _run_forecast(app, dispatch: dict) -> None:
    try:
        async for session in app.state.database.session():
            await bind_tenant_session(session, dispatch["tenant_id"])
            await ForecastService(app.state.settings, app.state.forecast_runtime).execute(
                session, **dispatch)
    except Exception:
        app.state.logger.exception("forecast_background_job_failed",
                                   extra={"job_uuid": dispatch["job_uuid"]})


@router.get("/forecast/status", response_model=SuccessEnvelope[ForecastModelStatus],
            operation_id="API-FRC-00", summary="查询销量预测模型状态")
async def forecast_status(request: Request, session: DatabaseSession,
                          principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    service = _service(request)
    deployment = await service.repository.active_deployment(
        session, tenant_id=principal.tenant_id)
    if deployment is None:
        metadata = {"enabled": request.app.state.settings.forecast_enabled, "ready": False,
                    "error": "tenant deployment is not configured"}
    else:
        metadata = request.app.state.forecast_runtime.resolve(
            tenant_id=principal.tenant_id, deployment=deployment).metadata()
    return SuccessEnvelope(data=ForecastModelStatus(**metadata),
                           request_id=request.state.request_id)


@router.get("/forecast/skus", operation_id="API-FRC-SKU", summary="查询可预测 SKU 与站点")
async def forecast_skus(request: Request,
                        session: DatabaseSession,
                        principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
                        site: str | None = Query(default=None, min_length=2, max_length=16),
                        limit: int = Query(default=200, ge=1, le=5000)):
    items = await _service(request).repository.list_skus(
        session, tenant_id=principal.tenant_id,
        site=site.upper() if site else None, limit=limit)
    return SuccessEnvelope(data={"items": items}, request_id=request.state.request_id)


@router.post("/forecast-jobs", status_code=201, operation_id="API-FRC-02",
             summary="创建销量预测任务")
async def create_forecast_job(body: ForecastJobCreateRequest, request: Request,
                              session: DatabaseSession,
                              principal: Annotated[AuthenticatedPrincipal, Depends(
                                  require_permission("analysis.execute"))],
                              idempotency_key: Annotated[str, Header(alias="Idempotency-Key",
                                                                     min_length=1, max_length=128)]):
    def envelope(data):
        return SuccessEnvelope(data=ForecastJobSummary(**data),
                               request_id=request.state.request_id).model_dump(mode="json")
    try:
        status, content = await _service(request).create(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            idempotency_key=idempotency_key, payload=body.model_dump(mode="json"),
            response_envelope=envelope)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return JSONResponse(status_code=status, content=content)


@router.post("/forecast-jobs/{job_uuid}:start", status_code=202, operation_id="API-FRC-03",
             summary="启动销量预测任务")
async def start_forecast_job(job_uuid: UUID, request: Request, background_tasks: BackgroundTasks,
                             session: DatabaseSession,
                             principal: Annotated[AuthenticatedPrincipal, Depends(
                                 require_permission("analysis.execute"))],
                             idempotency_key: Annotated[str, Header(alias="Idempotency-Key",
                                                                    min_length=1, max_length=128)]):
    def envelope(data):
        return SuccessEnvelope(data=ForecastJobSummary(**data),
                               request_id=request.state.request_id).model_dump(mode="json")
    try:
        status, content, dispatch = await _service(request).start(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            job_uuid=str(job_uuid), idempotency_key=idempotency_key,
            response_envelope=envelope)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if dispatch:
        if request.app.state.job_queue is not None:
            await enqueue_job(request.app, "forecast", dispatch, job_id=f"forecast:{job_uuid}")
        else:
            background_tasks.add_task(_run_forecast, request.app, dispatch)
    return JSONResponse(status_code=status, content=content)


@router.get("/forecast-jobs", response_model=SuccessEnvelope[PageData[ForecastJobSummary]],
            operation_id="API-FRC-04", summary="查询销量预测历史")
async def list_forecast_jobs(request: Request, session: DatabaseSession, pagination: Pagination,
                             principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
                             q: str | None = Query(default=None, max_length=100),
                             status: Literal["draft", "queued", "running", "succeeded", "failed", "cancelled"] | None = Query(default=None),
                             granularity: Literal["day", "week"] | None = Query(default=None)):
    service = _service(request)
    rows, total = await service.repository.list(
        session, tenant_id=principal.tenant_id, limit=pagination.page_size,
        offset=pagination.offset, q=q, status=status, granularity=granularity)
    page = PageData.build(items=[ForecastJobSummary(**service.projection(row)) for row in rows],
                          total=total, params=pagination)
    return SuccessEnvelope(data=page, request_id=request.state.request_id)


@router.get("/forecast-jobs/{job_uuid}", response_model=SuccessEnvelope[ForecastJobSummary],
            operation_id="API-FRC-05", summary="查询销量预测任务")
async def get_forecast_job(job_uuid: UUID, request: Request, session: DatabaseSession,
                           principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    service = _service(request)
    row = await service.repository.get(session, tenant_id=principal.tenant_id,
                                       job_uuid=str(job_uuid))
    if row is None:
        from ..errors import BusinessError
        raise BusinessError("FORECAST_JOB_NOT_FOUND", "预测任务不存在或不可访问", status_code=404)
    return SuccessEnvelope(data=ForecastJobSummary(**service.projection(row)),
                           request_id=request.state.request_id)


@router.get("/forecast-jobs/{job_uuid}/result",
            response_model=SuccessEnvelope[ForecastResultResponse], operation_id="API-FRC-06",
            summary="查询销量预测结果")
async def get_forecast_result(job_uuid: UUID, request: Request, session: DatabaseSession,
                              principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await _service(request).result(session, tenant_id=principal.tenant_id,
                                          job_uuid=str(job_uuid))
    return SuccessEnvelope(data=ForecastResultResponse(**data), request_id=request.state.request_id)
