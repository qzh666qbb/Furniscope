"""Atomic conversation turns shared by workspace and task-bound chat."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.conversation_repository import ConversationRepository
from ..repositories.memory_repository import MemoryRepository
from ..schemas.context_lifecycle import TurnCreateRequest
from .chat_sse import sse, text_chunks
from .agent_orchestrator import AgentToolOrchestrator
from .conversation_context import ConversationContext, ConversationContextService
from .customer_memory import extract_memory_candidates, requested_forget_keys
from .idempotency_service import canonical_request_hash
from .model_router_client import ServiceModelRouterClient
from .workbench_chat import parse_model_chat_text


def _suggested_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions = payload.get("suggested_actions")
    if isinstance(actions, list):
        return [item for item in actions if isinstance(item, dict)]
    output = [
        {"action": "send_prompt", "label": str(prompt), "value": str(prompt)}
        for prompt in (payload.get("suggested_prompts") or [])
        if str(prompt).strip()
    ][:6]
    action = payload.get("suggested_action")
    if not action:
        return output
    item: dict[str, Any] = {"action": str(action)}
    if payload.get("action_label"):
        item["label"] = payload["action_label"]
    if payload.get("action_href"):
        item["href"] = payload["action_href"]
    return [item, *output]


def _finalize_answer(raw: str, fallback: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    parsed = parse_model_chat_text(raw) or {}
    answer = str(parsed.get("answer") or raw or fallback.get("answer") or "").strip()
    if not answer:
        answer = "当前证据不足，暂时无法回答这个问题。"
    actions = _suggested_actions(parsed or fallback)
    if not actions:
        actions = _suggested_actions(fallback)
    return answer, actions


def _memory_label(memory: dict[str, Any]) -> str:
    labels = {
        "target_market": "目标市场",
        "budget": "预算",
        "unit_cost_limit": "单位成本上限",
        "preferred_channel": "偏好渠道",
        "customer_preference": "客户偏好",
    }
    value = memory.get("value") or {}
    rendered = value.get("value") if isinstance(value, dict) else value
    if (
        memory.get("memory_type") in {"budget", "unit_cost_limit"}
        and isinstance(value, dict)
        and value.get("amount") is not None
    ):
        rendered = f"{value['amount']} {value.get('currency') or ''}".strip()
    return f"{labels.get(memory['memory_type'], memory['memory_type'])}：{rendered}"


class TurnService:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.repository = ConversationRepository()
        self.memories = MemoryRepository()
        self.contexts = ConversationContextService(settings)

    async def reserve(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_uuid: str,
        idempotency_key: str,
        body: TurnCreateRequest,
        regenerated_from_turn_uuid: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        workspace = await self._workspace(
            session,
            tenant_id=tenant_id,
            workspace_uuid=workspace_uuid,
        )
        payload = body.model_dump(mode="json")
        if regenerated_from_turn_uuid:
            payload["regenerated_from_turn_uuid"] = regenerated_from_turn_uuid
        request_hash = canonical_request_hash(payload)
        turn, created = await self.repository.reserve_turn(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=int(workspace["id"]),
            client_turn_id=str(body.client_turn_id),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_payload=payload,
            task_uuid=str(body.task_uuid) if body.task_uuid else None,
            regenerated_from_turn_uuid=regenerated_from_turn_uuid,
        )
        if not turn:
            raise BusinessError("TURN_CONTEXT_INVALID", "任务不存在或不属于当前租户", status_code=422)
        if not created:
            if turn.get("blocking_turn_uuid"):
                raise BusinessError(
                    "WORKSPACE_TURN_IN_PROGRESS",
                    "当前工作台已有一轮问询正在处理，请等待完成后再发送",
                    status_code=409,
                    details=[{
                        "turn_uuid": turn["blocking_turn_uuid"],
                        "sequence_no": turn["blocking_sequence_no"],
                    }],
                )
            if turn["request_hash"] != request_hash:
                raise BusinessError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求", status_code=409)
            if turn["status"] == "completed" and turn.get("response_payload"):
                return turn, False
            raise BusinessError("TURN_ALREADY_EXISTS", "相同对话轮次正在处理或已结束", status_code=409)
        return turn, True

    async def answer(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_uuid: str,
        turn_uuid: str,
        body: TurnCreateRequest,
    ) -> dict[str, Any]:
        context = await self.contexts.build(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_uuid=workspace_uuid,
            question=body.question,
            product_id=body.product_id,
            dataset_id=body.dataset_id,
            task_uuid=str(body.task_uuid) if body.task_uuid else None,
            streaming=False,
        )
        tool_results: list[dict[str, Any]] = []
        forget_action = await self._forget_action(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=int(context.workspace["id"]),
            question=body.question,
        )
        if forget_action:
            answer = "我找到了需要忘记的已确认记忆。删除会影响后续上下文，请先确认具体条目。"
            actions = [forget_action]
        else:
            decision = await AgentToolOrchestrator(self.settings).route(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=int(context.workspace["id"]),
                turn_uuid=turn_uuid,
                question=body.question,
                context=context,
            )
            self._attach_tool_evidence(context, decision.sources, decision.citations)
            tool_results = decision.tool_results
            if decision.answer is not None:
                answer, actions = decision.answer, decision.actions
            else:
                client = ServiceModelRouterClient(self.settings)
                try:
                    output = await client.structured(messages=context.messages, output_type=dict)
                    raw = json.dumps(output, ensure_ascii=False) if isinstance(output, dict) else ""
                    answer, actions = _finalize_answer(raw, context.fallback)
                    actions = [*decision.actions, *actions]
                except Exception:
                    answer, actions = _finalize_answer("", context.fallback)
                    actions = [*decision.actions, *actions]
                finally:
                    await client.close()
        return await self._persist(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            turn_uuid=turn_uuid,
            body=body,
            context=context,
            answer=answer,
            actions=actions,
            tool_results=tool_results,
        )

    async def stream(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_uuid: str,
        turn_uuid: str,
        body: TurnCreateRequest,
    ) -> AsyncIterator[str]:
        client: ServiceModelRouterClient | None = None
        try:
            context = await self.contexts.build(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_uuid=workspace_uuid,
                question=body.question,
                product_id=body.product_id,
                dataset_id=body.dataset_id,
                task_uuid=str(body.task_uuid) if body.task_uuid else None,
                streaming=True,
            )
            yield ": connected\n\n"
            yield sse(
                "turn_started",
                {"turn_uuid": turn_uuid},
                event_id=f"{turn_uuid}:started",
            )
            yield sse("progress", {
                "stage": "context_ready",
                "message": f"已加载 {len(context.sources)} 项可审计上下文",
            }, event_id=f"{turn_uuid}:context-ready")
            forget_action = await self._forget_action(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=int(context.workspace["id"]),
                question=body.question,
            )
            answer = ""
            actions: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            if forget_action:
                answer = "我找到了需要忘记的已确认记忆。删除会影响后续上下文，请先确认具体条目。"
                actions = [forget_action]
            else:
                decision = await AgentToolOrchestrator(self.settings).route(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=int(context.workspace["id"]),
                    turn_uuid=turn_uuid,
                    question=body.question,
                    context=context,
                )
                self._attach_tool_evidence(context, decision.sources, decision.citations)
                tool_results = decision.tool_results
                yield sse("progress", {
                    "stage": decision.progress_stage,
                    "message": decision.progress_message,
                }, event_id=f"{turn_uuid}:tool-route")
                if decision.answer is not None:
                    answer, actions = decision.answer, decision.actions
                else:
                    client = ServiceModelRouterClient(self.settings)
                    try:
                        async for delta in client.stream_chat(messages=context.messages):
                            content = str(delta.get("content") or "")
                            if not content:
                                continue
                            answer += content
                    except Exception:
                        answer = ""
                    if not answer.strip():
                        answer, actions = _finalize_answer("", context.fallback)
                    else:
                        answer, actions = _finalize_answer(answer, context.fallback)
                    actions = [*decision.actions, *actions]
            payload = await self._persist(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                turn_uuid=turn_uuid,
                body=body,
                context=context,
                answer=answer,
                actions=actions,
                tool_results=tool_results,
            )
            await session.commit()
            # Do not expose an answer that is not yet durable. The model call stays
            # outside the database transaction, then committed content is replayed as SSE.
            for index, part in enumerate(text_chunks(payload["answer"])):
                yield sse(
                    "answer_delta",
                    {"delta": part},
                    event_id=f"{turn_uuid}:answer:{index}",
                )
                await asyncio.sleep(0)
            for citation in payload["citations"]:
                yield sse(
                    "citation",
                    citation,
                    event_id=f"{turn_uuid}:citation:{citation['citation_uuid']}",
                )
            for index, result in enumerate(payload.get("tool_results") or []):
                yield sse(
                    "tool_result",
                    result,
                    event_id=f"{turn_uuid}:tool-result:{index}",
                )
            for candidate in payload["memory_candidates"]:
                yield sse(
                    "memory_candidate",
                    candidate,
                    event_id=f"{turn_uuid}:memory:{candidate['memory_uuid']}",
                )
            for index, action in enumerate(payload["suggested_actions"]):
                yield sse("action", action, event_id=f"{turn_uuid}:action:{index}")
            yield sse("done", payload, event_id=f"{turn_uuid}:done")
        except asyncio.CancelledError:
            await session.rollback()
            workspace = await self._workspace(
                session,
                tenant_id=tenant_id,
                workspace_uuid=workspace_uuid,
            )
            await self.repository.cancel_turn(
                session,
                tenant_id=tenant_id,
                workspace_id=int(workspace["id"]),
                turn_uuid=turn_uuid,
            )
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            try:
                workspace = await self._workspace(
                    session,
                    tenant_id=tenant_id,
                    workspace_uuid=workspace_uuid,
                )
                await self.repository.fail_turn(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=int(workspace["id"]),
                    turn_uuid=turn_uuid,
                    error_code="TURN_EXECUTION_FAILED",
                )
                await session.commit()
            except Exception:
                await session.rollback()
            yield sse(
                "error",
                {"code": "TURN_EXECUTION_FAILED", "message": "暂时无法完成这次问询。"},
                event_id=f"{turn_uuid}:error",
            )
        finally:
            if client is not None:
                await client.close()

    async def _persist(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        turn_uuid: str,
        body: TurnCreateRequest,
        context: ConversationContext,
        answer: str,
        actions: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        resolved_state = dict(context.resolved_state)
        pending_action = next(
            (
                item for item in actions
                if item.get("requires_confirmation") and item.get("action")
            ),
            None,
        )
        if pending_action:
            resolved_state["pending_confirmation"] = pending_action
        elif resolved_state.get("last_user_intent") not in {"pause_task"}:
            resolved_state["pending_confirmation"] = None
        payload = await self.repository.complete_turn(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=int(context.workspace["id"]),
            turn_uuid=turn_uuid,
            question=body.question,
            answer=answer,
            message_kind="task_chat" if body.task_uuid else "text",
            task_uuid=str(body.task_uuid) if body.task_uuid else None,
            context_revision=context.context_revision,
            context_configuration=context.configuration,
            context_sources=context.sources,
            estimated_tokens=context.estimated_tokens,
            truncated_sources=context.truncated_sources,
            context_hash=context.context_hash,
            citations=context.citations,
            suggested_actions=actions,
            resolved_state=resolved_state,
            state_revision=context.state_revision,
            resolution_log=context.resolution_log,
            context_conflicts=context.conflicts,
            tool_results=tool_results,
        )
        if payload is None:
            raise BusinessError("TURN_NOT_FOUND", "对话轮次不存在", status_code=404)
        if payload.get("status") == "completed" and payload.get("response_payload"):
            return payload["response_payload"]
        if payload.get("status") in {"cancelled", "failed"}:
            raise BusinessError("TURN_NOT_CANCELLABLE", "对话轮次已经结束", status_code=409)
        policy = await self.memories.get_policy(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        candidates = []
        if (
            self.settings.memory_auto_extract_enabled
            and policy["auto_extract"]
            and body.memory_mode != "temporary"
        ):
            memory_scope = (
                body.memory_mode
                if body.memory_mode in {"workspace", "user"}
                else policy["default_scope"]
            )
            extracted = [
                item for item in extract_memory_candidates(body.question)
                if item["memory_key"] in set(policy["allowed_types"])
            ]
            for memory in extracted[:5]:
                saved = await self.memories.create_candidate(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=int(context.workspace["id"]),
                    memory_type=memory["memory_key"],
                    value=memory["memory_value"],
                    confidence=float(memory.get("confidence") or 0.9),
                    source_message_uuid=payload["user_message_uuid"],
                    scope=memory_scope,
                    retention_days=int(policy["retention_days"]),
                )
                if not policy["confirmation_required"]:
                    saved = await self.memories.confirm(
                        session,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        memory_uuid=str(saved["memory_uuid"]),
                    ) or saved
                candidates.append(saved)
        payload["memory_candidates"] = candidates
        await self.repository.attach_memory_candidates(
            session,
            tenant_id=tenant_id,
            workspace_id=int(context.workspace["id"]),
            turn_uuid=turn_uuid,
            memory_candidates=candidates,
        )
        return payload

    @staticmethod
    def _attach_tool_evidence(
        context: ConversationContext,
        sources: list[dict[str, Any]],
        citations: list[dict[str, Any]],
    ) -> None:
        if not sources and not citations:
            return
        context.sources.extend(sources)
        context.sources.sort(
            key=lambda item: int(item.get("priority") or 0),
            reverse=True,
        )
        context.citations.extend(citations)
        context.context_hash = hashlib.sha256(
            json.dumps(
                {
                    "base_context_hash": context.context_hash,
                    "tool_sources": sources,
                    "tool_citations": citations,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()

    async def _forget_action(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_id: int,
        question: str,
    ) -> dict[str, Any] | None:
        keys = requested_forget_keys(question)
        if not keys:
            return None
        targets = await self.memories.find_confirmed_targets(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
            memory_types=keys,
        )
        if not targets:
            return {
                "action": "forget_memory",
                "requires_confirmation": True,
                "targets": [],
                "message": "没有找到匹配的已确认记忆",
            }
        return {
            "action": "forget_memory",
            "requires_confirmation": True,
            "targets": [
                {
                    "memory_uuid": item["memory_uuid"],
                    "label": _memory_label(item),
                }
                for item in targets
            ],
        }

    @staticmethod
    async def _workspace(
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_uuid: str,
    ) -> dict[str, Any]:
        from ..repositories.workspace_repository import WorkspaceRepository

        workspace = await WorkspaceRepository().get(
            session,
            tenant_id=tenant_id,
            workspace_uuid=workspace_uuid,
        )
        if workspace is None:
            raise BusinessError("WORKSPACE_NOT_FOUND", "工作台不存在或已删除", status_code=404)
        return workspace
