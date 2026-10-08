"""P0 analysis task and Agent execution endpoints."""

import asyncio
import json

from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from ..auth import AuthenticatedPrincipal, require_permission, require_user
from ..dependencies import DatabaseSession, Pagination
from ..schemas import PageData, SuccessEnvelope
from ..schemas.analysis_tasks import (AnalysisTaskCreateRequest, AnalysisTaskCreated, AnalysisTaskListItem,
    AnalysisTaskResultResponse, AnalysisTaskStarted, AnalysisTaskStatusResponse,
    ConfirmationAnswerAccepted, ConfirmationAnswerRequest)
from ..schemas.context_lifecycle import TurnCreateRequest
from ..errors import BusinessError
from ..repositories.analysis_task_repository import AnalysisTaskRepository
from ..services.analysis_agent_adapter import AnalysisAgentAdapter
from ..services.analysis_task_service import AnalysisTaskService
from ..services.job_dispatch import enqueue_job
from ..services.turn_service import TurnService

router = APIRouter(prefix="/api/v1/analysis-tasks", tags=["Analysis Tasks"])


class TaskChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


async def _run_demo_analysis(settings, dispatch: dict) -> None:
    await AnalysisAgentAdapter(settings).run(**dispatch)


async def _resume_confirmation(settings, tenant_id) -> None:
    await AnalysisAgentAdapter(settings).resume_next_confirmation(tenant_id)


@router.post("", operation_id="API-INS-01", status_code=201, summary="创建分析任务")
async def create_analysis_task(body: AnalysisTaskCreateRequest, request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("analysis.execute"))],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
    def envelope(data):
        return SuccessEnvelope(data=AnalysisTaskCreated(**data),
                               request_id=request.state.request_id).model_dump(mode="json")
    try:
        status_code, content = await AnalysisTaskService(request.app.state.settings).create(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            idempotency_key=idempotency_key, payload=body.model_dump(mode="python"),
            response_envelope=envelope, request_id=request.state.request_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return JSONResponse(status_code=status_code, content=content)


@router.post("/{task_uuid}:start", operation_id="API-INS-02", status_code=202,
             summary="启动分析任务")
async def start_analysis_task(task_uuid: UUID, request: Request, background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_permission("analysis.execute"))],
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
        if request.app.state.job_queue is not None:
            await enqueue_job(request.app, "analysis", dispatch, job_id=f"analysis:{task_uuid}")
        else:
            background_tasks.add_task(_run_demo_analysis, request.app.state.settings, dispatch)
    return JSONResponse(status_code=status_code, content=content)


@router.get("", response_model=SuccessEnvelope[PageData[AnalysisTaskListItem]],
            operation_id="API-INS-00", summary="查询分析任务列表")
