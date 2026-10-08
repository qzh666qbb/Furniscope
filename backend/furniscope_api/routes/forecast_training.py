"""Tenant-scoped forecast data append and automatic retraining endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from ..auth import AuthenticatedPrincipal, require_permission, require_user
from ..database import bind_tenant_session
from ..dependencies import DatabaseSession
from ..errors import BusinessError
from ..schemas import SuccessEnvelope
from ..schemas.data_imports import (ImportConfirmRequest, ImportPreflightRequest,
                                   TrainingCreateRequest, SkuMappingSave,
                                   ImportTemplateSave, ImportTemplateApply)
from ..services.forecast_catalog import ForecastCatalog
from ..services.forecast_data_service import ForecastDataService
from ..services.data_query import DataQueryService
from ..services.forecast_training_service import ForecastTrainingService
from ..services.job_dispatch import enqueue_job

router = APIRouter(prefix="/api/v1/forecast", tags=["Sales Forecast"])


@router.get("/import-templates", summary="读取本企业的可复用导入模板")
async def list_import_templates(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    items = await ForecastDataService(request.app.state.settings).templates(session, principal.tenant_id)
    return SuccessEnvelope(data={"items": items}, request_id=request.state.request_id)


@router.post("/import-templates", summary="从已确认数据保存不可变模板修订")
async def save_import_template(body: ImportTemplateSave, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("dataset.write"))]):
    data = await ForecastDataService(request.app.state.settings).save_template(
        session, principal.tenant_id, principal.user_id, body)
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/data-imports/{version_uuid}/apply-template", summary="复用企业模板并使旧预检失效")
async def apply_import_template(version_uuid: UUID, body: ImportTemplateApply, request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("dataset.write"))]):
    data = await ForecastDataService(request.app.state.settings).apply_template(
        session, principal.tenant_id, str(version_uuid), str(body.template_uuid))
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/sku-mappings", summary="读取企业训练SKU映射")
async def get_sku_mappings(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await ForecastCatalog().configuration(session, principal.tenant_id)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.put("/sku-mappings", summary="维护下一次训练使用的SKU映射")
async def save_sku_mappings(body: SkuMappingSave, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("forecast.train"))]):
    data = await ForecastCatalog().save(session, principal.tenant_id, principal.user_id, body)
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


def _service(request: Request) -> ForecastTrainingService:
    return ForecastTrainingService(request.app.state.settings,
                                   request.app.state.forecast_runtime)


async def _run_training(app, dispatch: dict) -> None:
    try:
        async with app.state.database.session_factory() as session:
            await bind_tenant_session(session, dispatch["tenant_id"])
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
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("forecast.train"))],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    orders_file: Annotated[UploadFile, File(description="必选的每日汇总销量 .xlsx 文件")],
    inventory_file: Annotated[UploadFile | None, File(description="可选的库存 .xlsx 文件")] = None,
    allow_history_overwrite: Annotated[
        bool, Form(description="已查看覆盖规则并确认同键历史由本次上传替换")
    ] = False,
):
    max_bytes = request.app.state.settings.upload_max_bytes + 1
    orders_content = await orders_file.read(max_bytes)
    inventory_content = await inventory_file.read(max_bytes) if inventory_file else None
    try:
        status_code, data = await _service(request).accept(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            idempotency_key=idempotency_key,
            orders_filename=orders_file.filename or "orders.xlsx",
            orders_content=orders_content,
            inventory_filename=inventory_file.filename if inventory_file else None,
            inventory_content=inventory_content,
            allow_history_overwrite=allow_history_overwrite)
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


@router.post("/data-imports", summary="上传企业原始数据并建议字段映射")
async def upload_data(
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("dataset.write"))],
    file: Annotated[UploadFile, File()],
):
    content = await file.read(request.app.state.settings.upload_max_bytes + 1)
    service = ForecastDataService(request.app.state.settings)
    data = await service.upload(session, tenant_id=principal.tenant_id, user_id=principal.user_id,
                                 filename=file.filename or "data.csv", content=content)
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/data-imports", summary="查询企业数据版本")
async def list_data(
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    limit: int = Query(default=20, ge=1, le=100),
):
    items = await ForecastDataService(request.app.state.settings).list(
        session, tenant_id=principal.tenant_id, limit=limit)
    return SuccessEnvelope(data={"items": items}, request_id=request.state.request_id)


@router.get("/data-imports/{version_uuid}", summary="查询数据质量与标准样本")
async def get_data(
    version_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    import json
    service = ForecastDataService(request.app.state.settings)
    row = await service.row(session, principal.tenant_id, str(version_uuid))
    data = service.projection(row)
    if row["canonical_storage_key"]:
        payload = json.loads(service.read_verified(principal.tenant_id,
                                                  row["canonical_storage_key"], row["canonical_sha256"]))
        data["sample"] = payload["records"][:20]
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/data-imports/{version_uuid}/audit", summary="下载完整行级转换记录")
async def get_data_audit(
    version_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    from fastapi.responses import Response
    service = ForecastDataService(request.app.state.settings)
    row = await service.row(session, principal.tenant_id, str(version_uuid))
    if not row["canonical_storage_key"]:
        raise BusinessError("DATA_PREVIEW_REQUIRED", "请先执行数据预检", status_code=409)
    payload = service.read_verified(principal.tenant_id, row["canonical_storage_key"],
                                   row["canonical_sha256"])
    return Response(payload, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="audit-{version_uuid}.json"',
                             "Cache-Control": "private, no-store"})


@router.post("/data-imports/{version_uuid}/mapping-suggestion", summary="建议未知列映射")
async def suggest_data_mapping(
    version_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    data = await ForecastDataService(request.app.state.settings).mapping_suggestion(
        session, tenant_id=principal.tenant_id, version_uuid=str(version_uuid))
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/data-imports/{version_uuid}/preflight", summary="按确认的字段与口径预检")
async def preflight_data(
    version_uuid: UUID, body: ImportPreflightRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("dataset.write"))],
):
    data = await ForecastDataService(request.app.state.settings).preflight(
        session, tenant_id=principal.tenant_id, version_uuid=str(version_uuid), rules=body.rules,
        auxiliary_versions=body.auxiliary_versions)
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/data-imports/{version_uuid}/confirm", summary="确认不可变标准数据版本")
async def confirm_data(
    version_uuid: UUID, body: ImportConfirmRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("dataset.write"))],
):
    data = await ForecastDataService(request.app.state.settings).confirm(
        session, tenant_id=principal.tenant_id, version_uuid=str(version_uuid),
        preview_sha256=body.preview_sha256)
    await DataQueryService(request.app.state.settings).project_version(
        session,
        tenant_id=principal.tenant_id,
        version_uuid=str(version_uuid),
    )
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/data-imports/{version_uuid}/revisions", summary="保留原始文件并创建新的清洗版本")
async def revise_data(
    version_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("dataset.write"))],
):
    data = await ForecastDataService(request.app.state.settings).revise(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id, version_uuid=str(version_uuid))
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/training-preview", summary="预览首次建模或历史数据合并")
async def preview_training(
    body: TrainingCreateRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    data = await _service(request).preview(session, tenant_id=principal.tenant_id,
                                           version_uuid=str(body.data_version_uuid), mode=body.mode)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/training-runs", status_code=202, summary="以已确认企业数据创建训练任务")
async def create_training(
    body: TrainingCreateRequest, request: Request, background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("forecast.train"))],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    status_code, data = await _service(request).create(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id,
        version_uuid=str(body.data_version_uuid), mode=body.mode,
        allow_history_overwrite=body.allow_history_overwrite, idempotency_key=idempotency_key)
    await session.commit()
    if status_code == 202:
        dispatch = {"tenant_id": principal.tenant_id, "training_uuid": data["training_uuid"]}
        if request.app.state.job_queue is not None:
            await enqueue_job(request.app, "forecast_training", dispatch,
                              job_id=f"forecast-training:{data['training_uuid']}")
        else:
            background_tasks.add_task(_run_training, request.app, dispatch)
    return JSONResponse(status_code=status_code, content=SuccessEnvelope(
        data=data, request_id=request.state.request_id).model_dump(mode="json"))
