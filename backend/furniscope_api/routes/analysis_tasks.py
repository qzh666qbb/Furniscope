"""P0 analysis task and Agent execution endpoints."""

import asyncio
import json

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..schemas import PageData, SuccessEnvelope
from ..schemas.analysis_tasks import (AnalysisTaskCreateRequest, AnalysisTaskCreated, AnalysisTaskListItem,
    AnalysisTaskResultResponse, AnalysisTaskStarted, AnalysisTaskStatusResponse,
    ConfirmationAnswerAccepted, ConfirmationAnswerRequest)
from ..errors import BusinessError
from ..repositories.analysis_task_repository import AnalysisTaskRepository
from ..repositories.insight_repository import InsightRepository
from ..services.analysis_agent_adapter import AnalysisAgentAdapter
from ..services.analysis_task_service import AnalysisTaskService
from ..services.chat_sse import stream_chat_events
from ..services.job_dispatch import enqueue_job
from ..services.model_router_client import ServiceModelRouterClient
from ..services.task_chat import build_task_thinking, local_task_chat_answer
from ..services.workbench_chat import parse_model_chat_text

router = APIRouter(prefix="/api/v1/analysis-tasks", tags=["Analysis Tasks"])


class TaskChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=12)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


async def _task_chat_context(task_uuid, body, request, session, principal, *, streaming: bool):
    service = AnalysisTaskService(request.app.state.settings)
    try:
        result = await service.result(
            session, tenant_id=principal.tenant_id, task_uuid=str(task_uuid),
            opportunity_limit=10, recommendation_limit=20,
        )
    except BusinessError as exc:
        if exc.code not in {"TASK_RESULT_NOT_READY", "TASK_RESULT_INCOMPLETE"}:
            raise
        result = None
    repository = InsightRepository()
    projections = {}
    limits = {"clusters": 12, "opportunities": 10, "recommendations": 20, "evidence": 60, "competitors": 12}
    for name, limit in limits.items():
        rows = await repository.task_projection(
            session, tenant_id=principal.tenant_id,
            task_uuid=str(task_uuid), projection=name,
        ) or []
        projections[name] = rows[:limit]
    fallback = local_task_chat_answer(body.question, result=result, projections=projections)
    thinking = build_task_thinking(body.question, result=result, projections=projections)
    context = json.dumps({"result": result, **projections}, ensure_ascii=False, default=str)
    output_rule = (
        "用中文直接回答用户，不要输出 JSON、不要输出思考标签。"
        if streaming else
        "输出JSON对象，字段answer为简洁中文答案，evidence_refs为引用的记录ID数组。"
    )
    messages = [{
        "role": "system",
        "content": (
            "你是FurniScope任务结果问询助手。只能依据给定任务数据作答；没有依据时明确说数据不足，禁止补造数字或事实。"
            "若用户问销量预测、未来销量、销售数据或能否预测：必须说明本任务是市场洞察（评论/竞品/机会评分），"
            "没有订单序列，机会分不能当作未来销量，并引导去「销量预测」模块。不要用评论数或机会分回答销量问题。"
            f"{output_rule}任务数据："
            + context
        ),
    }]
    messages.extend({"role": item.get("role", "user"), "content": item.get("content", "")[:2000]}
                    for item in body.history[-8:] if item.get("content"))
    messages.append({"role": "user", "content": body.question})
    return fallback, thinking, messages


async def _run_demo_analysis(settings, dispatch: dict) -> None:
    await AnalysisAgentAdapter(settings).run(**dispatch)


async def _resume_confirmation(settings) -> None:
    await AnalysisAgentAdapter(settings).resume_next_confirmation()


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
            response_envelope=envelope, request_id=request.state.request_id)
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


@router.post("/{task_uuid}/chat", operation_id="API-INS-07",
             summary="基于任务证据问询分析结果")
async def chat_about_analysis_task(task_uuid: UUID, body: TaskChatRequest,
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    fallback, _thinking, messages = await _task_chat_context(
        task_uuid, body, request, session, principal, streaming=False,
    )
    client = ServiceModelRouterClient(request.app.state.settings)
    try:
        output = await client.structured(messages=messages, output_type=dict)
    except RuntimeError:
        output = fallback
    finally:
        await client.close()
    if not isinstance(output, dict) or not isinstance(output.get("answer"), str) or not output["answer"].strip():
        output = fallback
    if not isinstance(output.get("answer"), str) or not output["answer"].strip():
        raise BusinessError("TASK_CHAT_INVALID", "智能问询返回格式无效", status_code=502)
    payload = {"answer": output["answer"].strip(), "evidence_refs": output.get("evidence_refs") or []}
    if output.get("suggested_action"):
        payload["suggested_action"] = output["suggested_action"]
    return SuccessEnvelope(data=payload, request_id=request.state.request_id)


@router.post("/{task_uuid}/chat/stream", summary="基于任务证据的流式问询")
@router.post("/{task_uuid}/chat:stream", include_in_schema=False)
async def chat_about_analysis_task_stream(task_uuid: UUID, body: TaskChatRequest,
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    fallback, thinking, messages = await _task_chat_context(
        task_uuid, body, request, session, principal, streaming=True,
    )
    client = ServiceModelRouterClient(request.app.state.settings)

    def finalize(answer_text: str) -> dict:
        parsed = parse_model_chat_text(answer_text) or {}
        answer = str(parsed.get("answer") or answer_text or fallback.get("answer") or "").strip()
        refs = parsed.get("evidence_refs") if isinstance(parsed.get("evidence_refs"), list) else fallback.get("evidence_refs") or []
        payload = {"answer": answer, "evidence_refs": refs}
        action = parsed.get("suggested_action") or fallback.get("suggested_action")
        if action:
            payload["suggested_action"] = action
        return payload

    return StreamingResponse(
        stream_chat_events(
            thinking=thinking, messages=messages, client=client, fallback=fallback, finalize=finalize,
        ),
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
            user_input=body.user_input, user_id=principal.user_id,
        )
    except ValueError as exc:
        raise BusinessError("CONFIRMATION_INVALID", str(exc), status_code=409) from exc
    if request.app.state.job_queue is not None:
        await enqueue_job(request.app, "confirmation_resume", {},
                          job_id=f"confirmation:{confirmation_id}")
    else:
        background_tasks.add_task(_resume_confirmation, request.app.state.settings)
    return SuccessEnvelope(data=ConfirmationAnswerAccepted(**data),
                           request_id=request.state.request_id)
