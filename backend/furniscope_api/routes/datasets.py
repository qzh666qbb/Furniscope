"""P0 market dataset read endpoints."""

from typing import Annotated
from io import BytesIO

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from ..auth import AuthenticatedPrincipal, require_permission, require_user
from ..database import bind_tenant_session
from ..dependencies import DatabaseSession, Pagination
from ..errors import BusinessError
from ..repositories.dataset_repository import DatasetRepository
from ..schemas import PageData, SuccessEnvelope
from ..schemas.datasets import DatasetBoundTask, DatasetCreateRequest, DatasetCreateResponse, DatasetDetail, DatasetImportAccepted, DatasetListItem, DatasetListingPreview, DatasetReviewPreview
from ..services.dataset_service import DatasetService
from ..services.dataset_import_service import DatasetImportService
from ..services.job_dispatch import enqueue_job

router = APIRouter(prefix="/api/v1/market-datasets", tags=["Market Datasets"])


@router.post("", operation_id="API-DAT-01", status_code=201, summary="创建授权数据集")
async def create_dataset(body: DatasetCreateRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("dataset.write"))],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
    def envelope(data):
        return SuccessEnvelope(data=DatasetCreateResponse(**data), request_id=request.state.request_id).model_dump(mode="json")
    try:
        status_code, content = await DatasetService().create(session, tenant_id=principal.tenant_id,
            user_id=principal.user_id, idempotency_key=idempotency_key,
            payload=body.model_dump(mode="python"), response_envelope=envelope,
            request_id=request.state.request_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return JSONResponse(status_code=status_code, content=content)


async def _run_dataset_import(database,settings,tenant_id:int,dataset_id:int,content:bytes,
                              filename:str,mime:str)->None:
    async with database.session_factory() as session:
        await bind_tenant_session(session, tenant_id)
        try:
            await DatasetImportService(settings).run_import(session,tenant_id=tenant_id,dataset_id=dataset_id,
                                                            content=content,filename=filename,mime=mime)
            await session.commit()
        except Exception:
            await session.rollback()
            async with database.session_factory() as failure_session:
                from sqlalchemy import text
                await bind_tenant_session(failure_session, tenant_id)
                await failure_session.execute(text("""
                    UPDATE market_datasets SET status='rejected',limitations='["market_data_import_failed"]'::jsonb
                     WHERE id=:dataset AND tenant_id=:tenant
                """),{"dataset":dataset_id,"tenant":tenant_id})
                await failure_session.commit()


@router.post("/{dataset_id}/imports",operation_id="API-DAT-02",status_code=202,summary="导入竞品和评论")
async def import_dataset(dataset_id:int,request:Request,background_tasks:BackgroundTasks,session:DatabaseSession,
    principal:Annotated[AuthenticatedPrincipal,Depends(require_permission("dataset.write"))],
    idempotency_key:Annotated[str,Header(alias="Idempotency-Key",min_length=1,max_length=128)],
    deduplication_strategy:Annotated[str,Form()],files:Annotated[list[UploadFile],File()],
    field_mapping:Annotated[str|None,Form()]=None):
    if field_mapping not in {None,"",'[]'}:
        raise BusinessError("FIELD_MAPPING_INVALID","P0导入使用数据集已确认字段映射",status_code=422)
    if len(files)!=1: raise BusinessError("DATASET_FILE_INVALID","每次请选择一份市场数据工作簿",status_code=422)
    upload=files[0]; content=await upload.read()
    def envelope(data):
        return SuccessEnvelope(data=DatasetImportAccepted(**data),request_id=request.state.request_id).model_dump(mode="json")
    try:
        status_code,response=await DatasetImportService(request.app.state.settings).accept(session,
            tenant_id=principal.tenant_id,user_id=principal.user_id,dataset_id=dataset_id,
            idempotency_key=idempotency_key,deduplication_strategy=deduplication_strategy,
            filename=upload.filename or "market-data.xlsx",mime=upload.content_type or "application/octet-stream",
            content=content,response_envelope=envelope,request_id=request.state.request_id)
        await session.commit()
    except Exception:
        await session.rollback(); raise
    if request.app.state.job_queue is not None:
        await enqueue_job(request.app, "dataset_import",
                          {"tenant_id": principal.tenant_id, "dataset_id": dataset_id},
                          job_id=f"dataset:{dataset_id}")
    else:
        background_tasks.add_task(_run_dataset_import,request.app.state.database,request.app.state.settings,
                                  principal.tenant_id,dataset_id,content,upload.filename or "market-data.xlsx",
                                  upload.content_type or "application/octet-stream")
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


@router.get("/import-template", operation_id="API-DAT-05", summary="下载市场数据填写模板")
async def download_import_template():
    workbook = Workbook()
    products = workbook.active
    products.title = "商品信息"
    products.append(["商品编号", "商品标题", "品牌", "品类", "目标市场", "售价", "划线价", "币种", "评分", "评论数", "上架日期", "采集时间"])
    reviews = workbook.create_sheet("消费者评价")
    reviews.append(["商品编号", "评价编号", "评分", "评价内容", "语言", "用户属地", "是否已购买", "评价时间"])
    instructions = workbook.create_sheet("填写说明", 0)
    instructions.append(["工作表", "填写要求"])
    instructions.append(["商品信息", "每行一个市场商品；商品编号、商品标题、售价、币种和采集时间必填。"])
    instructions.append(["消费者评价", "每行一条消费者评价；商品编号须与商品信息一致，评价编号和评价内容必填。"])
    instructions.append(["时间格式", "建议填写为 2026-09-01 这样的日期，或包含具体时间。"])
    header_fill = PatternFill("solid", fgColor="0B8883")
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for cell in sheet[1]:
            cell.font = Font(color="FFFFFF", bold=True)
            cell.fill = header_fill
        for column in sheet.columns:
            sheet.column_dimensions[column[0].column_letter].width = min(42, max(14, max(len(str(cell.value or "")) for cell in column) + 3))
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=FurniScope_market_data_template.xlsx"},
    )


