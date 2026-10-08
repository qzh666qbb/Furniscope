"""Build one auditable context for workspace-only and task-bound turns."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.conversation_repository import ConversationRepository
from ..repositories.dataset_repository import DatasetRepository
from ..repositories.enterprise_repository import EnterpriseRepository
from ..repositories.insight_repository import InsightRepository
from ..repositories.knowledge_repository import KnowledgeRepository
from ..repositories.memory_repository import MemoryRepository
from ..repositories.product_repository import ProductRepository
from ..repositories.workspace_repository import WorkspaceRepository
from ..schemas.context_lifecycle import WorkspaceContextConfig
from .analysis_task_service import AnalysisTaskService
from .customer_memory import memory_context
from .conversation_state import detect_context_conflicts, resolve_conversation_state
from .knowledge_service import KnowledgeService
from .task_chat import build_task_thinking, local_task_chat_answer
from .workbench_chat import (
    build_workbench_thinking,
    catalog_context,
    enrich_catalog_context,
    local_workbench_chat_answer,
    workbench_system_prompt,
)

_KNOWLEDGE_INTENT_RE = re.compile(
    r"知识库|文档|资料|说明书|手册|制度|规范|认证|合同|附件|根据.+(?:文件|材料)",
    re.I,
)


@dataclass(slots=True)
class ConversationContext:
    messages: list[dict[str, str]]
    fallback: dict[str, Any]
    progress_summary: str
    configuration: dict[str, Any]
    context_revision: int | None
    sources: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    estimated_tokens: int
    truncated_sources: list[dict[str, Any]]
    context_hash: str
    workspace: dict[str, Any]
    confirmed_memories: list[dict[str, Any]]
    resolved_state: dict[str, Any]
    state_revision: int
    conflicts: list[dict[str, Any]]
    resolution_log: list[dict[str, Any]]


def _token_estimate(value: Any) -> int:
    content = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return max(1, (len(content) + 2) // 3)


def _public_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": source["type"],
        "source_id": source["source_id"],
        "label": source["label"],
        "version": source.get("version"),
        "locator": source.get("locator") or {},
        "estimated_tokens": int(source.get("estimated_tokens") or 0),
        "priority": int(source.get("priority") or 0),
        "trust_level": source.get("trust_level") or "historical",
    }


def _citation(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "citation_uuid": str(uuid4()),
        "source_type": source["type"],
        "source_id": source["source_id"],
        "source_version": (
            str(source["version"]) if source.get("version") is not None else None
        ),
        "label": source["label"],
        "locator": source.get("locator") or {},
        "excerpt": source.get("excerpt"),
        "score": source.get("score"),
    }


class ConversationContextService:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.conversations = ConversationRepository()
        self.memories = MemoryRepository()

    async def default_config(
        self,
        session: AsyncSession,
        *,
        workspace: dict[str, Any],
    ) -> WorkspaceContextConfig:
        return WorkspaceContextConfig(product_id=workspace.get("product_id"))

    async def get_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace: dict[str, Any],
    ) -> tuple[WorkspaceContextConfig, int | None, str | None]:
        current = await self.conversations.get_context(
            session,
            tenant_id=tenant_id,
            workspace_id=int(workspace["id"]),
        )
        if current is None:
            return await self.default_config(session, workspace=workspace), None, None
        return (
            WorkspaceContextConfig.model_validate(current["configuration"]),
            int(current["revision"]),
            current["context_uuid"],
        )

    async def validate_config(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        config: WorkspaceContextConfig,
        workspace_uuid: str | None = None,
    ) -> None:
        if config.product_id is not None:
            exists = await session.scalar(text("""
                SELECT EXISTS(
                  SELECT FROM products WHERE tenant_id=:tenant_id AND id=:product_id
                    AND deleted_at IS NULL
                )
            """), {"tenant_id": tenant_id, "product_id": config.product_id})
            if not exists:
                raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        checks = (
            ("dataset_ids", config.dataset_ids, "market_datasets", "id::text", "deleted_at IS NULL", "数据集"),
            (
                "knowledge_base_uuids",
                [str(item) for item in config.knowledge_base_uuids],
                "knowledge_bases",
                "knowledge_base_uuid::text",
                "status='active' AND (visibility='tenant' OR created_by=:user_id)",
                "知识库",
            ),
            (
                "task_uuids",
                [str(item) for item in config.task_uuids],
                "analysis_tasks",
                "task_uuid::text",
                (
                    "status<>'cancelled'"
                    if workspace_uuid is None else
                    "status<>'cancelled' AND analysis_config->>'workspace_uuid'=:workspace_uuid"
                ),
                "任务",
            ),
        )
        for _name, values, table, column, active, label in checks:
            if not values:
                continue
            count = int(await session.scalar(text(f"""
                SELECT count(*) FROM {table}
                 WHERE tenant_id=:tenant_id AND {column}=ANY(CAST(:values AS text[]))
                   AND {active}
            """), {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "values": [str(item) for item in values],
                "workspace_uuid": workspace_uuid,
            }) or 0)
            if count != len(values):
                raise BusinessError("CONTEXT_RESOURCE_INVALID", f"存在不可访问的{label}", status_code=422)

    async def build(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_uuid: str,
        question: str,
        product_id: int | None = None,
        dataset_id: int | None = None,
        task_uuid: str | None = None,
        streaming: bool,
        configuration_override: WorkspaceContextConfig | None = None,
    ) -> ConversationContext:
        workspace = await WorkspaceRepository().get(
            session,
            tenant_id=tenant_id,
            workspace_uuid=workspace_uuid,
        )
        if workspace is None:
            raise BusinessError("WORKSPACE_NOT_FOUND", "工作台不存在或已删除", status_code=404)
        config, revision, _context_uuid = await self.get_config(
            session,
            tenant_id=tenant_id,
            workspace=workspace,
        )
        if configuration_override is not None:
            config = configuration_override
            revision = None

        products, _ = await ProductRepository().list(
            session,
            tenant_id=tenant_id,
            offset=0,
            limit=100,
            analysis_status=None,
            category_code=None,
            keyword=None,
        )
        datasets, _ = await DatasetRepository().list(
            session,
            tenant_id=tenant_id,
            offset=0,
            limit=100,
            platform=None,
            market_country=None,
            category_code=None,
            status=None,
        )
        persisted_state = await self.conversations.get_state(
            session,
            tenant_id=tenant_id,
            workspace_id=int(workspace["id"]),
        )
        resolved_state, resolution_log = resolve_conversation_state(
            persisted=persisted_state,
            question=question,
            products=products,
            datasets=datasets,
            configured_product_id=config.product_id,
            configured_dataset_ids=config.dataset_ids,
            configured_task_uuids=[str(item) for item in config.task_uuids],
            requested_product_id=product_id,
            requested_dataset_id=dataset_id,
            requested_task_uuid=task_uuid,
        )
        raw_config = config.model_dump(mode="json")
        raw_config["product_id"] = resolved_state.get("current_product_id")
        raw_config["dataset_ids"] = (
            [resolved_state["current_dataset_id"]]
            if resolved_state.get("current_dataset_id") else []
        )
        raw_config["task_uuids"] = (
            [resolved_state["current_task_uuid"]]
            if resolved_state.get("current_task_uuid") else []
        )
        config = WorkspaceContextConfig.model_validate(raw_config)
        await self.validate_config(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            config=config,
            workspace_uuid=workspace_uuid,
        )
        selected_product = None
        if config.product_id is not None:
            selected_product = await ProductRepository().get_product(
                session,
                tenant_id=tenant_id,
                product_id=config.product_id,
            )
        selected_datasets = []
        for bound_dataset_id in config.dataset_ids:
            item = await DatasetRepository().get(
                session,
                tenant_id=tenant_id,
                dataset_id=bound_dataset_id,
            )
            if item:
                selected_datasets.append(item)
        selected_dataset = selected_datasets[0] if selected_datasets else None

        sources: list[dict[str, Any]] = []
        truncated: list[dict[str, Any]] = []
        model_context: dict[str, Any] = {}
        question_content = question.strip()
        if question_content:
            sources.append({
                "type": "current_user_input",
                "source_id": "current-turn:question",
                "label": "当前用户明确表达",
                "version": None,
                "locator": {},
                "excerpt": question_content[:1000],
                "estimated_tokens": _token_estimate(question_content),
                "priority": 100,
                "trust_level": "authoritative",
            })
        state_content = json.dumps(resolved_state, ensure_ascii=False, default=str)
        sources.append({
            "type": "conversation_state",
            "source_id": f"workspace:{workspace_uuid}:state",
            "label": "工作台显式状态",
            "version": persisted_state.get("revision") or 0,
            "locator": {"workspace_uuid": workspace_uuid},
            "excerpt": state_content[:1000],
            "estimated_tokens": _token_estimate(state_content),
            "priority": 70,
            "trust_level": "historical",
        })
        model_context["conversation_state"] = resolved_state
        enterprise_profile = None
        if config.retrieval_policy.include_enterprise_profile:
            enterprise_profile = await EnterpriseRepository().get_profile(session, tenant_id=tenant_id)
            if enterprise_profile:
                content = json.dumps(enterprise_profile, ensure_ascii=False, default=str)
                sources.append({
                    "type": "enterprise_profile",
                    "source_id": f"enterprise_profile:{tenant_id}",
                    "label": "企业能力画像",
                    "version": enterprise_profile.get("profile_version"),
                    "locator": {},
                    "excerpt": content[:1000],
                    "estimated_tokens": _token_estimate(content),
                    "priority": 90,
                    "trust_level": "confirmed",
                })
                model_context["enterprise_profile"] = enterprise_profile

        product_profile = None
        if selected_product and config.retrieval_policy.include_product_profile:
            profile_id = selected_product.get("current_profile_version_id")
            if profile_id:
                product_profile = await ProductRepository().get_profile(
                    session,
                    tenant_id=tenant_id,
                    product_id=int(selected_product["product_id"]),
                    profile_version_id=int(profile_id),
                )
                if product_profile:
                    product_profile["attributes"] = await ProductRepository().attributes(
                        session,
                        tenant_id=tenant_id,
                        profile_version_id=int(profile_id),
                    )
                    content = json.dumps(product_profile, ensure_ascii=False, default=str)
                    sources.append({
                        "type": "product_profile",
                        "source_id": f"profile:{profile_id}",
                        "label": f"{selected_product['sku']} 产品画像",
                        "version": product_profile.get("profile_version"),
                        "locator": {"product_id": selected_product["product_id"]},
                        "excerpt": content[:1000],
                        "estimated_tokens": _token_estimate(content),
                        "priority": 90,
                        "trust_level": "confirmed",
                    })
                    for attribute in product_profile["attributes"]:
                        attribute_content = json.dumps(
                            {
                                "value": attribute.get("value"),
                                "unit": attribute.get("unit"),
                                "confirmation_status": attribute.get("confirmation_status"),
                            },
                            ensure_ascii=False,
                            default=str,
                        )
                        sources.append({
                            "type": "product_profile_attribute",
                            "source_id": (
                                f"profile:{profile_id}:attribute:{attribute['attribute_code']}"
                            ),
                            "label": (
                                f"{selected_product['sku']} · {attribute['attribute_code']}"
                            ),
                            "version": product_profile.get("profile_version"),
                            "locator": {
                                "product_id": selected_product["product_id"],
                                "profile_version_id": profile_id,
                                "attribute_code": attribute["attribute_code"],
                            },
                            "excerpt": attribute_content,
                            "estimated_tokens": _token_estimate(attribute_content),
                            "priority": 90,
                            "trust_level": "confirmed",
                        })
                    model_context["selected_product_profile"] = product_profile

        for item in selected_datasets:
            content = json.dumps(item, ensure_ascii=False, default=str)
            sources.append({
                "type": "authorized_dataset",
                "source_id": f"dataset:{item['dataset_id']}",
                "label": item["name"],
                "version": item.get("version_no"),
                "locator": {"dataset_id": item["dataset_id"]},
                "excerpt": content[:1000],
                "estimated_tokens": _token_estimate(content),
                "priority": 90,
                "trust_level": "confirmed",
            })
        model_context["datasets"] = selected_datasets

        memory_rows, _ = await self.memories.list(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            offset=0,
            limit=100,
            status="confirmed",
            workspace_uuid=workspace_uuid,
            active_only=True,
        )
        allowed_scopes = set(config.memory_scope)
        memory_rows = [item for item in memory_rows if item["scope"] in allowed_scopes]
        restricted_memories = [
            item for item in memory_rows if item.get("sensitivity") == "restricted"
        ]
        memory_rows = [
            item for item in memory_rows if item.get("sensitivity") != "restricted"
        ]
        for item in restricted_memories:
            truncated.append({
                "type": "customer_memory",
                "source_id": f"memory:{item['memory_uuid']}",
                "label": f"{item['memory_type']}（受限，未发送给模型）",
                "version": str(item["memory_uuid"]),
                "locator": {"memory_uuid": str(item["memory_uuid"])},
                "estimated_tokens": 0,
                "priority": 80,
                "trust_level": "restricted",
            })
        for item in memory_rows:
            content = json.dumps(item["value"], ensure_ascii=False, default=str)
            sources.append({
                "type": "customer_memory",
                "source_id": f"memory:{item['memory_uuid']}",
                "label": item["memory_type"],
                "version": str(item["memory_uuid"]),
                "locator": {
                    "memory_uuid": str(item["memory_uuid"]),
                    "source_message_uuid": (
                        str(item["source_message_uuid"])
                        if item.get("source_message_uuid") else None
                    ),
                },
                "excerpt": content[:1000],
                "estimated_tokens": _token_estimate(content),
                "priority": 80,
                "trust_level": "confirmed",
            })
        model_context["customer_memories"] = memory_context([
            {
                "memory_key": item["memory_type"],
                "memory_value": item["value"],
                "source": item["scope"],
            }
            for item in memory_rows
        ])
        conflicts = detect_context_conflicts(
            state=resolved_state,
            enterprise_profile=enterprise_profile,
            memories=memory_rows,
        )
        model_context["context_governance"] = {
            "source_priority": [
                "current_user_input",
                "confirmed_business_facts",
                "confirmed_customer_memory",
                "workspace_state_and_history",
                "task_and_report_evidence",
                "knowledge_documents",
                "model_inference",
            ],
            "conflicts": conflicts,
            "writeback_rule": (
                "当前问题只更新工作台状态；长期记忆必须产生候选并经确认，"
                "企业/产品画像只能通过对应业务接口修改。"
            ),
        }

        history_rows, _ = await WorkspaceRepository().list_messages(
            session,
            tenant_id=tenant_id,
            workspace_id=int(workspace["id"]),
            offset=0,
            limit=100,
        )
        history_limit = config.retrieval_policy.history_turn_limit * 2
        history_rows = [
            item for item in history_rows
            if item.get("role") in {"user", "assistant"}
        ][-history_limit:]
        history_messages = [
            {"role": item["role"], "content": item["content"][:2000]}
            for item in history_rows
        ]
        if history_rows:
            history_text = "\n".join(item["content"] for item in history_rows)
            sources.append({
                "type": "workspace_history",
                "source_id": f"workspace:{workspace_uuid}:messages",
                "label": f"工作台最近 {len(history_rows)} 条消息",
                "version": history_rows[-1]["seq_no"],
                "locator": {
                    "first_seq": history_rows[0]["seq_no"],
                    "last_seq": history_rows[-1]["seq_no"],
                },
                "estimated_tokens": _token_estimate(history_text),
                "priority": 70,
                "trust_level": "historical",
            })

        active_task_uuid = str(config.task_uuids[0]) if config.task_uuids else None
        task_result = None
        projections: dict[str, Any] = {}
        if active_task_uuid:
            try:
                task_result = await AnalysisTaskService(self.settings).result(
                    session,
                    tenant_id=tenant_id,
                    task_uuid=active_task_uuid,
                    opportunity_limit=10,
                    recommendation_limit=20,
                )
            except BusinessError as exc:
                if exc.code not in {"TASK_RESULT_NOT_READY", "TASK_RESULT_INCOMPLETE"}:
                    raise
            limits = {
                "clusters": 12,
                "opportunities": 10,
                "recommendations": 20,
                "evidence": 60,
                "competitors": 12,
            }
            insight_repo = InsightRepository()
            for name, limit in limits.items():
                rows = await insight_repo.task_projection(
                    session,
                    tenant_id=tenant_id,
                    task_uuid=active_task_uuid,
                    projection=name,
                ) or []
                projections[name] = rows[:limit]
            content = json.dumps(
                {"result": task_result, **projections},
                ensure_ascii=False,
                default=str,
            )
            sources.append({
                "type": "task_evidence",
                "source_id": f"task:{active_task_uuid}",
                "label": "分析任务结果与证据",
                "version": active_task_uuid,
                "locator": {"task_uuid": active_task_uuid},
                "excerpt": content[:1000],
                "estimated_tokens": _token_estimate(content),
                "priority": 60,
                "trust_level": "historical",
            })
            model_context["task"] = {"result": task_result, **projections}

        knowledge_matches: list[dict[str, Any]] = []
        knowledge_allowed = bool(await session.scalar(text("""
            SELECT EXISTS(
              SELECT FROM user_role_assignments a
              JOIN role_permissions p
                ON p.role_id=a.role_id AND p.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant_id AND a.user_id=:user_id
               AND (a.expires_at IS NULL OR a.expires_at>CURRENT_TIMESTAMP)
               AND p.permission_code='knowledge.read'
            )
        """), {"tenant_id": tenant_id, "user_id": user_id}))
        selected_knowledge_base_uuids = (
            [str(item) for item in config.knowledge_base_uuids]
            if knowledge_allowed else []
        )
        if config.knowledge_base_uuids and not knowledge_allowed:
            truncated.append({
                "type": "knowledge_base",
                "source_id": "knowledge:permission-denied",
                "label": "知识库（当前角色无读取权限）",
                "version": None,
                "locator": {},
                "estimated_tokens": 0,
                "priority": 50,
                "trust_level": "restricted",
            })
        if (
            not selected_knowledge_base_uuids
            and knowledge_allowed
            and config.retrieval_policy.knowledge_top_k
            and _KNOWLEDGE_INTENT_RE.search(question or "")
        ):
            available_bases, _ = await KnowledgeRepository().list_bases(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                offset=0,
                limit=self.settings.knowledge_auto_select_limit,
            )
            selected_knowledge_base_uuids = [
                str(item["knowledge_base_uuid"])
                for item in available_bases
                if int(item.get("ready_document_count") or 0) > 0
            ][:self.settings.knowledge_auto_select_limit]
            if selected_knowledge_base_uuids:
                auto_selection = json.dumps(
                    selected_knowledge_base_uuids,
                    ensure_ascii=False,
                )
                sources.append({
                    "type": "knowledge_auto_selection",
                    "source_id": f"workspace:{workspace_uuid}:knowledge-auto",
                    "label": f"自动选择 {len(selected_knowledge_base_uuids)} 个可访问知识库",
                    "version": None,
                    "locator": {"knowledge_base_uuids": selected_knowledge_base_uuids},
                    "excerpt": auto_selection,
                    "estimated_tokens": _token_estimate(auto_selection),
                    "priority": 50,
                    "trust_level": "historical",
                })
        if selected_knowledge_base_uuids and config.retrieval_policy.knowledge_top_k:
            try:
                knowledge_matches = await KnowledgeService(self.settings).search(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=int(workspace["id"]),
                    query=question or workspace["name"],
                    knowledge_base_uuids=selected_knowledge_base_uuids,
                    document_types=[],
                    top_k=config.retrieval_policy.knowledge_top_k,
                    persist_citations=False,
                )
            except RuntimeError:
                truncated.append({
                    "type": "knowledge_base",
                    "source_id": "knowledge:embedding-unavailable",
                    "label": "知识库检索暂不可用",
                    "version": None,
                    "locator": {},
                    "estimated_tokens": 0,
                })
            for match in knowledge_matches:
                sources.append({
                    "type": "knowledge_document",
                    "source_id": f"document:{match['document_uuid']}",
                    "label": match["document_name"],
                    "version": match["document_version"],
                    "locator": {
                        "document_uuid": match["document_uuid"],
                        "page": match.get("page"),
                    },
                    "excerpt": match["chunk_text"][:1000],
                    "score": match["score"],
                    "estimated_tokens": _token_estimate(match["chunk_text"]),
                    "priority": 50,
                    "trust_level": "untrusted",
                })
            model_context["knowledge_matches"] = knowledge_matches

        workbench_fallback = local_workbench_chat_answer(
            question,
            products=products,
            datasets=datasets,
            selected_product=selected_product,
            selected_dataset=selected_dataset,
        )
        if active_task_uuid:
            fallback = local_task_chat_answer(
                question,
                result=task_result,
                projections=projections,
            )
            progress_summary = build_task_thinking(
                question,
                result=task_result,
                projections=projections,
            )
        else:
            fallback = workbench_fallback
            progress_summary = build_workbench_thinking(
                question,
                products=products,
                datasets=datasets,
                selected_product=selected_product,
                selected_dataset=selected_dataset,
            )

        def render_messages() -> list[dict[str, str]]:
            base_context = enrich_catalog_context(
                catalog_context(products, datasets, selected_product, selected_dataset),
                enterprise_profile=enterprise_profile,
                product_profile=product_profile,
                memories=model_context["customer_memories"],
            )
            base_context.update(model_context)
            rendered = [{
                "role": "system",
                "content": workbench_system_prompt(base_context, streaming=streaming),
            }]
            rendered.extend(history_messages)
            rendered.append({"role": "user", "content": question})
            return rendered

        messages = render_messages()
        token_budget = config.retrieval_policy.max_context_tokens
        while sum(_token_estimate(item["content"]) for item in messages) > token_budget and history_messages:
            history_messages.pop(0)
            messages = render_messages()
        history_source = next((item for item in sources if item["type"] == "workspace_history"), None)
        if history_source and len(history_messages) != len(history_rows):
            truncated.append(_public_source(history_source))
            sources.remove(history_source)
            if history_messages:
                kept_rows = history_rows[-len(history_messages):]
                kept_text = "\n".join(item["content"] for item in kept_rows)
                history_source = {
                    **history_source,
                    "label": f"工作台最近 {len(kept_rows)} 条消息",
                    "version": kept_rows[-1]["seq_no"],
                    "locator": {
                        "first_seq": kept_rows[0]["seq_no"],
                        "last_seq": kept_rows[-1]["seq_no"],
                    },
                    "estimated_tokens": _token_estimate(kept_text),
                }
                sources.append(history_source)
        while (
            sum(_token_estimate(item["content"]) for item in messages) > token_budget
            and knowledge_matches
        ):
            removed = knowledge_matches.pop()
            source = next((
                item for item in reversed(sources)
                if item["type"] == "knowledge_document"
                and item["source_id"] == f"document:{removed['document_uuid']}"
                and item.get("locator", {}).get("page") == removed.get("page")
            ), None)
            if source:
                sources.remove(source)
                truncated.append(_public_source(source))
            model_context["knowledge_matches"] = knowledge_matches
            messages = render_messages()
        if (
            sum(_token_estimate(item["content"]) for item in messages) > token_budget
            and model_context.get("task")
        ):
            task_source = next((item for item in sources if item["type"] == "task_evidence"), None)
            if task_source:
                truncated.append(_public_source(task_source))
            task_text = json.dumps(model_context["task"], ensure_ascii=False, default=str)
            model_context["task"] = {
                "truncated": True,
                "excerpt": task_text[:max(1000, token_budget * 2)],
            }
            messages = render_messages()

        sources.sort(key=lambda item: int(item.get("priority") or 0), reverse=True)
        public_sources = [_public_source(item) for item in sources]
        citations = [
            _citation(item)
            for item in sources
            if item["type"] not in {
                "current_user_input",
                "conversation_state",
                "workspace_history",
                "product_profile",
            }
        ]
        estimated_tokens = sum(_token_estimate(item["content"]) for item in messages)
        snapshot_material = {
            "configuration": config.model_dump(mode="json"),
            "sources": public_sources,
            "resolved_state": resolved_state,
            "conflicts": conflicts,
            "resolution_log": resolution_log,
        }
        context_hash = hashlib.sha256(
            json.dumps(
                snapshot_material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return ConversationContext(
            messages=messages,
            fallback=fallback,
            progress_summary=progress_summary,
            configuration=config.model_dump(mode="json"),
            context_revision=revision,
            sources=public_sources,
            citations=citations,
            estimated_tokens=estimated_tokens,
            truncated_sources=truncated,
            context_hash=context_hash,
            workspace=workspace,
            confirmed_memories=memory_rows,
            resolved_state=resolved_state,
            state_revision=int(persisted_state.get("revision") or 0),
            conflicts=conflicts,
            resolution_log=resolution_log,
        )
