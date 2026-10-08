"""Tenant-scoped AI employee goal, run, approval and artifact APIs."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import AuthenticatedPrincipal, require_permission
from ..database import bind_tenant_session
from ..dependencies import DatabaseSession
from ..errors import BusinessError
from ..repositories.agent_repository import AgentRepository
from ..schemas import SuccessEnvelope
from ..schemas.agent_runtime import (
    AgentCapabilityItem,
    AgentGoalDetail,
    AgentGoalSummary,
    AgentOverview,
    AgentRunResponse,
    AgentSkillItem,
    ApprovalRespondRequest,
    GoalCreateRequest,
    GoalDraftRequest,
    GoalDraftResponse,
    GoalMessageRequest,
    RunControlRequest,
)
from ..services.agent_goal_service import AgentGoalService
from ..services.agent_planner import skill_catalog, tool_catalog
from ..services.agent_supervisor import AgentSupervisor
from ..services.job_dispatch import enqueue_job

router = APIRouter(prefix="/api/v1/agent-runtime", tags=["AI Employee"])
AgentPrincipal = Annotated[
    AuthenticatedPrincipal,
    Depends(require_permission("analysis.execute")),
]


def _require_agent_runtime_enabled(request: Request) -> None:
    if not request.app.state.settings.agent_runtime_enabled:
        raise BusinessError(
            "AGENT_RUNTIME_DISABLED",
            "AI 员工执行功能本期未开放",
            status_code=409,
        )


async def _run_in_process(settings, database, payload: dict[str, Any]) -> None:
    async with database.session_factory() as session:
        await bind_tenant_session(session, int(payload["tenant_id"]))
        await AgentSupervisor(settings).execute(session, **payload)


async def _dispatch(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] | None,
) -> None:
    if payload is None:
        return
    if request.app.state.job_queue is not None:
        await enqueue_job(
            request.app,
            "agent_run",
            payload,
            job_id=f"agent-run:{payload['run_uuid']}",
        )
        return
    background_tasks.add_task(
        _run_in_process,
        request.app.state.settings,
        request.app.state.database,
        payload,
    )


@router.get(
    "/overview",
    response_model=SuccessEnvelope[AgentOverview],
    summary="读取 AI 员工驾驶舱",
)
async def get_agent_overview(
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    repository = AgentRepository()
    profile = await repository.ensure_catalog(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
    )
    goals = await repository.list_goals(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        limit=20,
    )
    approvals = await repository.list_approvals(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        status="pending",
        limit=20,
    )
    artifacts = await repository.list_artifacts(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        limit=12,
    )
    activities = await repository.list_events(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        limit=20,
    )
    terminal = {"succeeded", "partial_succeeded", "failed", "cancelled"}
    counts = {
        "active": sum(item["status"] not in terminal for item in goals),
        "waiting_human": sum(item["status"] == "waiting_human" for item in goals),
        "completed": sum(item["status"] in {"succeeded", "partial_succeeded"} for item in goals),
        "artifacts": len(artifacts),
    }
    await session.commit()
    return SuccessEnvelope(
        data=AgentOverview(
            profile={
                "profile_uuid": profile["profile_uuid"],
                "display_name": profile["display_name"],
                "role_title": profile["role_title"],
                "autonomy_level": profile["autonomy_level"],
                "status": profile["status"],
                "enabled_skills": profile["enabled_skills"],
            },
            counts=counts,
            commitments=[AgentGoalSummary(**item) for item in goals],
            approvals=approvals,
            artifacts=artifacts,
            activities=activities,
        ),
        request_id=request.state.request_id,
    )


@router.post(
    "/goals:draft",
    response_model=SuccessEnvelope[GoalDraftResponse],
    summary="解析目标并生成委派草案",
)
async def draft_agent_goal(
    body: GoalDraftRequest,
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    data = await AgentGoalService().draft(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        body=body,
    )
    await session.commit()
    return SuccessEnvelope(
        data=GoalDraftResponse(**data),
        request_id=request.state.request_id,
    )


@router.post(
    "/goals",
    response_model=SuccessEnvelope[AgentGoalDetail],
    status_code=201,
    summary="创建 AI 员工目标",
)
async def create_agent_goal(
    body: GoalCreateRequest,
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
):
    try:
        data = await AgentGoalService().create(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            idempotency_key=idempotency_key,
            body=body,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(
        data=AgentGoalDetail(**data),
        request_id=request.state.request_id,
    )


@router.get(
    "/goals",
    response_model=SuccessEnvelope[list[AgentGoalSummary]],
    summary="读取目标列表",
)
async def list_agent_goals(
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
    status: Annotated[str | None, Query(max_length=24)] = None,
):
    rows = await AgentRepository().list_goals(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        status=status,
    )
    return SuccessEnvelope(
        data=[AgentGoalSummary(**row) for row in rows],
        request_id=request.state.request_id,
    )


@router.post(
    "/goals/{goal_uuid}/plan",
    response_model=SuccessEnvelope[AgentGoalDetail],
    summary="读取或确认目标计划",
)
async def plan_agent_goal(
    goal_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    data = await AgentGoalService().get_goal(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        goal_uuid=str(goal_uuid),
    )
    return SuccessEnvelope(
        data=AgentGoalDetail(**data),
        request_id=request.state.request_id,
    )


@router.post(
    "/goals/{goal_uuid}/start",
    response_model=SuccessEnvelope[AgentGoalDetail],
    status_code=202,
    summary="启动目标执行",
)
async def start_agent_goal(
    goal_uuid: UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    _require_agent_runtime_enabled(request)
    try:
        data, dispatch = await AgentGoalService().start(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            goal_uuid=str(goal_uuid),
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await _dispatch(request, background_tasks, dispatch)
    return SuccessEnvelope(
        data=AgentGoalDetail(**data),
        request_id=request.state.request_id,
    )


@router.get(
    "/goals/{goal_uuid}",
    response_model=SuccessEnvelope[AgentGoalDetail],
    summary="读取目标执行台",
)
async def get_agent_goal(
    goal_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    data = await AgentGoalService().get_goal(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        goal_uuid=str(goal_uuid),
    )
    return SuccessEnvelope(
        data=AgentGoalDetail(**data),
        request_id=request.state.request_id,
    )


@router.post(
    "/goals/{goal_uuid}/messages",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="向目标追加要求",
)
async def append_agent_goal_message(
    goal_uuid: UUID,
    body: GoalMessageRequest,
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    messages = await AgentGoalService().append_message(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        goal_uuid=str(goal_uuid),
        content=body.content,
    )
    await session.commit()
    return SuccessEnvelope(data=messages, request_id=request.state.request_id)


@router.post(
    "/runs/{run_uuid}/{action}",
    response_model=SuccessEnvelope[dict[str, Any]],
    status_code=202,
    summary="暂停、恢复或取消执行",
)
async def control_agent_run(
    run_uuid: UUID,
    action: str,
    body: RunControlRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    if action not in {"pause", "resume", "cancel"}:
        raise BusinessError(
            "AGENT_RUN_ACTION_INVALID",
            "仅支持 pause、resume 或 cancel",
            status_code=422,
        )
    if action == "resume":
        _require_agent_runtime_enabled(request)
    data, dispatch = await AgentGoalService().control_run(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        run_uuid=str(run_uuid),
        action=action,
        reason=body.reason,
    )
    await session.commit()
    await _dispatch(request, background_tasks, dispatch)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get(
    "/runs/{run_uuid}",
    response_model=SuccessEnvelope[AgentRunResponse],
    summary="读取执行状态",
)
async def get_agent_run(
    run_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    context = await AgentRepository().run_context(
        session,
        tenant_id=principal.tenant_id,
        run_uuid=str(run_uuid),
    )
    if context is None or int(context["goal_created_by"]) != principal.user_id:
        raise BusinessError("AGENT_RUN_NOT_FOUND", "执行实例不存在或不可访问", status_code=404)
    return SuccessEnvelope(
        data=AgentRunResponse(**{
            key: context.get(key)
            for key in AgentRunResponse.model_fields
        }),
        request_id=request.state.request_id,
    )


@router.get(
    "/runs/{run_uuid}/timeline",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="读取执行时间线",
)
async def get_agent_run_timeline(
    run_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
):
    rows = await AgentRepository().list_events(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        run_uuid=str(run_uuid),
        after_sequence=after_sequence,
    )
    return SuccessEnvelope(data=rows, request_id=request.state.request_id)


@router.get("/runs/{run_uuid}/events", summary="订阅 AI 员工执行事件")
async def stream_agent_run_events(
    run_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    context = await AgentRepository().run_context(
        session,
        tenant_id=principal.tenant_id,
        run_uuid=str(run_uuid),
    )
    if context is None or int(context["goal_created_by"]) != principal.user_id:
        raise BusinessError("AGENT_RUN_NOT_FOUND", "执行实例不存在或不可访问", status_code=404)

    async def events():
        sequence = 0
        while not await request.is_disconnected():
            rows = await AgentRepository().list_events(
                session,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                run_uuid=str(run_uuid),
                after_sequence=sequence,
            )
            for row in rows:
                sequence = max(sequence, int(row["sequence_no"]))
                yield (
                    f"id: {sequence}\nevent: {row['event_type']}\n"
                    f"data: {json.dumps(row, ensure_ascii=False, default=str)}\n\n"
                )
            current = await AgentRepository().run_context(
                session,
                tenant_id=principal.tenant_id,
                run_uuid=str(run_uuid),
            )
            if current and current["status"] in {
                "succeeded",
                "partial_succeeded",
                "failed",
                "cancelled",
            }:
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    })


@router.get(
    "/approvals",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="读取待我处理事项",
)
async def list_agent_approvals(
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
    status: Annotated[str | None, Query(max_length=16)] = "pending",
):
    rows = await AgentRepository().list_approvals(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        status=status,
    )
    return SuccessEnvelope(data=rows, request_id=request.state.request_id)


@router.post(
    "/approvals/{approval_uuid}:respond",
    response_model=SuccessEnvelope[dict[str, Any]],
    status_code=202,
    summary="处理 AI 员工确认事项",
)
async def respond_agent_approval(
    approval_uuid: UUID,
    body: ApprovalRespondRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    if body.selected_option != "cancel":
        _require_agent_runtime_enabled(request)
    data, dispatch = await AgentGoalService().respond_approval(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        approval_uuid=str(approval_uuid),
        selected_option=body.selected_option,
        user_input=body.user_input,
    )
    await session.commit()
    await _dispatch(request, background_tasks, dispatch)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get(
    "/artifacts",
    response_model=SuccessEnvelope[list[dict[str, Any]]],
    summary="读取 AI 员工交付物",
)
async def list_agent_artifacts(
    request: Request,
    session: DatabaseSession,
    principal: AgentPrincipal,
):
    rows = await AgentRepository().list_artifacts(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
    )
    return SuccessEnvelope(data=rows, request_id=request.state.request_id)


@router.get(
    "/skills",
    response_model=SuccessEnvelope[list[AgentSkillItem]],
    summary="读取已安装技能",
)
async def list_agent_skills(request: Request, principal: AgentPrincipal):
    return SuccessEnvelope(
        data=[AgentSkillItem(**item) for item in skill_catalog()],
        request_id=request.state.request_id,
    )


@router.get(
    "/capabilities",
    response_model=SuccessEnvelope[list[AgentCapabilityItem]],
    summary="读取可审计能力目录",
)
async def list_agent_capabilities(request: Request, principal: AgentPrincipal):
    return SuccessEnvelope(
        data=[AgentCapabilityItem(**item) for item in tool_catalog()],
        request_id=request.state.request_id,
    )
