"""P0 product endpoints that are not blocked by profile/storage contract drift."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..repositories.product_repository import ProductRepository
from ..schemas import ErrorBody, ErrorEnvelope, PageData, SuccessEnvelope
from ..schemas.products import (ProductCreateRequest, ProductCreateResponse, ProductDetail,
    ProductListItem, ProductProfileConfirmRequest, ProductProfileConfirmResponse,
    ProductUpdateRequest, ProductUpdateResponse)
from ..services.product_service import ProductService
from ..services.parse_service import ParseService
from ..schemas.parse_jobs import ParseAccepted

router = APIRouter(prefix="/api/v1/products", tags=["Products"])


@router.post("", operation_id="API-PRD-01", status_code=201, summary="创建产品")
async def create_product(body: ProductCreateRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
    def envelope(data):
        return SuccessEnvelope(data=ProductCreateResponse(**data), request_id=request.state.request_id).model_dump(mode="json")
    def error_envelope(code, message):
        return ErrorEnvelope(error=ErrorBody(code=code, message=message), request_id=request.state.request_id).model_dump(mode="json")
    try:
        status, response = await ProductService().create(session, tenant_id=principal.tenant_id,
            user_id=principal.user_id, idempotency_key=idempotency_key,
            payload=body.model_dump(), response_envelope=envelope, error_envelope=error_envelope)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return JSONResponse(status_code=status, content=response)


@router.get("", response_model=SuccessEnvelope[PageData[ProductListItem]], operation_id="API-PRD-02", summary="查询产品列表")
async def list_products(request: Request, session: DatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    analysis_status: Annotated[str | None, Query()] = None,
    category_code: Annotated[str | None, Query()] = None,
    keyword: Annotated[str | None, Query(max_length=200)] = None):
    items, total = await ProductRepository().list(session, tenant_id=principal.tenant_id,
        offset=pagination.offset, limit=pagination.page_size, analysis_status=analysis_status,
        category_code=category_code, keyword=keyword)
    data = PageData[ProductListItem].build(items=[ProductListItem(**item) for item in items], total=total, params=pagination)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/{product_id}", response_model=SuccessEnvelope[ProductDetail], operation_id="API-PRD-03", summary="获取产品与画像详情")
async def get_product(product_id: int, request: Request, response: Response, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    profile_version_id: Annotated[int | None, Query()] = None):
    data = await ProductService().detail(session, tenant_id=principal.tenant_id,
        product_id=product_id, profile_version_id=profile_version_id)
    response.headers["ETag"] = data.pop("resource_version")
    return SuccessEnvelope(data=ProductDetail(**data), request_id=request.state.request_id)


@router.patch("/{product_id}", response_model=SuccessEnvelope[ProductUpdateResponse], operation_id="API-PRD-04", summary="更新产品与草稿画像")
async def update_product(product_id: int, body: ProductUpdateRequest, request: Request, response: Response,
    session: DatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    if_match: Annotated[str, Header(alias="If-Match")]):
    try:
        data = await ProductService().update(session, tenant_id=principal.tenant_id,
            product_id=product_id, if_match=if_match, payload=body.model_dump())
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    response.headers["ETag"] = data["resource_version"]
    return SuccessEnvelope(data=ProductUpdateResponse(**data), request_id=request.state.request_id)


@router.post("/{product_id}/profile:confirm", response_model=SuccessEnvelope[ProductProfileConfirmResponse],
             operation_id="API-PRD-07", summary="确认产品画像")
async def confirm_product_profile(product_id: int, body: ProductProfileConfirmRequest, request: Request,
    session: DatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    if_match: Annotated[str, Header(alias="If-Match")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
    def envelope(data):
        return SuccessEnvelope(data=ProductProfileConfirmResponse(**data), request_id=request.state.request_id).model_dump(mode="json")
    try:
        status_code, content = await ProductService().confirm(session, tenant_id=principal.tenant_id,
            user_id=principal.user_id, product_id=product_id, profile_version_id=body.profile_version_id,
            codes=body.confirmed_attribute_codes, if_match=if_match,
            idempotency_key=idempotency_key, response_envelope=envelope)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return JSONResponse(status_code=status_code, content=content)


async def _run_demo_parse(database, settings, tenant_id: int, parse_job_id: str) -> None:
    async with database.session_factory() as session:
        try:
            await ParseService(settings).run_demo_job(session, tenant_id=tenant_id, parse_job_id=parse_job_id)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@router.post("/{product_id}/assets:parse", response_model=SuccessEnvelope[ParseAccepted], status_code=202,
             operation_id="API-PRD-05", summary="上传并解析产品资料")
async def parse_product_assets(product_id: int, request: Request, background_tasks: BackgroundTasks,
    session: DatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    source_type: Annotated[str, Form()], files: Annotated[list[UploadFile], File()],
    parse_config: Annotated[str | None, Form()] = None):
    if source_type not in {"document","image","user_input"}:
        from ..errors import BusinessError
        raise BusinessError("PARSE_CONFIG_INVALID", "source_type无效", status_code=422)
    if parse_config not in {None,"",'{}'}:
        from ..errors import BusinessError
        raise BusinessError("PARSE_CONFIG_INVALID", "P0仅支持空的parse_config", status_code=422)
    uploads=[]
    for upload in files:
        uploads.append((upload.filename or "unnamed", upload.content_type or "application/octet-stream", await upload.read()))
    try:
        def envelope(data):
            return SuccessEnvelope(data=ParseAccepted(**data),request_id=request.state.request_id).model_dump(mode="json")
        status_code,content=await ParseService(request.app.state.settings).accept(session,tenant_id=principal.tenant_id,
            user_id=principal.user_id,product_id=product_id,idempotency_key=idempotency_key,
            source_type=source_type,uploads=uploads,response_envelope=envelope)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    parse_job_id=content["data"]["parse_job_id"]
    background_tasks.add_task(_run_demo_parse, request.app.state.database, request.app.state.settings,
                              principal.tenant_id, parse_job_id)
    return JSONResponse(status_code=status_code,content=content)
