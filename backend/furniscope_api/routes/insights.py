"""Read-only market insight drill-down endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession
from ..errors import BusinessError
from ..repositories.insight_repository import InsightRepository
from ..schemas import SuccessEnvelope

router = APIRouter(prefix="/api/v1/analysis-tasks", tags=["Market Insights"])


async def _projection(task_uuid: UUID, name: str, request: Request, session: DatabaseSession,
                      principal: AuthenticatedPrincipal):
    rows = await InsightRepository().task_projection(
        session, tenant_id=principal.tenant_id, task_uuid=str(task_uuid), projection=name,
    )
    if rows is None:
        raise BusinessError("TASK_NOT_FOUND", "任务不存在或不可访问", status_code=404)
    return SuccessEnvelope(data={"task_uuid": task_uuid, "items": rows}, request_id=request.state.request_id)


@router.get("/{task_uuid}/competitors", operation_id="API-CMP-01", summary="查询竞品集合")
async def competitors(task_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return await _projection(task_uuid, "competitors", request, session, principal)


@router.get("/{task_uuid}/review-aspects", operation_id="API-REV-01", summary="查询评论观点")
async def review_aspects(task_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return await _projection(task_uuid, "review_aspects", request, session, principal)


@router.get("/{task_uuid}/insight-clusters", operation_id="API-REV-02", summary="查询洞察聚类")
async def insight_clusters(task_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return await _projection(task_uuid, "clusters", request, session, principal)


@router.get("/{task_uuid}/evidence", operation_id="API-EVD-01", summary="查询任务证据")
async def evidence(task_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return await _projection(task_uuid, "evidence", request, session, principal)


@router.get("/{task_uuid}/opportunities", operation_id="API-OPP-01", summary="查询市场机会")
async def opportunities(task_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return await _projection(task_uuid, "opportunities", request, session, principal)


@router.get("/{task_uuid}/recommendations", operation_id="API-REC-01", summary="查询产品建议")
async def recommendations(task_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return await _projection(task_uuid, "recommendations", request, session, principal)
