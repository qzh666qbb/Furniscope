"""AI workbench persistence endpoints."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..errors import BusinessError
from ..repositories.dataset_repository import DatasetRepository
from ..repositories.product_repository import ProductRepository
from ..repositories.workspace_repository import WorkspaceRepository
from ..repositories.enterprise_repository import EnterpriseRepository
from ..schemas import PageData, SuccessEnvelope
from ..schemas.workspaces import (
    WorkbenchChatRequest, WorkbenchChatResponse,
    WorkspaceArchiveResponse, WorkspaceCreateRequest, WorkspaceListItem,
    WorkspaceMessageCreateRequest, WorkspaceMessageItem,
    CustomerMemoryItem, CustomerMemoryList,
)
from ..services.chat_sse import stream_chat_events
from ..services.model_router_client import ServiceModelRouterClient
from ..services.workbench_chat import (
    build_workbench_thinking, catalog_context, local_workbench_chat_answer,
    parse_model_chat_text, workbench_system_prompt,
    enrich_catalog_context,
)
from ..services.customer_memory import extract_memory_candidates, memory_context

router = APIRouter(prefix="/api/v1/analysis-workspaces", tags=["Analysis Workspaces"])


@router.get("/memories", response_model=SuccessEnvelope[CustomerMemoryList], include_in_schema=False,
            summary="查看当前账号的已确认客户记忆")
async def list_customer_memories(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    workspace_uuid: UUID | None = None):
    repo = WorkspaceRepository()
    workspace_id = None
    if workspace_uuid:
        workspace = await repo.get(session, tenant_id=principal.tenant_id, workspace_uuid=str(workspace_uuid))
        workspace_id = workspace["id"] if workspace else None
    rows = await repo.list_memory(session, tenant_id=principal.tenant_id,
                                  user_id=principal.user_id, workspace_id=workspace_id,
                                  include_candidates=True)
    return SuccessEnvelope(data=CustomerMemoryList(items=[CustomerMemoryItem(**row) for row in rows]),
                           request_id=request.state.request_id)


@router.delete("/memories/{memory_key}", response_model=SuccessEnvelope[dict], include_in_schema=False,
               summary="删除一条客户记忆")
async def delete_customer_memory(memory_key: str, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    workspace_uuid: UUID | None = None):
    repo = WorkspaceRepository()
    workspace_id = None
    if workspace_uuid:
        workspace = await repo.get(session, tenant_id=principal.tenant_id, workspace_uuid=str(workspace_uuid))
        workspace_id = workspace["id"] if workspace else None
    archived = await repo.archive_memory(session, tenant_id=principal.tenant_id,
                                         user_id=principal.user_id, memory_key=memory_key,
                                         workspace_id=workspace_id)
    await session.commit()
    return SuccessEnvelope(data={"memory_key": memory_key, "archived": archived}, request_id=request.state.request_id)


@router.post("/memories/{memory_key}:confirm", response_model=SuccessEnvelope[dict], include_in_schema=False,
             summary="确认一条候选客户记忆")
async def confirm_customer_memory(memory_key: str, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    workspace_uuid: UUID | None = None):
    repo = WorkspaceRepository()
    workspace_id = None
    if workspace_uuid:
        workspace = await repo.get(session, tenant_id=principal.tenant_id, workspace_uuid=str(workspace_uuid))
        workspace_id = workspace["id"] if workspace else None
    confirmed = await repo.set_memory_status(session, tenant_id=principal.tenant_id,
                                              user_id=principal.user_id, memory_key=memory_key,
                                              status="confirmed", workspace_id=workspace_id)
    await session.commit()
    return SuccessEnvelope(data={"memory_key": memory_key, "confirmed": confirmed}, request_id=request.state.request_id)


@router.post("/memories/{memory_key}:reject", response_model=SuccessEnvelope[dict], include_in_schema=False,
             summary="拒绝一条候选客户记忆")
async def reject_customer_memory(memory_key: str, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    workspace_uuid: UUID | None = None):
    repo = WorkspaceRepository()
    workspace_id = None
    if workspace_uuid:
        workspace = await repo.get(session, tenant_id=principal.tenant_id, workspace_uuid=str(workspace_uuid))
        workspace_id = workspace["id"] if workspace else None
    rejected = await repo.set_memory_status(session, tenant_id=principal.tenant_id,
                                             user_id=principal.user_id, memory_key=memory_key,
                                             status="archived", workspace_id=workspace_id)
    await session.commit()
    return SuccessEnvelope(data={"memory_key": memory_key, "rejected": rejected}, request_id=request.state.request_id)


def _normalize_chat_output(output: dict, fallback: dict) -> dict:
    answer = str(output.get("answer") or "").strip()
    if not answer:
        return fallback
    prompts = output.get("suggested_prompts") or fallback.get("suggested_prompts") or []
    if not isinstance(prompts, list):
        prompts = fallback.get("suggested_prompts") or []
    candidates = output.get("product_candidates")
    if not isinstance(candidates, list):
        candidates = fallback.get("product_candidates") or []
    cleaned_candidates = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("product_id")
        try:
            product_id = int(raw_id) if raw_id is not None else None
        except (TypeError, ValueError):
            product_id = None
        cleaned_candidates.append({
            "product_id": product_id,
            "sku": item.get("sku"),
            "name": item.get("name"),
        })
    action = output.get("suggested_action") or fallback.get("suggested_action") or None
    if action == "":
        action = None
    return {
        "answer": answer,
        "title": output.get("title") or fallback.get("title"),
        "suggested_prompts": [str(item) for item in prompts if str(item).strip()][:6],
        "suggested_action": action,
        "action_label": output.get("action_label") or fallback.get("action_label") or None,
        "action_href": output.get("action_href") or fallback.get("action_href") or None,
        "product_candidates": cleaned_candidates,
        "missing_market": output.get("missing_market") or fallback.get("missing_market"),
        "memory_updates": output.get("memory_updates") if isinstance(output.get("memory_updates"), list) else fallback.get("memory_updates", []),
        "context_sources": output.get("context_sources") if isinstance(output.get("context_sources"), list) else fallback.get("context_sources", []),
    }


def _finalize_workbench_answer(answer_text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_model_chat_text(answer_text) or {}
    if parsed.get("answer"):
        return _normalize_chat_output(parsed, fallback)
    return _normalize_chat_output({"answer": answer_text}, fallback)


def _context_sources(messages: list[dict[str, Any]], memory_updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    system = messages[0].get("content", "") if messages else ""
    return [
        {"type": "workspace_history", "label": "工作台历史", "used": max(0, len(messages) - 2)},
        {"type": "customer_memory", "label": "客户记忆", "used": len(memory_updates)},
        {"type": "enterprise_profile", "label": "企业画像", "used": 1 if "enterprise_profile" in system else 0},
        {"type": "product_profile", "label": "产品画像", "used": 1 if "selected_product_profile" in system else 0},
        {"type": "authorized_data", "label": "授权市场数据", "used": 1 if "datasets" in system else 0},
    ]


async def _workbench_chat_context(body: WorkbenchChatRequest, session, tenant_id: int, user_id: int, *, streaming: bool):
    products, _ = await ProductRepository().list(
        session, tenant_id=tenant_id, offset=0, limit=100,
        analysis_status=None, category_code=None, keyword=None,
    )
    datasets, _ = await DatasetRepository().list(
        session, tenant_id=tenant_id, offset=0, limit=100,
        platform=None, market_country=None, category_code=None, status=None,
    )
    selected_product = next((item for item in products if item.get("product_id") == body.product_id), None)
    selected_dataset = next((item for item in datasets if item.get("dataset_id") == body.dataset_id), None)
    repo = WorkspaceRepository()
    workspace = None
    if body.workspace_uuid:
        workspace = await repo.get(session, tenant_id=tenant_id, workspace_uuid=str(body.workspace_uuid))
    stored_history = []
    if workspace:
        stored_history, _ = await repo.list_messages(
            session, tenant_id=tenant_id, workspace_id=workspace["id"], offset=0, limit=100,
        )
        stored_history = [
            {"role": item["role"], "content": item["content"]}
            for item in stored_history if item.get("role") in {"user", "assistant"}
        ]
    memories = await repo.list_memory(
        session, tenant_id=tenant_id,
        user_id=user_id,
        workspace_id=workspace["id"] if workspace else None,
    )
    enterprise_profile = await EnterpriseRepository().get_profile(session, tenant_id=tenant_id)
    product_profile = None
    if selected_product and selected_product.get("current_profile_version_id"):
        product_profile = await ProductRepository().get_profile(
            session, tenant_id=tenant_id, product_id=int(selected_product["product_id"]),
            profile_version_id=int(selected_product["current_profile_version_id"]),
        )
        if product_profile:
            product_profile["attributes"] = await ProductRepository().attributes(
                session, tenant_id=tenant_id, profile_version_id=int(product_profile["profile_version_id"]),
            )
    fallback = local_workbench_chat_answer(
        body.question, products=products, datasets=datasets,
        selected_product=selected_product, selected_dataset=selected_dataset,
    )
    thinking = build_workbench_thinking(
        body.question, products=products, datasets=datasets,
        selected_product=selected_product, selected_dataset=selected_dataset,
    )
    context = enrich_catalog_context(
        catalog_context(products, datasets, selected_product, selected_dataset),
        enterprise_profile=enterprise_profile, product_profile=product_profile,
        memories=memory_context(memories),
    )
    messages = [{
        "role": "system",
        "content": workbench_system_prompt(context, streaming=streaming),
    }]
    combined_history = stored_history[-12:] if stored_history else body.history[-12:]
    messages.extend(
        {"role": item.get("role", "user"), "content": item.get("content", "")[:2000]}
        for item in combined_history[-8:] if item.get("content")
    )
    messages.append({"role": "user", "content": body.question})
    return fallback, thinking, messages


async def _persist_memory_candidates(body: WorkbenchChatRequest, session, *, tenant_id: int,
                                     user_id: int, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not body.workspace_uuid or not candidates:
        return []
    repo = WorkspaceRepository()
    workspace = await repo.get(session, tenant_id=tenant_id, workspace_uuid=str(body.workspace_uuid))
    if not workspace:
        return []
    saved = []
    for candidate in candidates[:5]:
        saved.append(await repo.upsert_memory(
            session, tenant_id=tenant_id, user_id=user_id, workspace_id=workspace["id"],
            memory=candidate, status="candidate",
        ))
    return saved


@router.post("/chat", response_model=SuccessEnvelope[WorkbenchChatResponse], include_in_schema=False,
             summary="分析工作台对话：核对目录与市场数据后再回答")
async def workbench_chat(body: WorkbenchChatRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    fallback, _thinking, messages = await _workbench_chat_context(
        body, session, principal.tenant_id, principal.user_id, streaming=False,
    )
    saved_memory = []
    if request.app.state.settings.memory_auto_extract_enabled:
        saved_memory = await _persist_memory_candidates(
            body, session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            candidates=extract_memory_candidates(body.question),
        )
    client = ServiceModelRouterClient(request.app.state.settings)
    try:
        output = await client.structured(messages=messages, output_type=dict)
        payload = _normalize_chat_output(output if isinstance(output, dict) else {}, fallback)
        payload["memory_updates"] = saved_memory
        payload["context_sources"] = _context_sources(messages, saved_memory)
        data = WorkbenchChatResponse(**payload)
    except Exception:
        fallback["memory_updates"] = saved_memory
        fallback["context_sources"] = _context_sources(messages, saved_memory)
        data = WorkbenchChatResponse(**fallback)
    finally:
        await session.commit()
        await client.close()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/chat/stream", summary="分析工作台流式对话（兼容接口）", include_in_schema=False)
@router.post("/chat:stream", include_in_schema=False)
async def workbench_chat_stream(body: WorkbenchChatRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    fallback, thinking, messages = await _workbench_chat_context(
        body, session, principal.tenant_id, principal.user_id, streaming=True,
    )
    saved_memory = []
    if request.app.state.settings.memory_auto_extract_enabled:
        saved_memory = await _persist_memory_candidates(
            body, session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            candidates=extract_memory_candidates(body.question),
        )
    fallback["memory_updates"] = saved_memory
    fallback["context_sources"] = _context_sources(messages, saved_memory)
    await session.commit()
    client = ServiceModelRouterClient(request.app.state.settings)
    return StreamingResponse(
        stream_chat_events(
            thinking=thinking, messages=messages, client=client, fallback=fallback,
            finalize=lambda text: _finalize_workbench_answer(text, fallback),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


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
    pagination: Pagination, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    before_seq: Annotated[int | None, Query(ge=1)] = None,
    after_seq: Annotated[int | None, Query(ge=0)] = None,
    message_kind: Annotated[str | None, Query(max_length=24)] = None,
    task_uuid: UUID | None = None):
    workspace = await WorkspaceRepository().get(
        session, tenant_id=principal.tenant_id, workspace_uuid=str(workspace_uuid),
    )
    if workspace is None:
        data = PageData[WorkspaceMessageItem].build(items=[], total=0, params=pagination)
        return SuccessEnvelope(data=data, request_id=request.state.request_id)
    rows, total = await WorkspaceRepository().list_messages(
        session, tenant_id=principal.tenant_id, workspace_id=workspace["id"],
        offset=pagination.offset, limit=pagination.page_size,
        before_seq=before_seq, after_seq=after_seq, message_kind=message_kind,
        task_uuid=str(task_uuid) if task_uuid else None,
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
        saved = await repo.upsert(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            payload={"workspace_uuid": str(workspace_uuid), "name": "未命名工作台", "source": "node_workflow_canvas"},
        )
        workspace = await repo.get(
            session, tenant_id=principal.tenant_id, workspace_uuid=str(saved["workspace_uuid"]),
        )
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
