"""P0 analysis task and Agent execution endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from ..auth import AuthenticatedPrincipal, require_user, require_user_or_admin
from ..dependencies import DatabaseSession
from ..schemas import SuccessEnvelope
from ..schemas.analysis_tasks import (AnalysisTaskCreateRequest, AnalysisTaskCreated,
    AnalysisTaskResultResponse, AnalysisTaskStarted, AnalysisTaskStatusResponse)
from ..services.analysis_agent_adapter import AnalysisAgentAdapter
from ..services.analysis_task_service import AnalysisTaskService

router = APIRouter(prefix="/api/v1/analysis-tasks", tags=["Analysis Tasks"])


async def _run_demo_analysis(settings, dispatch: dict) -> None:
    await AnalysisAgentAdapter(settings).run(**dispatch)


@router.post("", operation_id="API-INS-01", status_code=201, summary="创建分析任务")
async def create_analysis_task(body: AnalysisTaskCreateRequest, request: Request,
    session: DatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
    def envelope(data):
        return SuccessEnvelope(data=AnalysisTaskCreated(**data),
                               request_id=request.state.request_id).model_dump(mode="json")
    try:
        status_code, content = await AnalysisTaskService(request.app.state.settings).create(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            idempotency_key=idempotency_key, payload=body.model_dump(mode="python"),
            response_envelope=envelope)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return JSONResponse(status_code=status_code, content=content)


@router.post("/{task_uuid}:start", operation_id="API-INS-02", status_code=202,
             summary="启动分析任务")
async def start_analysis_task(task_uuid: UUID, request: Request, background_tasks: BackgroundTasks,
    session: DatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
    def envelope(data):
        return SuccessEnvelope(data=AnalysisTaskStarted(**data),
                               request_id=request.state.request_id).model_dump(mode="json")
    try:
        status_code, content, dispatch = await AnalysisTaskService(request.app.state.settings).start(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            task_uuid=str(task_uuid), idempotency_key=idempotency_key,
            response_envelope=envelope)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if dispatch is not None:
        background_tasks.add_task(_run_demo_analysis, request.app.state.settings, dispatch)
    return JSONResponse(status_code=status_code, content=content)


@router.get("/{task_uuid}/result", response_model=SuccessEnvelope[AnalysisTaskResultResponse],
            operation_id="API-INS-04", summary="获取任务综合结果")
async def get_analysis_task_result(task_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    opportunity_limit: Annotated[int, Query(ge=1, le=100)] = 5,
    recommendation_limit: Annotated[int, Query(ge=1, le=100)] = 10):
    data = await AnalysisTaskService(request.app.state.settings).result(
        session, tenant_id=principal.tenant_id, task_uuid=str(task_uuid),
        opportunity_limit=opportunity_limit, recommendation_limit=recommendation_limit)
    return SuccessEnvelope(data=AnalysisTaskResultResponse(**data),
                           request_id=request.state.request_id)


@router.get("/{task_uuid}", response_model=SuccessEnvelope[AnalysisTaskStatusResponse],
            operation_id="API-INS-03", summary="查询任务五阶段状态")
async def get_analysis_task_status(task_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user_or_admin)],
    include_stage_runs: Annotated[bool, Query()] = False,
    stage_run_limit: Annotated[int, Query(ge=1, le=100)] = 20):
    data = await AnalysisTaskService(request.app.state.settings).status(
        session, tenant_id=principal.tenant_id, task_uuid=str(task_uuid),
        include_stage_runs=include_stage_runs, stage_run_limit=stage_run_limit,
        admin=principal.role_code == "admin")
    return SuccessEnvelope(data=AnalysisTaskStatusResponse(**data),
                           request_id=request.state.request_id)
