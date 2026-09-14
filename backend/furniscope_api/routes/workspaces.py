"""AI workbench persistence endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..errors import BusinessError
from ..repositories.workspace_repository import WorkspaceRepository
from ..schemas import PageData, SuccessEnvelope
from ..schemas.workspaces import (
    WorkspaceArchiveResponse, WorkspaceCreateRequest, WorkspaceListItem,
    WorkspaceMessageCreateRequest, WorkspaceMessageItem,
)

router = APIRouter(prefix="/api/v1/analysis-workspaces", tags=["Analysis Workspaces"])


@router.post("", response_model=SuccessEnvelope[WorkspaceListItem], status_code=201,
             summary="创建或更新分析工作台")
async def upsert_workspace(body: WorkspaceCreateRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    try:
        repo = WorkspaceRepository()
        saved = await repo.upsert(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            payload=body.model_dump(mode="python"),
        )
        created = await repo.summary(
            session, tenant_id=principal.tenant_id, workspace_uuid=str(saved["workspace_uuid"]),
        )
        if created is None:
            raise BusinessError("WORKSPACE_NOT_FOUND", "工作台写入后无法读取", status_code=500)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(data=WorkspaceListItem(**created), request_id=request.state.request_id)


@router.get("", response_model=SuccessEnvelope[PageData[WorkspaceListItem]],
            summary="查询分析工作台")
async def list_workspaces(request: Request, session: DatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    q: Annotated[str | None, Query(max_length=200)] = None):
    rows, total = await WorkspaceRepository().list(
        session, tenant_id=principal.tenant_id, offset=pagination.offset,
        limit=pagination.page_size, keyword=q,
    )
    data = PageData[WorkspaceListItem].build(
        items=[WorkspaceListItem(**row) for row in rows], total=total, params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/{workspace_uuid}/messages",
            response_model=SuccessEnvelope[PageData[WorkspaceMessageItem]],
            summary="查询工作台对话")
async def list_workspace_messages(workspace_uuid: UUID, request: Request, session: DatabaseSession,
    pagination: Pagination, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    workspace = await WorkspaceRepository().get(
        session, tenant_id=principal.tenant_id, workspace_uuid=str(workspace_uuid),
    )
    if workspace is None:
        raise BusinessError("WORKSPACE_NOT_FOUND", "工作台不存在或已删除", status_code=404)
    rows, total = await WorkspaceRepository().list_messages(
        session, tenant_id=principal.tenant_id, workspace_id=workspace["id"],
        offset=pagination.offset, limit=pagination.page_size,
    )
    data = PageData[WorkspaceMessageItem].build(
        items=[WorkspaceMessageItem(**row) for row in rows], total=total, params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/{workspace_uuid}/messages", response_model=SuccessEnvelope[WorkspaceMessageItem],
             status_code=201, summary="追加工作台对话")
async def append_workspace_message(workspace_uuid: UUID, body: WorkspaceMessageCreateRequest,
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    repo = WorkspaceRepository()
    workspace = await repo.get(session, tenant_id=principal.tenant_id, workspace_uuid=str(workspace_uuid))
    if workspace is None:
        raise BusinessError("WORKSPACE_NOT_FOUND", "工作台不存在或已删除", status_code=404)
    try:
        row = await repo.append_message(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            workspace_id=workspace["id"], payload=body.model_dump(mode="python"),
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(data=WorkspaceMessageItem(**row), request_id=request.state.request_id)


@router.post("/{workspace_uuid}:archive", response_model=SuccessEnvelope[WorkspaceArchiveResponse],
             summary="归档（删除）分析工作台")
async def archive_workspace(workspace_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    try:
        data = await WorkspaceRepository().archive(
            session, tenant_id=principal.tenant_id, workspace_uuid=str(workspace_uuid),
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if not data["archived"]:
        raise BusinessError("WORKSPACE_NOT_FOUND", "工作台不存在或已删除", status_code=404)
    return SuccessEnvelope(data=WorkspaceArchiveResponse(**data), request_id=request.state.request_id)
