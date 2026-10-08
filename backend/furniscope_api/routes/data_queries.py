"""Tenant-scoped controlled metric-query endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from ..auth import AuthenticatedPrincipal, require_permission
from ..dependencies import DatabaseSession
from ..errors import BusinessError
from ..repositories.workspace_repository import WorkspaceRepository
from ..schemas import SuccessEnvelope
from ..schemas.data_query import (
    DataMetricItem,
    DataQueryAuditItem,
    DataQueryExecuteRequest,
    DataQueryResult,
)
from ..services.data_query import DataQueryService

router = APIRouter(prefix="/api/v1/data-queries", tags=["Data Queries"])


@router.get(
    "/metrics",
    response_model=SuccessEnvelope[list[DataMetricItem]],
    summary="读取受控指标目录",
)
async def list_data_metrics(
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("dataset.read")),
    ],
):
    items = await DataQueryService(request.app.state.settings).metric_catalog(session)
    return SuccessEnvelope(
        data=[DataMetricItem(**item) for item in items],
        request_id=request.state.request_id,
    )


@router.post(
    ":execute",
    response_model=SuccessEnvelope[DataQueryResult],
    summary="执行受控语义数据查询",
)
async def execute_data_query(
    body: DataQueryExecuteRequest,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("dataset.read")),
    ],
):
    workspace_id = None
    if body.workspace_uuid:
        workspace = await WorkspaceRepository().get(
            session,
            tenant_id=principal.tenant_id,
            workspace_uuid=str(body.workspace_uuid),
        )
        if workspace is None:
            raise BusinessError(
                "WORKSPACE_NOT_FOUND",
                "工作台不存在或已删除",
                status_code=404,
            )
        workspace_id = int(workspace["id"])
    data = await DataQueryService(request.app.state.settings).execute(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        plan=body.plan,
        workspace_id=workspace_id,
    )
    await session.commit()
    return SuccessEnvelope(
        data=DataQueryResult(**data),
        request_id=request.state.request_id,
    )


@router.get(
    "/{query_uuid}",
    response_model=SuccessEnvelope[DataQueryAuditItem],
    summary="读取可审计问数结果",
)
async def get_data_query(
    query_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal,
        Depends(require_permission("dataset.read")),
    ],
):
    data = await DataQueryService(request.app.state.settings).get(
        session,
        tenant_id=principal.tenant_id,
        query_uuid=str(query_uuid),
    )
    if data is None:
        raise BusinessError(
            "DATA_QUERY_NOT_FOUND",
            "问数结果不存在或不可访问",
            status_code=404,
        )
    return SuccessEnvelope(
        data=DataQueryAuditItem(**data),
        request_id=request.state.request_id,
    )
