"""Unified API resources for turns, memories, context, citations, and policy."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..errors import BusinessError
from ..repositories.conversation_repository import ConversationRepository
from ..repositories.memory_repository import MemoryRepository
from ..repositories.workspace_repository import WorkspaceRepository
from ..schemas import PageData, SuccessEnvelope
from ..schemas.context_lifecycle import (
    CitationItem,
    ContextPreviewRequest,
    ContextPreviewResponse,
    ConversationStateItem,
    CustomerMemoryBatchConfirmRequest,
    CustomerMemoryItem,
    CustomerMemoryPatchRequest,
    CustomerMemoryPolicy,
    MemoryScope,
    MemoryStatus,
    TurnCreateRequest,
    TurnListItem,
    TurnResponse,
    WorkspaceContextConfig,
    WorkspaceContextResponse,
)
from ..services.chat_sse import sse, text_chunks
from ..services.conversation_context import ConversationContextService
from ..services.turn_service import TurnService

router = APIRouter(prefix="/api/v1", tags=["Context Lifecycle"])


async def _workspace(session, *, tenant_id: int, workspace_uuid: UUID) -> dict:
    value = await WorkspaceRepository().get(
        session,
        tenant_id=tenant_id,
        workspace_uuid=str(workspace_uuid),
    )
    if value is None:
        raise BusinessError("WORKSPACE_NOT_FOUND", "工作台不存在或已删除", status_code=404)
    return value


@router.post(
    "/analysis-workspaces/{workspace_uuid}/turns",
    response_model=SuccessEnvelope[TurnResponse],
    status_code=201,
    summary="原子提交一个工作台对话轮次",
)
async def create_turn(
    workspace_uuid: UUID,
    body: TurnCreateRequest,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
):
    service = TurnService(request.app.state.settings)
    turn = None
    try:
        turn, created = await service.reserve(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_uuid=str(workspace_uuid),
            idempotency_key=idempotency_key,
            body=body,
        )
        await session.commit()
        if not created:
            return SuccessEnvelope(
                data=TurnResponse(**turn["response_payload"]),
                request_id=request.state.request_id,
            )
        payload = await service.answer(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_uuid=str(workspace_uuid),
            turn_uuid=turn["turn_uuid"],
            body=body,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        if turn and turn.get("turn_uuid"):
            workspace = await _workspace(
                session,
                tenant_id=principal.tenant_id,
                workspace_uuid=workspace_uuid,
            )
            await ConversationRepository().fail_turn(
                session,
                tenant_id=principal.tenant_id,
                workspace_id=int(workspace["id"]),
                turn_uuid=turn["turn_uuid"],
                error_code="TURN_EXECUTION_FAILED",
            )
            await session.commit()
        raise
    return SuccessEnvelope(data=TurnResponse(**payload), request_id=request.state.request_id)


@router.post(
    "/analysis-workspaces/{workspace_uuid}/turns:stream",
    summary="以标准 SSE 协议原子提交工作台对话轮次",
)
async def stream_turn(
    workspace_uuid: UUID,
    body: TurnCreateRequest,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
):
    service = TurnService(request.app.state.settings)
    try:
        turn, created = await service.reserve(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_uuid=str(workspace_uuid),
            idempotency_key=idempotency_key,
            body=body,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise

    if not created:
        async def replay():
            yield ": connected\n\n"
            turn_uuid = turn["turn_uuid"]
            yield sse(
                "turn_started",
                {"turn_uuid": turn_uuid, "replayed": True},
                event_id=f"{turn_uuid}:started",
            )
            payload = turn["response_payload"]
            for citation in payload.get("citations") or []:
                yield sse(
                    "citation",
                    citation,
                    event_id=f"{turn_uuid}:citation:{citation['citation_uuid']}",
                )
            for candidate in payload.get("memory_candidates") or []:
                yield sse(
                    "memory_candidate",
                    candidate,
                    event_id=f"{turn_uuid}:memory:{candidate['memory_uuid']}",
                )
            for index, result in enumerate(payload.get("tool_results") or []):
                yield sse(
                    "tool_result",
                    result,
                    event_id=f"{turn_uuid}:tool-result:{index}",
                )
            for index, action in enumerate(payload.get("suggested_actions") or []):
                yield sse("action", action, event_id=f"{turn_uuid}:action:{index}")
            for index, part in enumerate(text_chunks(payload.get("answer") or "")):
                yield sse(
                    "answer_delta",
                    {"delta": part},
                    event_id=f"{turn_uuid}:answer:{index}",
                )
            yield sse("done", payload, event_id=f"{turn_uuid}:done")

        events = replay()
    else:
        events = service.stream(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_uuid=str(workspace_uuid),
            turn_uuid=turn["turn_uuid"],
            body=body,
        )
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/analysis-workspaces/{workspace_uuid}/turns",
    response_model=SuccessEnvelope[PageData[TurnListItem]],
    summary="查询工作台对话轮次",
)
async def list_turns(
    workspace_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    workspace = await _workspace(
        session,
        tenant_id=principal.tenant_id,
        workspace_uuid=workspace_uuid,
    )
    rows, total = await ConversationRepository().list_turns(
        session,
        tenant_id=principal.tenant_id,
        workspace_id=int(workspace["id"]),
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    data = PageData[TurnListItem].build(
        items=[TurnListItem(**row) for row in rows],
        total=total,
        params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get(
    "/analysis-workspaces/{workspace_uuid}/turns/{turn_uuid}",
    response_model=SuccessEnvelope[TurnResponse],
    summary="查询完整对话轮次",
)
async def get_turn(
    workspace_uuid: UUID,
    turn_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    workspace = await _workspace(
        session,
        tenant_id=principal.tenant_id,
        workspace_uuid=workspace_uuid,
    )
    turn = await ConversationRepository().get_turn(
        session,
        tenant_id=principal.tenant_id,
        workspace_id=int(workspace["id"]),
        turn_uuid=str(turn_uuid),
    )
    if turn is None or turn["status"] != "completed" or not turn.get("response_payload"):
        raise BusinessError("TURN_NOT_READY", "对话轮次不存在或尚未完成", status_code=404)
    return SuccessEnvelope(
        data=TurnResponse(**turn["response_payload"]),
        request_id=request.state.request_id,
    )


@router.post(
    "/analysis-workspaces/{workspace_uuid}/turns/{turn_uuid}:regenerate",
    response_model=SuccessEnvelope[TurnResponse],
    status_code=201,
    summary="保留原回答并重新生成新轮次",
)
async def regenerate_turn(
    workspace_uuid: UUID,
    turn_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ],
):
    workspace = await _workspace(
        session,
        tenant_id=principal.tenant_id,
        workspace_uuid=workspace_uuid,
    )
    original = await ConversationRepository().get_turn(
        session,
        tenant_id=principal.tenant_id,
        workspace_id=int(workspace["id"]),
        turn_uuid=str(turn_uuid),
    )
    if original is None or original["status"] != "completed":
        raise BusinessError("TURN_NOT_FOUND", "原对话轮次不存在或尚未完成", status_code=404)
    request_payload = dict(original["request_payload"])
    request_payload["client_turn_id"] = str(uuid5(
        NAMESPACE_URL,
        f"furniscope:{workspace_uuid}:{turn_uuid}:{idempotency_key}",
    ))
    request_payload.pop("regenerated_from_turn_uuid", None)
    body = TurnCreateRequest.model_validate(request_payload)
    service = TurnService(request.app.state.settings)
    turn = None
    try:
        turn, created = await service.reserve(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            workspace_uuid=str(workspace_uuid),
            idempotency_key=idempotency_key,
            body=body,
            regenerated_from_turn_uuid=str(turn_uuid),
        )
        await session.commit()
        if not created:
            payload = turn["response_payload"]
        else:
            payload = await service.answer(
                session,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                workspace_uuid=str(workspace_uuid),
                turn_uuid=turn["turn_uuid"],
                body=body,
            )
            await session.commit()
    except Exception:
        await session.rollback()
        if turn and turn.get("turn_uuid"):
            await ConversationRepository().fail_turn(
                session,
                tenant_id=principal.tenant_id,
                workspace_id=int(workspace["id"]),
                turn_uuid=turn["turn_uuid"],
                error_code="TURN_REGENERATION_FAILED",
            )
            await session.commit()
        raise
    return SuccessEnvelope(data=TurnResponse(**payload), request_id=request.state.request_id)


@router.post(
    "/analysis-workspaces/{workspace_uuid}/turns/{turn_uuid}:cancel",
    response_model=SuccessEnvelope[dict],
    summary="取消尚未完成的对话轮次",
)
async def cancel_turn(
    workspace_uuid: UUID,
    turn_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    workspace = await _workspace(
        session,
        tenant_id=principal.tenant_id,
        workspace_uuid=workspace_uuid,
    )
    cancelled = await ConversationRepository().cancel_turn(
        session,
        tenant_id=principal.tenant_id,
        workspace_id=int(workspace["id"]),
        turn_uuid=str(turn_uuid),
    )
    await session.commit()
    if not cancelled:
        raise BusinessError("TURN_NOT_CANCELLABLE", "对话轮次不存在或已结束", status_code=409)
    return SuccessEnvelope(
        data={"turn_uuid": str(turn_uuid), "cancelled": True},
        request_id=request.state.request_id,
    )


@router.get(
    "/analysis-workspaces/{workspace_uuid}/context",
    response_model=SuccessEnvelope[WorkspaceContextResponse],
    summary="读取工作台当前上下文绑定",
)
async def get_workspace_context(
    workspace_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    workspace = await _workspace(
        session,
        tenant_id=principal.tenant_id,
        workspace_uuid=workspace_uuid,
    )
    config, revision, context_uuid = await ConversationContextService(
        request.app.state.settings
    ).get_config(session, tenant_id=principal.tenant_id, workspace=workspace)
    return SuccessEnvelope(
        data=WorkspaceContextResponse(
            **config.model_dump(mode="python"),
            revision=revision or 0,
            context_uuid=context_uuid,
        ),
        request_id=request.state.request_id,
    )


@router.put(
    "/analysis-workspaces/{workspace_uuid}/context",
    response_model=SuccessEnvelope[WorkspaceContextResponse],
    summary="创建工作台上下文的新版本",
)
async def put_workspace_context(
    workspace_uuid: UUID,
    body: WorkspaceContextConfig,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    workspace = await _workspace(
        session,
        tenant_id=principal.tenant_id,
        workspace_uuid=workspace_uuid,
    )
    service = ConversationContextService(request.app.state.settings)
    await service.validate_config(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        config=body,
        workspace_uuid=str(workspace_uuid),
    )
    saved = await ConversationRepository().put_context(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        workspace_id=int(workspace["id"]),
        configuration=body.model_dump(mode="json"),
    )
    await session.commit()
    return SuccessEnvelope(
        data=WorkspaceContextResponse(
            **saved["configuration"],
            revision=saved["revision"],
            context_uuid=saved["context_uuid"],
        ),
        request_id=request.state.request_id,
    )


@router.post(
    "/analysis-workspaces/{workspace_uuid}/context:preview",
    response_model=SuccessEnvelope[ContextPreviewResponse],
    summary="预览下一轮会使用的真实上下文来源",
)
async def preview_workspace_context(
    workspace_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    body: ContextPreviewRequest = Body(default_factory=ContextPreviewRequest),
):
    context = await ConversationContextService(request.app.state.settings).build(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        workspace_uuid=str(workspace_uuid),
        question=body.query or "",
        streaming=False,
        configuration_override=body.configuration,
    )
    return SuccessEnvelope(
        data=ContextPreviewResponse(
            sources=context.sources,
            estimated_tokens=context.estimated_tokens,
            truncated_sources=context.truncated_sources,
            resolved_state=ConversationStateItem(**context.resolved_state),
            conflicts=context.conflicts,
            resolution_log=context.resolution_log,
        ),
        request_id=request.state.request_id,
    )


@router.get(
    "/analysis-workspaces/{workspace_uuid}/state",
    response_model=SuccessEnvelope[ConversationStateItem],
    summary="读取服务端维护的工作台多轮语义状态",
)
async def get_workspace_state(
    workspace_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    workspace = await _workspace(
        session,
        tenant_id=principal.tenant_id,
        workspace_uuid=workspace_uuid,
    )
    state = await ConversationRepository().get_state(
        session,
        tenant_id=principal.tenant_id,
        workspace_id=int(workspace["id"]),
    )
    return SuccessEnvelope(
        data=ConversationStateItem(**state),
        request_id=request.state.request_id,
    )


@router.get(
    "/customer-memories",
    response_model=SuccessEnvelope[PageData[CustomerMemoryItem]],
    summary="查询客户记忆资源",
)
async def list_customer_memories(
    request: Request,
    session: DatabaseSession,
    pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    status: Annotated[MemoryStatus | None, Query()] = None,
    scope: Annotated[MemoryScope | None, Query()] = None,
    workspace_uuid: UUID | None = None,
    memory_type: Annotated[str | None, Query(max_length=64)] = None,
):
    rows, total = await MemoryRepository().list(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        offset=pagination.offset,
        limit=pagination.page_size,
        status=status,
        scope=scope,
        workspace_uuid=str(workspace_uuid) if workspace_uuid else None,
        memory_type=memory_type,
    )
    data = PageData[CustomerMemoryItem].build(
        items=[CustomerMemoryItem(**row) for row in rows],
        total=total,
        params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get(
    "/customer-memories/{memory_uuid}",
    response_model=SuccessEnvelope[CustomerMemoryItem],
    summary="读取客户记忆",
)
async def get_customer_memory(
    memory_uuid: UUID,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    item = await MemoryRepository().get(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        memory_uuid=str(memory_uuid),
    )
    if item is None:
        raise BusinessError("MEMORY_NOT_FOUND", "客户记忆不存在", status_code=404)
    return SuccessEnvelope(data=CustomerMemoryItem(**item), request_id=request.state.request_id)


@router.patch(
    "/customer-memories/{memory_uuid}",
    response_model=SuccessEnvelope[CustomerMemoryItem],
    summary="编辑候选记忆；编辑已确认记忆会创建新候选版本",
)
async def patch_customer_memory(
    memory_uuid: UUID,
    body: CustomerMemoryPatchRequest,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    workspace_id = None
    if body.scope == "workspace" or body.workspace_uuid:
        if body.workspace_uuid is None:
            raise BusinessError("MEMORY_SCOPE_INVALID", "工作台记忆必须指定 workspace_uuid", status_code=422)
        workspace = await _workspace(
            session,
            tenant_id=principal.tenant_id,
            workspace_uuid=body.workspace_uuid,
        )
        workspace_id = int(workspace["id"])
    policy = await MemoryRepository().get_policy(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
    )
    item = await MemoryRepository().patch(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        memory_uuid=str(memory_uuid),
        value=body.value,
        scope=body.scope,
        workspace_id=workspace_id,
        retention_days=int(policy["retention_days"]),
        expires_at=body.expires_at,
        expires_at_set="expires_at" in body.model_fields_set,
        sensitivity=body.sensitivity,
    )
    if item is None:
        raise BusinessError("MEMORY_NOT_EDITABLE", "记忆不存在或当前状态不可编辑", status_code=409)
    await session.commit()
    return SuccessEnvelope(data=CustomerMemoryItem(**item), request_id=request.state.request_id)


async def _change_memory_status(
    action: Literal["confirm", "reject", "archive"],
    *,
    memory_uuid: UUID,
    request: Request,
    session,
    principal: AuthenticatedPrincipal,
):
    repository = MemoryRepository()
    method = getattr(repository, action)
    item = await method(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        memory_uuid=str(memory_uuid),
    )
    if item is None:
        raise BusinessError("MEMORY_STATE_CONFLICT", "记忆不存在或当前状态不允许该操作", status_code=409)
    await session.commit()
    return SuccessEnvelope(data=CustomerMemoryItem(**item), request_id=request.state.request_id)


@router.post("/customer-memories/{memory_uuid}:confirm", response_model=SuccessEnvelope[CustomerMemoryItem])
async def confirm_customer_memory(memory_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return await _change_memory_status(
        "confirm", memory_uuid=memory_uuid, request=request, session=session, principal=principal
    )


@router.post("/customer-memories/{memory_uuid}:reject", response_model=SuccessEnvelope[CustomerMemoryItem])
async def reject_customer_memory(memory_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return await _change_memory_status(
        "reject", memory_uuid=memory_uuid, request=request, session=session, principal=principal
    )


@router.delete("/customer-memories/{memory_uuid}", response_model=SuccessEnvelope[CustomerMemoryItem])
async def delete_customer_memory(memory_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return await _change_memory_status(
        "archive", memory_uuid=memory_uuid, request=request, session=session, principal=principal
    )


@router.get(
    "/customer-memories/{memory_uuid}/history",
    response_model=SuccessEnvelope[list[CustomerMemoryItem]],
)
async def customer_memory_history(memory_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    rows = await MemoryRepository().history(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        memory_uuid=str(memory_uuid),
    )
    if not rows:
        raise BusinessError("MEMORY_NOT_FOUND", "客户记忆不存在", status_code=404)
    return SuccessEnvelope(
        data=[CustomerMemoryItem(**item) for item in rows],
        request_id=request.state.request_id,
    )


@router.post(
    "/customer-memories:batch-confirm",
    response_model=SuccessEnvelope[list[CustomerMemoryItem]],
)
async def batch_confirm_customer_memories(
    body: CustomerMemoryBatchConfirmRequest,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    repository = MemoryRepository()
    items = []
    for memory_uuid in dict.fromkeys(body.memory_uuids):
        item = await repository.confirm(
            session,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            memory_uuid=str(memory_uuid),
        )
        if item is None:
            await session.rollback()
            raise BusinessError("MEMORY_STATE_CONFLICT", f"记忆 {memory_uuid} 无法确认", status_code=409)
        items.append(CustomerMemoryItem(**item))
    await session.commit()
    return SuccessEnvelope(data=items, request_id=request.state.request_id)


@router.get("/customer-memory-policy", response_model=SuccessEnvelope[CustomerMemoryPolicy])
async def get_customer_memory_policy(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    policy = await MemoryRepository().get_policy(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
    )
    if not request.app.state.settings.memory_auto_extract_enabled:
        policy["auto_extract"] = False
    return SuccessEnvelope(data=CustomerMemoryPolicy(**policy), request_id=request.state.request_id)


@router.put("/customer-memory-policy", response_model=SuccessEnvelope[CustomerMemoryPolicy])
async def put_customer_memory_policy(body: CustomerMemoryPolicy, request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    policy_payload = body.model_dump(mode="json")
    if not request.app.state.settings.memory_auto_extract_enabled:
        policy_payload["auto_extract"] = False
    policy = await MemoryRepository().put_policy(
        session,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        policy=policy_payload,
    )
    await session.commit()
    return SuccessEnvelope(data=CustomerMemoryPolicy(**policy), request_id=request.state.request_id)


@router.get("/citations/{citation_uuid}", response_model=SuccessEnvelope[CitationItem])
async def get_citation(citation_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    citation = await ConversationRepository().get_citation(
        session,
        tenant_id=principal.tenant_id,
        citation_uuid=str(citation_uuid),
    )
    if citation is None:
        raise BusinessError("CITATION_NOT_FOUND", "引用不存在或不可访问", status_code=404)
    return SuccessEnvelope(data=CitationItem(**citation), request_id=request.state.request_id)
