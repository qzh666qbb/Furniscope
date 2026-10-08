"""Product master template, preflight and transactional import endpoints."""

from typing import Annotated, Any
import json

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile

from ..auth import AuthenticatedPrincipal, require_permission, require_user
from ..dependencies import DatabaseSession
from ..errors import BusinessError
from ..schemas import SuccessEnvelope
from ..schemas.product_imports import (
    ProductImportCommitRequest,
    ProductImportCommitResponse,
    ProductImportInspection,
    ProductImportJob,
    ProductImportRowPatch,
)
from ..services.product_import_service import (
    ProductImportService,
    TEMPLATE_VERSION,
    build_template,
)


router = APIRouter(prefix="/api/v1/product-imports", tags=["Product Imports"])


def _json_object(raw: str, field_name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise BusinessError(
            "PRODUCT_IMPORT_CONFIG_INVALID",
            f"{field_name} 必须是合法 JSON 对象",
            status_code=422,
        ) from exc
    if not isinstance(value, dict):
        raise BusinessError(
            "PRODUCT_IMPORT_CONFIG_INVALID",
            f"{field_name} 必须是 JSON 对象",
            status_code=422,
        )
    return value


@router.get("/template", operation_id="API-PRD-IMP-01", summary="下载动态产品主档模板")
async def download_product_template(
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    del principal
    content, file_sha, schema_sha = build_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="FurniScope-Product-Master-{TEMPLATE_VERSION}.xlsx"',
            "X-Template-Version": TEMPLATE_VERSION,
            "X-Content-SHA256": file_sha,
            "X-Schema-SHA256": schema_sha,
        },
    )


@router.get("/recent", operation_id="API-PRD-IMP-02", summary="查询最近产品导入任务")
async def recent_product_imports(
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
):
    items = await ProductImportService().recent(
        session, tenant_id=principal.tenant_id, limit=limit
    )
    return SuccessEnvelope(
        data={"items": [ProductImportJob(**item).model_dump(mode="json") for item in items]},
        request_id=request.state.request_id,
    )


@router.post(":inspect", operation_id="API-PRD-IMP-03A", summary="自动识别产品主档结构")
async def inspect_product_import(
    request: Request,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("product.write"))
    ],
    file: Annotated[UploadFile, File()],
):
    del principal
    content = await file.read()
    data = ProductImportService().inspect(
        filename=file.filename or "products.xlsx",
        mime_type=file.content_type or "application/octet-stream",
        content=content,
    )
    return SuccessEnvelope(
        data=ProductImportInspection(**data), request_id=request.state.request_id
    )


@router.post(":preflight", operation_id="API-PRD-IMP-03", summary="上传并预检产品主档")
async def preflight_product_import(
    request: Request, session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("product.write"))
    ],
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ],
    file: Annotated[UploadFile, File()],
    sheet_name: Annotated[str | None, Form()] = None,
    header_row: Annotated[int | None, Form(ge=1, le=100)] = None,
    field_mapping: Annotated[str, Form()] = "{}",
    unit_mapping: Annotated[str, Form()] = "{}",
    dictionary_mapping: Annotated[str, Form()] = "{}",
    import_mode: Annotated[str, Form()] = "create_only",
):
    content = await file.read()
    try:
        data = await ProductImportService().preflight(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            idempotency_key=idempotency_key, filename=file.filename or "products.xlsx",
            mime_type=file.content_type or "application/octet-stream", content=content,
            sheet_name=sheet_name, header_row=header_row,
            field_mapping=_json_object(field_mapping, "field_mapping"),
            unit_mapping={
                str(key): str(value)
                for key, value in _json_object(unit_mapping, "unit_mapping").items()
            },
            dictionary_mapping=_json_object(
                dictionary_mapping, "dictionary_mapping"
            ),
            import_mode=import_mode, request_id=request.state.request_id,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(
        data=ProductImportJob(**data), request_id=request.state.request_id
    )


@router.get("/{job_uuid}", operation_id="API-PRD-IMP-04", summary="查询产品导入任务")
async def get_product_import(
    job_uuid: str, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    row_limit: Annotated[int, Query(ge=1, le=2000)] = 500,
):
    data = await ProductImportService().job(
        session, tenant_id=principal.tenant_id, job_uuid=job_uuid,
        row_limit=row_limit,
    )
    return SuccessEnvelope(
        data=ProductImportJob(**data), request_id=request.state.request_id
    )


@router.patch(
    "/{job_uuid}/rows/{source_row_number}",
    operation_id="API-PRD-IMP-05",
    summary="修正产品导入预检行",
)
async def patch_product_import_row(
    job_uuid: str, source_row_number: int, body: ProductImportRowPatch,
    request: Request, session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("product.write"))
    ],
):
    try:
        data = await ProductImportService().patch_row(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            job_uuid=job_uuid, source_row_number=source_row_number,
            normalized_values=body.normalized_values,
            request_id=request.state.request_id,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(
        data=ProductImportJob(**data), request_id=request.state.request_id
    )


@router.post(
    "/{job_uuid}:commit",
    operation_id="API-PRD-IMP-06",
    summary="事务提交产品导入",
)
async def commit_product_import(
    job_uuid: str, body: ProductImportCommitRequest, request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("product.write"))
    ],
):
    try:
        data = await ProductImportService().commit(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            job_uuid=job_uuid, preview_sha256=body.preview_sha256,
            request_id=request.state.request_id,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(
        data=ProductImportCommitResponse(**data), request_id=request.state.request_id
    )


@router.post(
    "/{job_uuid}:cancel",
    operation_id="API-PRD-IMP-07",
    summary="取消产品导入任务",
)
async def cancel_product_import(
    job_uuid: str, request: Request, session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("product.write"))
    ],
):
    try:
        data = await ProductImportService().cancel(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            job_uuid=job_uuid, request_id=request.state.request_id,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(
        data=ProductImportJob(**data), request_id=request.state.request_id
    )


@router.get(
    "/{job_uuid}/error-report",
    operation_id="API-PRD-IMP-08",
    summary="下载产品导入错误 Excel",
)
async def download_product_import_errors(
    job_uuid: str, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    content = await ProductImportService().error_report(
        session, tenant_id=principal.tenant_id, job_uuid=job_uuid
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="product-import-errors-{job_uuid}.xlsx"',
            "X-Content-SHA256": __import__("hashlib").sha256(content).hexdigest(),
        },
    )
