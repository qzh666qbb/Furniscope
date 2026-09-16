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
from ..schemas import PageData, SuccessEnvelope
from ..schemas.workspaces import (
    WorkbenchChatRequest, WorkbenchChatResponse,
    WorkspaceArchiveResponse, WorkspaceCreateRequest, WorkspaceListItem,
    WorkspaceMessageCreateRequest, WorkspaceMessageItem,
)
from ..services.chat_sse import stream_chat_events
from ..services.model_router_client import ServiceModelRouterClient
from ..services.workbench_chat import (
    build_workbench_thinking, catalog_context, local_workbench_chat_answer,
    parse_model_chat_text, workbench_system_prompt,
)

router = APIRouter(prefix="/api/v1/analysis-workspaces", tags=["Analysis Workspaces"])


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
    }


def _finalize_workbench_answer(answer_text: str, fallback: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_model_chat_text(answer_text) or {}
    if parsed.get("answer"):
        return _normalize_chat_output(parsed, fallback)
    return _normalize_chat_output({"answer": answer_text}, fallback)


async def _workbench_chat_context(body: WorkbenchChatRequest, session, tenant_id: int, *, streaming: bool):
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
    fallback = local_workbench_chat_answer(
        body.question, products=products, datasets=datasets,
        selected_product=selected_product, selected_dataset=selected_dataset,
    )
    thinking = build_workbench_thinking(
        body.question, products=products, datasets=datasets,
        selected_product=selected_product, selected_dataset=selected_dataset,
    )
    context = catalog_context(products, datasets, selected_product, selected_dataset)
    messages = [{
        "role": "system",
        "content": workbench_system_prompt(context, streaming=streaming),
    }]
    messages.extend(
        {"role": item.get("role", "user"), "content": item.get("content", "")[:2000]}
        for item in body.history[-8:] if item.get("content")
    )
    messages.append({"role": "user", "content": body.question})
    return fallback, thinking, messages


@router.post("/chat", response_model=SuccessEnvelope[WorkbenchChatResponse],
             summary="分析工作台对话：核对目录与市场数据后再回答")
async def workbench_chat(body: WorkbenchChatRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    fallback, _thinking, messages = await _workbench_chat_context(
        body, session, principal.tenant_id, streaming=False,
    )
    client = ServiceModelRouterClient(request.app.state.settings)
    try:
        output = await client.structured(messages=messages, output_type=dict)
        payload = _normalize_chat_output(output if isinstance(output, dict) else {}, fallback)
        data = WorkbenchChatResponse(**payload)
    except Exception:
        data = WorkbenchChatResponse(**fallback)
    finally:
        await client.close()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/chat/stream", summary="分析工作台流式对话（思考过程 + 回答）")
@router.post("/chat:stream", include_in_schema=False)
async def workbench_chat_stream(body: WorkbenchChatRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    fallback, thinking, messages = await _workbench_chat_context(
        body, session, principal.tenant_id, streaming=True,
    )
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
    pagination: Pagination, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    workspace = await WorkspaceRepository().get(
        session, tenant_id=principal.tenant_id, workspace_uuid=str(workspace_uuid),
    )
    if workspace is None:
        data = PageData[WorkspaceMessageItem].build(items=[], total=0, params=pagination)
        return SuccessEnvelope(data=data, request_id=request.state.request_id)
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