async def list_analysis_tasks(request: Request, session: DatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    source: Annotated[str | None, Query(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")] = None):
    rows, total = await AnalysisTaskRepository().list(
        session, tenant_id=principal.tenant_id,
        offset=pagination.offset, limit=pagination.page_size, source=source,
    )
    data = PageData[AnalysisTaskListItem].build(
        items=[AnalysisTaskListItem(**row) for row in rows], total=total, params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


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


@router.get("/{task_uuid}/events", operation_id="API-INS-06",
            summary="订阅任务阶段实时事件")
async def stream_analysis_task_events(task_uuid: UUID, request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    async def events():
        last_payload = None
        while not await request.is_disconnected():
            data = await AnalysisTaskService(request.app.state.settings).status(
                session, tenant_id=principal.tenant_id, task_uuid=str(task_uuid),
                include_stage_runs=True, stage_run_limit=20,
                admin=principal.role_code == "admin",
            )
            payload = AnalysisTaskStatusResponse(**data).model_dump(mode="json")
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if serialized != last_payload:
                yield f"event: task\ndata: {serialized}\n\n"
                last_payload = serialized
            if payload["status"] in {"succeeded", "partial_succeeded", "failed", "cancelled"}:
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    })


@router.post("/{task_uuid}/chat", operation_id="API-INS-07", include_in_schema=False,
             summary="基于任务证据问询分析结果")
async def chat_about_analysis_task(task_uuid: UUID, body: TaskChatRequest,
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None):
    workspace_uuid = await session.scalar(text("""
        SELECT analysis_config->>'workspace_uuid' FROM analysis_tasks
         WHERE tenant_id=:tenant_id AND task_uuid=CAST(:task_uuid AS uuid)
    """), {"tenant_id": principal.tenant_id, "task_uuid": str(task_uuid)})
    if not workspace_uuid:
        raise BusinessError("TASK_WORKSPACE_REQUIRED", "该任务尚未绑定分析工作台", status_code=409)
    effective_key = idempotency_key or f"legacy-task:{request.state.request_id}"
    turn_body = TurnCreateRequest(
        client_turn_id=uuid5(NAMESPACE_URL, f"furniscope:{task_uuid}:{effective_key}"),
        question=body.question,
        task_uuid=task_uuid,
    )
    service = TurnService(request.app.state.settings)
    try:
        turn, created = await service.reserve(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_uuid=workspace_uuid,
            idempotency_key=effective_key,
            body=turn_body,
        )
        await session.commit()
        payload = turn["response_payload"] if not created else await service.answer(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_uuid=workspace_uuid,
            turn_uuid=turn["turn_uuid"],
            body=turn_body,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    payload = {
        "answer": payload["answer"],
        "evidence_refs": [item["citation_uuid"] for item in payload.get("citations") or []],
        "context_sources": payload.get("context_sources") or [],
        "suggested_actions": payload.get("suggested_actions") or [],
    }
    return SuccessEnvelope(data=payload, request_id=request.state.request_id)


@router.post("/{task_uuid}/chat/stream", summary="基于任务证据的流式问询（兼容接口）", include_in_schema=False)
@router.post("/{task_uuid}/chat:stream", include_in_schema=False)
async def chat_about_analysis_task_stream(task_uuid: UUID, body: TaskChatRequest,
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None):
    workspace_uuid = await session.scalar(text("""
        SELECT analysis_config->>'workspace_uuid' FROM analysis_tasks
         WHERE tenant_id=:tenant_id AND task_uuid=CAST(:task_uuid AS uuid)
    """), {"tenant_id": principal.tenant_id, "task_uuid": str(task_uuid)})
    if not workspace_uuid:
        raise BusinessError("TASK_WORKSPACE_REQUIRED", "该任务尚未绑定分析工作台", status_code=409)
    effective_key = idempotency_key or f"legacy-task:{request.state.request_id}"
    turn_body = TurnCreateRequest(
        client_turn_id=uuid5(NAMESPACE_URL, f"furniscope:{task_uuid}:{effective_key}"),
        question=body.question,
        task_uuid=task_uuid,
    )
    service = TurnService(request.app.state.settings)
    turn, created = await service.reserve(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        workspace_uuid=workspace_uuid,
        idempotency_key=effective_key,
        body=turn_body,
    )
    await session.commit()
    if not created:
        async def replay():
            yield ": connected\n\n"
            yield f"event: done\ndata: {json.dumps(turn['response_payload'], ensure_ascii=False)}\n\n"
        events = replay()
    else:
        events = service.stream(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_uuid=workspace_uuid,
            turn_uuid=turn["turn_uuid"],
            body=turn_body,
        )
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get("/{task_uuid}", response_model=SuccessEnvelope[AnalysisTaskStatusResponse],
            operation_id="API-INS-03", summary="查询任务五阶段状态")
async def get_analysis_task_status(task_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    include_stage_runs: Annotated[bool, Query()] = False,
    stage_run_limit: Annotated[int, Query(ge=1, le=100)] = 20):
    data = await AnalysisTaskService(request.app.state.settings).status(
        session, tenant_id=principal.tenant_id, task_uuid=str(task_uuid),
        include_stage_runs=include_stage_runs, stage_run_limit=stage_run_limit,
        admin=principal.role_code == "admin")
    return SuccessEnvelope(data=AnalysisTaskStatusResponse(**data),
                           request_id=request.state.request_id)


@router.post("/confirmations/{confirmation_id}:answer",
             response_model=SuccessEnvelope[ConfirmationAnswerAccepted], status_code=202,
             operation_id="API-INS-05", summary="回答用户确认并安全恢复任务")
async def answer_confirmation(confirmation_id: UUID, body: ConfirmationAnswerRequest,
    request: Request, background_tasks: BackgroundTasks,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    try:
        data = await AnalysisAgentAdapter(request.app.state.settings).accept_confirmation(
            confirmation_id=str(confirmation_id), selected_option=body.selected_option,
            user_input=body.user_input, user_id=principal.user_id, tenant_id=principal.tenant_id,
        )
    except ValueError as exc:
        raise BusinessError("CONFIRMATION_INVALID", str(exc), status_code=409) from exc
    if request.app.state.job_queue is not None:
        await enqueue_job(request.app, "confirmation_resume", {"tenant_id": principal.tenant_id},
                          job_id=f"confirmation:{confirmation_id}")
    else:
        background_tasks.add_task(_resume_confirmation, request.app.state.settings, principal.tenant_id)
    return SuccessEnvelope(data=ConfirmationAnswerAccepted(**data),
                           request_id=request.state.request_id)
