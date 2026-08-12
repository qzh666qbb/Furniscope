"""P0 market dataset read endpoints."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..errors import BusinessError
from ..repositories.dataset_repository import DatasetRepository
from ..schemas import PageData, SuccessEnvelope
from ..schemas.datasets import DatasetCreateRequest, DatasetCreateResponse, DatasetDetail, DatasetImportAccepted, DatasetListItem
from ..services.dataset_service import DatasetService
from ..services.dataset_import_service import DatasetImportService

router = APIRouter(prefix="/api/v1/market-datasets", tags=["Market Datasets"])


@router.post("", operation_id="API-DAT-01", status_code=201, summary="创建授权数据集")
async def create_dataset(body: DatasetCreateRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
    def envelope(data):
        return SuccessEnvelope(data=DatasetCreateResponse(**data), request_id=request.state.request_id).model_dump(mode="json")
    try:
        status_code, content = await DatasetService().create(session, tenant_id=principal.tenant_id,
            user_id=principal.user_id, idempotency_key=idempotency_key,
            payload=body.model_dump(mode="python"), response_envelope=envelope)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return JSONResponse(status_code=status_code, content=content)


async def _run_demo_import(database,settings,tenant_id:int,dataset_id:int,content:bytes)->None:
    async with database.session_factory() as session:
        try:
            await DatasetImportService(settings).run_demo(session,tenant_id=tenant_id,dataset_id=dataset_id,content=content)
            await session.commit()
        except Exception:
            await session.rollback()
            async with database.session_factory() as failure_session:
                from sqlalchemy import text
                await failure_session.execute(text("""
                    UPDATE market_datasets SET status='rejected',limitations='["demo_import_failed"]'::jsonb
                     WHERE id=:dataset AND tenant_id=:tenant
                """),{"dataset":dataset_id,"tenant":tenant_id})
                await failure_session.commit()


@router.post("/{dataset_id}/imports",operation_id="API-DAT-02",status_code=202,summary="导入竞品和评论")
async def import_dataset(dataset_id:int,request:Request,background_tasks:BackgroundTasks,session:DatabaseSession,
    principal:Annotated[AuthenticatedPrincipal,Depends(require_user)],
    idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)],
    deduplication_strategy:Annotated[str,Form()],files:Annotated[list[UploadFile],File()],
    field_mapping:Annotated[str|None,Form()]=None):
    if field_mapping not in {None,"",'[]'}:
        raise BusinessError("FIELD_MAPPING_INVALID","P0导入使用数据集已确认字段映射",status_code=422)
    if len(files)!=1: raise BusinessError("DATASET_FILE_INVALID","P0每次仅导入一个JSON文件",status_code=422)
    upload=files[0]; content=await upload.read()
    def envelope(data):
        return SuccessEnvelope(data=DatasetImportAccepted(**data),request_id=request.state.request_id).model_dump(mode="json")
    try:
        status_code,response=await DatasetImportService(request.app.state.settings).accept(session,
            tenant_id=principal.tenant_id,user_id=principal.user_id,dataset_id=dataset_id,
            idempotency_key=idempotency_key,deduplication_strategy=deduplication_strategy,
            filename=upload.filename or "dataset.json",mime=upload.content_type or "application/octet-stream",
            content=content,response_envelope=envelope)
        await session.commit()
    except Exception:
        await session.rollback(); raise
    background_tasks.add_task(_run_demo_import,request.app.state.database,request.app.state.settings,
                              principal.tenant_id,dataset_id,content)
    return JSONResponse(status_code=status_code,content=response)


@router.get("", response_model=SuccessEnvelope[PageData[DatasetListItem]], operation_id="API-DAT-03", summary="查询数据集列表")
async def list_datasets(request: Request, session: DatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    platform: Annotated[str | None, Query()] = None,
    market_country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    category_code: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None):
    items, total = await DatasetRepository().list(session, tenant_id=principal.tenant_id,
        offset=pagination.offset, limit=pagination.page_size, platform=platform,
        market_country=market_country, category_code=category_code, status=status)
    data = PageData[DatasetListItem].build(items=[DatasetListItem(**item) for item in items], total=total, params=pagination)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/{dataset_id}", response_model=SuccessEnvelope[DatasetDetail], operation_id="API-DAT-04", summary="获取数据集质量与范围")
async def get_dataset(dataset_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    row = await DatasetRepository().get(session, tenant_id=principal.tenant_id, dataset_id=dataset_id)
    if row is None:
        raise BusinessError("DATASET_NOT_FOUND", "数据集不存在或不可访问", status_code=404)
    return SuccessEnvelope(data=DatasetDetail(**row), request_id=request.state.request_id)
