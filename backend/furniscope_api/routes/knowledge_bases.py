"""Tenant knowledge-base management, indexing, and citation-ready search."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile

from ..auth import AuthenticatedPrincipal, require_permission
from ..dependencies import DatabaseSession, Pagination
from ..errors import BusinessError
from ..repositories.knowledge_repository import KnowledgeRepository
from ..repositories.workspace_repository import WorkspaceRepository
from ..schemas import PageData, SuccessEnvelope
from ..schemas.context_lifecycle import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseItem,
    KnowledgeBasePatchRequest,
    KnowledgeDocumentItem,
    KnowledgeDocumentPreview,
    KnowledgeDocumentVersionItem,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from ..services.job_dispatch import enqueue_job
from ..services.knowledge_service import KnowledgeService, run_knowledge_index_job

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["Knowledge Bases"])


async def _base(
    session,
    *,
    tenant_id: int,
    user_id: int,
    knowledge_base_uuid: UUID,
) -> dict:
    value = await KnowledgeRepository().get_base(
        session,
        tenant_id=tenant_id,
        knowledge_base_uuid=str(knowledge_base_uuid),
        user_id=user_id,
    )
    if value is None:
        raise BusinessError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在或已归档", status_code=404)
    return value


async def _document(session, *, tenant_id: int, base_id: int, document_uuid: UUID) -> dict:
    value = await KnowledgeRepository().get_document(
        session,
        tenant_id=tenant_id,
        knowledge_base_id=base_id,
        document_uuid=str(document_uuid),
    )
    if value is None:
        raise BusinessError("KNOWLEDGE_DOCUMENT_NOT_FOUND", "知识文档不存在或已删除", status_code=404)
    return value


async def _dispatch_index_job(
    request: Request,
    background_tasks: BackgroundTasks,
    *,
    tenant_id: int,
    index_job_uuid: str,
) -> None:
    payload = {"tenant_id": tenant_id, "index_job_uuid": index_job_uuid}
    if request.app.state.job_queue is not None:
        await enqueue_job(
            request.app,
            "knowledge_index",
            payload,
            job_id=f"knowledge-index:{index_job_uuid}",
        )
        return
    background_tasks.add_task(
        run_knowledge_index_job,
        request.app.state.settings,
        request.app.state.database.session_factory,
        **payload,
    )


@router.post("", response_model=SuccessEnvelope[KnowledgeBaseItem], status_code=201)
async def create_knowledge_base(
    body: KnowledgeBaseCreateRequest,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.write")),
    ],
):
    item = await KnowledgeRepository().create_base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        name=body.name,
        description=body.description,
        visibility=body.visibility,
    )
    await session.commit()
    return SuccessEnvelope(data=KnowledgeBaseItem(**item), request_id=request.state.request_id)


@router.get("", response_model=SuccessEnvelope[PageData[KnowledgeBaseItem]])
async def list_knowledge_bases(
    request: Request,
    session: DatabaseSession,
    pagination: Pagination,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.read")),
    ],
):
    rows, total = await KnowledgeRepository().list_bases(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    data = PageData[KnowledgeBaseItem].build(
        items=[KnowledgeBaseItem(**row) for row in rows],
        total=total,
        params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/{knowledge_base_uuid}", response_model=SuccessEnvelope[KnowledgeBaseItem])
async def get_knowledge_base(
    knowledge_base_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.read")),
    ],
):
    item = await _base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=knowledge_base_uuid,
    )
    return SuccessEnvelope(data=KnowledgeBaseItem(**item), request_id=request.state.request_id)


@router.patch("/{knowledge_base_uuid}", response_model=SuccessEnvelope[KnowledgeBaseItem])
async def patch_knowledge_base(
    knowledge_base_uuid: UUID,
    body: KnowledgeBasePatchRequest,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.write")),
    ],
):
    item = await KnowledgeRepository().patch_base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=str(knowledge_base_uuid),
        name=body.name,
        description=body.description,
        visibility=body.visibility,
        fields_set=body.model_fields_set,
    )
    if item is None:
        raise BusinessError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在或已归档", status_code=404)
    await session.commit()
    return SuccessEnvelope(data=KnowledgeBaseItem(**item), request_id=request.state.request_id)


@router.delete("/{knowledge_base_uuid}", response_model=SuccessEnvelope[dict])
async def delete_knowledge_base(
    knowledge_base_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.write")),
    ],
):
    archived = await KnowledgeRepository().archive_base(
        session,
        tenant_id=principal.tenant_id,
        knowledge_base_uuid=str(knowledge_base_uuid),
        user_id=principal.user_id,
    )
    await session.commit()
    if not archived:
        raise BusinessError("KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在或已归档", status_code=404)
    return SuccessEnvelope(
        data={"knowledge_base_uuid": str(knowledge_base_uuid), "archived": True},
        request_id=request.state.request_id,
    )


@router.post(
    "/{knowledge_base_uuid}/documents",
    response_model=SuccessEnvelope[KnowledgeDocumentItem],
    status_code=202,
)
async def upload_knowledge_document(
    knowledge_base_uuid: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.write")),
    ],
    file: Annotated[UploadFile, File()],
    document_type: Annotated[str | None, Form(max_length=64)] = None,
):
    filename = Path(file.filename or "").name
    if not filename or len(filename) > 500:
        raise BusinessError("KNOWLEDGE_FILENAME_INVALID", "文件名为空或过长", status_code=422)
    content = await file.read(request.app.state.settings.upload_max_bytes + 1)
    item = await KnowledgeService(request.app.state.settings).queue_document(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=str(knowledge_base_uuid),
        filename=filename,
        mime_type=file.content_type or "application/octet-stream",
        content=content,
        document_type=document_type,
    )
    await session.commit()
    await _dispatch_index_job(
        request,
        background_tasks,
        tenant_id=principal.tenant_id,
        index_job_uuid=str(item["index_job_uuid"]),
    )
    return SuccessEnvelope(data=KnowledgeDocumentItem(**item), request_id=request.state.request_id)


@router.get(
    "/{knowledge_base_uuid}/documents",
    response_model=SuccessEnvelope[PageData[KnowledgeDocumentItem]],
)
async def list_knowledge_documents(
    knowledge_base_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    pagination: Pagination,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.read")),
    ],
):
    base = await _base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=knowledge_base_uuid,
    )
    rows, total = await KnowledgeRepository().list_documents(
        session,
        tenant_id=principal.tenant_id,
        knowledge_base_id=int(base["id"]),
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    data = PageData[KnowledgeDocumentItem].build(
        items=[KnowledgeDocumentItem(**row) for row in rows],
        total=total,
        params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get(
    "/{knowledge_base_uuid}/documents/{document_uuid}",
    response_model=SuccessEnvelope[KnowledgeDocumentItem],
)
async def get_knowledge_document(
    knowledge_base_uuid: UUID,
    document_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.read")),
    ],
):
    base = await _base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=knowledge_base_uuid,
    )
    item = await _document(
        session,
        tenant_id=principal.tenant_id,
        base_id=int(base["id"]),
        document_uuid=document_uuid,
    )
    return SuccessEnvelope(data=KnowledgeDocumentItem(**item), request_id=request.state.request_id)


@router.get(
    "/{knowledge_base_uuid}/documents/{document_uuid}/status",
    response_model=SuccessEnvelope[KnowledgeDocumentItem],
)
async def get_knowledge_document_status(
    knowledge_base_uuid: UUID,
    document_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.read")),
    ],
):
    return await get_knowledge_document(
        knowledge_base_uuid,
        document_uuid,
        request,
        session,
        principal,
    )


@router.post(
    "/{knowledge_base_uuid}/documents/{document_uuid}/versions",
    response_model=SuccessEnvelope[KnowledgeDocumentItem],
    status_code=202,
)
async def upload_knowledge_document_version(
    knowledge_base_uuid: UUID,
    document_uuid: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.write")),
    ],
    file: Annotated[UploadFile, File()],
):
    base = await _base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=knowledge_base_uuid,
    )
    document = await _document(
        session,
        tenant_id=principal.tenant_id,
        base_id=int(base["id"]),
        document_uuid=document_uuid,
    )
    filename = Path(file.filename or "").name
    if not filename or len(filename) > 500:
        raise BusinessError("KNOWLEDGE_FILENAME_INVALID", "文件名为空或过长", status_code=422)
    mime_type = file.content_type or "application/octet-stream"
    if (
        mime_type != document["mime_type"]
        or Path(filename).suffix.lower() != Path(document["filename"]).suffix.lower()
    ):
        raise BusinessError(
            "KNOWLEDGE_VERSION_TYPE_MISMATCH",
            "新版本必须与当前文档保持相同文件类型",
            status_code=422,
        )
    content = await file.read(request.app.state.settings.upload_max_bytes + 1)
    item = await KnowledgeService(request.app.state.settings).queue_document(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=str(knowledge_base_uuid),
        filename=filename,
        mime_type=mime_type,
        content=content,
        document_type=document["document_type"],
        document_id=int(document["id"]),
    )
    await session.commit()
    await _dispatch_index_job(
        request,
        background_tasks,
        tenant_id=principal.tenant_id,
        index_job_uuid=str(item["index_job_uuid"]),
    )
    return SuccessEnvelope(data=KnowledgeDocumentItem(**item), request_id=request.state.request_id)


@router.get(
    "/{knowledge_base_uuid}/documents/{document_uuid}/versions",
    response_model=SuccessEnvelope[PageData[KnowledgeDocumentVersionItem]],
)
async def list_knowledge_document_versions(
    knowledge_base_uuid: UUID,
    document_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    pagination: Pagination,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.read")),
    ],
):
    base = await _base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=knowledge_base_uuid,
    )
    await _document(
        session,
        tenant_id=principal.tenant_id,
        base_id=int(base["id"]),
        document_uuid=document_uuid,
    )
    rows, total = await KnowledgeRepository().list_document_versions(
        session,
        tenant_id=principal.tenant_id,
        knowledge_base_id=int(base["id"]),
        document_uuid=str(document_uuid),
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    data = PageData[KnowledgeDocumentVersionItem].build(
        items=[KnowledgeDocumentVersionItem(**row) for row in rows],
        total=total,
        params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get(
    "/{knowledge_base_uuid}/documents/{document_uuid}/versions/{version}/preview",
    response_model=SuccessEnvelope[KnowledgeDocumentPreview],
)
async def preview_knowledge_document_version(
    knowledge_base_uuid: UUID,
    document_uuid: UUID,
    version: int,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.read")),
    ],
):
    base = await _base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=knowledge_base_uuid,
    )
    item = await KnowledgeRepository().get_document_version(
        session,
        tenant_id=principal.tenant_id,
        knowledge_base_id=int(base["id"]),
        document_uuid=str(document_uuid),
        version=version,
    )
    if item is None:
        raise BusinessError("KNOWLEDGE_VERSION_NOT_FOUND", "文档版本不存在", status_code=404)
    preview_limit = 50_000
    extracted_text = item["extracted_text"]
    text = extracted_text[:preview_limit] if extracted_text else None
    return SuccessEnvelope(
        data=KnowledgeDocumentPreview(
            document_uuid=document_uuid,
            filename=item["filename"],
            mime_type=item["mime_type"],
            version=item["version"],
            extraction_method=item["extraction_method"],
            page_or_sheet_count=item["page_or_sheet_count"],
            text=text,
            available=bool(extracted_text),
            truncated=bool(extracted_text and len(extracted_text) > preview_limit),
        ),
        request_id=request.state.request_id,
    )


@router.post(
    "/{knowledge_base_uuid}/documents/{document_uuid}/versions/{version}:restore",
    response_model=SuccessEnvelope[KnowledgeDocumentItem],
    status_code=202,
)
async def restore_knowledge_document_version(
    knowledge_base_uuid: UUID,
    document_uuid: UUID,
    version: int,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.write")),
    ],
):
    base = await _base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=knowledge_base_uuid,
    )
    document = await _document(
        session,
        tenant_id=principal.tenant_id,
        base_id=int(base["id"]),
        document_uuid=document_uuid,
    )
    source = await KnowledgeRepository().get_document_version(
        session,
        tenant_id=principal.tenant_id,
        knowledge_base_id=int(base["id"]),
        document_uuid=str(document_uuid),
        version=version,
    )
    if source is None:
        raise BusinessError("KNOWLEDGE_VERSION_NOT_FOUND", "文档版本不存在", status_code=404)
    if int(source["version"]) == int(document["version"]):
        raise BusinessError(
            "KNOWLEDGE_VERSION_ALREADY_CURRENT",
            "所选版本已是当前版本",
            status_code=409,
        )
    item = await KnowledgeService(request.app.state.settings).queue_document(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=str(knowledge_base_uuid),
        filename=document["filename"],
        mime_type=document["mime_type"],
        content=bytes(source["source_content"]),
        document_type=document["document_type"],
        document_id=int(document["id"]),
    )
    await session.commit()
    await _dispatch_index_job(
        request,
        background_tasks,
        tenant_id=principal.tenant_id,
        index_job_uuid=str(item["index_job_uuid"]),
    )
    return SuccessEnvelope(data=KnowledgeDocumentItem(**item), request_id=request.state.request_id)


@router.delete(
    "/{knowledge_base_uuid}/documents/{document_uuid}",
    response_model=SuccessEnvelope[dict],
)
async def delete_knowledge_document(
    knowledge_base_uuid: UUID,
    document_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.write")),
    ],
):
    base = await _base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=knowledge_base_uuid,
    )
    deleted = await KnowledgeRepository().archive_document(
        session,
        tenant_id=principal.tenant_id,
        knowledge_base_id=int(base["id"]),
        document_uuid=str(document_uuid),
    )
    await session.commit()
    if not deleted:
        raise BusinessError("KNOWLEDGE_DOCUMENT_NOT_FOUND", "知识文档不存在或已删除", status_code=404)
    return SuccessEnvelope(
        data={"document_uuid": str(document_uuid), "deleted": True},
        request_id=request.state.request_id,
    )


@router.post(
    "/{knowledge_base_uuid}/documents/{document_uuid}:reindex",
    response_model=SuccessEnvelope[dict],
    status_code=202,
)
async def reindex_knowledge_document(
    knowledge_base_uuid: UUID,
    document_uuid: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.write")),
    ],
):
    base = await _base(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        knowledge_base_uuid=knowledge_base_uuid,
    )
    document = await _document(
        session,
        tenant_id=principal.tenant_id,
        base_id=int(base["id"]),
        document_uuid=document_uuid,
    )
    index_job_uuid = await KnowledgeRepository().enqueue_reindex(
        session,
        tenant_id=principal.tenant_id,
        document_id=int(document["id"]),
        document_version_id=int(document["document_version_id"]),
    )
    await session.commit()
    await _dispatch_index_job(
        request,
        background_tasks,
        tenant_id=principal.tenant_id,
        index_job_uuid=index_job_uuid,
    )
    return SuccessEnvelope(
        data={"document_uuid": str(document_uuid), "index_job_uuid": index_job_uuid, "status": "queued"},
        request_id=request.state.request_id,
    )


@router.post(":search", response_model=SuccessEnvelope[KnowledgeSearchResponse])
async def search_knowledge_bases(
    body: KnowledgeSearchRequest,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("knowledge.read")),
    ],
):
    repository = KnowledgeRepository()
    for knowledge_base_uuid in body.knowledge_base_uuids:
        if await repository.get_base(
            session,
            tenant_id=principal.tenant_id,
            knowledge_base_uuid=str(knowledge_base_uuid),
            user_id=principal.user_id,
        ) is None:
            raise BusinessError("KNOWLEDGE_BASE_NOT_FOUND", "存在不可访问的知识库", status_code=404)
    workspace_id = None
    if body.workspace_uuid:
        workspace = await WorkspaceRepository().get(
            session,
            tenant_id=principal.tenant_id,
            workspace_uuid=str(body.workspace_uuid),
        )
        if workspace is None:
            raise BusinessError("WORKSPACE_NOT_FOUND", "工作台不存在或已删除", status_code=404)
        workspace_id = int(workspace["id"])
    try:
        matches = await KnowledgeService(request.app.state.settings).search(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_id=workspace_id,
            query=body.query,
            knowledge_base_uuids=[str(item) for item in body.knowledge_base_uuids],
            document_types=body.filters.document_type,
            top_k=body.top_k,
        )
    except RuntimeError as exc:
        raise BusinessError(
            "KNOWLEDGE_SEARCH_UNAVAILABLE",
            "知识库检索暂不可用，请检查索引和模型路由配置",
            status_code=503,
        ) from exc
    await session.commit()
    return SuccessEnvelope(
        data=KnowledgeSearchResponse(
            matches=matches,
            relevance_threshold=request.app.state.settings.knowledge_relevance_threshold,
            refused=not bool(matches),
        ),
        request_id=request.state.request_id,
    )