@router.get("/{dataset_id}", response_model=SuccessEnvelope[DatasetDetail], operation_id="API-DAT-04", summary="获取数据集质量与范围")
async def get_dataset(dataset_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    row = await DatasetRepository().get(session, tenant_id=principal.tenant_id, dataset_id=dataset_id)
    if row is None:
        raise BusinessError("DATASET_NOT_FOUND", "数据集不存在或不可访问", status_code=404)
    return SuccessEnvelope(data=DatasetDetail(**row), request_id=request.state.request_id)


@router.get("/{dataset_id}/listings", response_model=SuccessEnvelope[PageData[DatasetListingPreview]], summary="预览竞品商品数据")
async def list_dataset_listings(dataset_id: int, request: Request, session: DatabaseSession,
    pagination: Pagination, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    market_country: Annotated[str | None, Query(min_length=2, max_length=2)] = None):
    repository = DatasetRepository()
    if await repository.get(session, tenant_id=principal.tenant_id, dataset_id=dataset_id) is None:
        raise BusinessError("DATASET_NOT_FOUND", "数据集不存在或不可访问", status_code=404)
    items, total = await repository.list_listings(session, tenant_id=principal.tenant_id,
        dataset_id=dataset_id, offset=pagination.offset, limit=pagination.page_size, query=q,
        market_country=market_country)
    data = PageData[DatasetListingPreview].build(items=[DatasetListingPreview(**item) for item in items], total=total, params=pagination)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/{dataset_id}/reviews", response_model=SuccessEnvelope[PageData[DatasetReviewPreview]], summary="预览海外评论原文")
async def list_dataset_reviews(dataset_id: int, request: Request, session: DatabaseSession,
    pagination: Pagination, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    sentiment: Annotated[str | None, Query(pattern="^(positive|neutral|negative)$")] = None):
    repository = DatasetRepository()
    if await repository.get(session, tenant_id=principal.tenant_id, dataset_id=dataset_id) is None:
        raise BusinessError("DATASET_NOT_FOUND", "数据集不存在或不可访问", status_code=404)
    items, total = await repository.list_reviews(session, tenant_id=principal.tenant_id,
        dataset_id=dataset_id, offset=pagination.offset, limit=pagination.page_size,
        query=q, sentiment=sentiment)
    data = PageData[DatasetReviewPreview].build(items=[DatasetReviewPreview(**item) for item in items], total=total, params=pagination)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/{dataset_id}/analysis-tasks", response_model=SuccessEnvelope[list[DatasetBoundTask]],
            summary="查询复用该数据集的分析任务")
async def list_dataset_bound_tasks(dataset_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    repository = DatasetRepository()
    if await repository.get(session, tenant_id=principal.tenant_id, dataset_id=dataset_id) is None:
        raise BusinessError("DATASET_NOT_FOUND", "数据集不存在或不可访问", status_code=404)
    items = await repository.list_bound_tasks(session, tenant_id=principal.tenant_id, dataset_id=dataset_id)
    return SuccessEnvelope(data=[DatasetBoundTask(**item) for item in items], request_id=request.state.request_id)


@router.delete("/{dataset_id}", response_model=SuccessEnvelope[dict], summary="软删除市场数据集")
async def delete_dataset(dataset_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("dataset.write"))]):
    deleted = await DatasetRepository().soft_delete(session, tenant_id=principal.tenant_id, dataset_id=dataset_id)
    if not deleted:
        raise BusinessError("DATASET_NOT_FOUND", "数据集不存在或不可访问", status_code=404)
    await session.commit()
    return SuccessEnvelope(data={"dataset_id": dataset_id, "deleted": True}, request_id=request.state.request_id)
