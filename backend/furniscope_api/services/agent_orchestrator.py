"""Server-side intent routing and deterministic tool execution for one turn."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..monitoring import DATA_QUERY_TOTAL
from .conversation_context import ConversationContext
from .data_query import (
    DataQueryService,
    build_data_query_plan,
    data_query_source_kind,
    is_data_query_request,
    render_data_query_answer,
)

AgentIntent = Literal["data_query", "rag", "workflow", "forecast", "conversation"]

_RAG_RE = re.compile(
    r"知识库|文档|资料|说明书|手册|制度|规范|认证|合同|附件|根据.+(?:文件|材料)",
    re.I,
)
_FORECAST_RE = re.compile(
    r"销量预测|未来销量|预测.*销量|销量.*预测|forecast",
    re.I,
)
_WORKFLOW_RE = re.compile(
    r"开始分析|运行(?:完整)?(?:工作流|分析)|运行至|继续(?:评估|分析|运行)|"
    r"重新运行|重试分析|生成(?:决策)?报告|评估.+(?:市场|机会)|分析.+(?:市场|竞品|评论|痛点)",
    re.I,
)


@dataclass(slots=True)
class AgentToolDecision:
    intent: AgentIntent
    answer: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    progress_stage: str = "model_generation"
    progress_message: str = "正在基于可审计上下文生成回答"

    @property
    def model_required(self) -> bool:
        return self.answer is None


def classify_agent_intent(question: str) -> AgentIntent:
    if is_data_query_request(question):
        return "data_query"
    if _FORECAST_RE.search(question or ""):
        return "forecast"
    if _RAG_RE.search(question or ""):
        return "rag"
    if _WORKFLOW_RE.search(question or ""):
        return "workflow"
    return "conversation"


def _workflow_target(question: str) -> str:
    if re.search(r"完整|全链路|报告|全部", question):
        return "report"
    if re.search(r"方案|改款|建议|定位|定价", question):
        return "plan"
    if re.search(r"评分|得分|机会|评估", question):
        return "score"
    if re.search(r"竞品|评论|痛点|市场|价格带|趋势", question):
        return "market"
    if re.search(r"产品|画像|参数|冲突", question):
        return "product"
    return "report"


class AgentToolOrchestrator:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    async def route(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_id: int,
        turn_uuid: str,
        question: str,
        context: ConversationContext,
    ) -> AgentToolDecision:
        intent = classify_agent_intent(question)
        if intent == "data_query":
            product_sku = None
            product_id = context.resolved_state.get("current_product_id")
            if product_id:
                product_sku = await session.scalar(text("""
                    SELECT sku FROM products
                     WHERE tenant_id=:tenant AND id=:product_id AND deleted_at IS NULL
                """), {"tenant": tenant_id, "product_id": product_id})
            plan = build_data_query_plan(
                question,
                current_sku=str(product_sku) if product_sku else None,
                current_market=context.resolved_state.get("current_market"),
            )
            if plan is None:
                return AgentToolDecision(intent="conversation")
            try:
                result = await DataQueryService(self.settings).execute(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    plan=plan,
                    workspace_id=workspace_id,
                    turn_uuid=turn_uuid,
                )
            except BusinessError as exc:
                if not exc.code.startswith("DATA_"):
                    raise
                DATA_QUERY_TOTAL.labels(
                    "rejected",
                    data_query_source_kind(plan),
                ).inc()
                return AgentToolDecision(
                    intent=intent,
                    answer=exc.message,
                    actions=[{
                        "action": "open_data_import",
                        "label": "管理经营数据",
                        "href": "forecast",
                    }],
                    tool_results=[{
                        "tool": "data_query",
                        "status": "rejected",
                        "data": {
                            "error_code": exc.code,
                            "plan": plan.model_dump(mode="json"),
                        },
                    }],
                    progress_stage="data_query_rejected",
                    progress_message=f"受控问数未执行：{exc.message}",
                )
            answer = render_data_query_answer(result)
            source = {
                "type": "data_query",
                "source_id": f"query:{result['query_uuid']}",
                "label": "企业经营数据查询",
                "version": result["source_version"]["canonical_sha256"],
                "locator": {
                    "query_uuid": result["query_uuid"],
                    "data_version_uuid": result["source_version"]["version_uuid"],
                    "result_sha256": result["result_sha256"],
                    "resolved_filters": result["resolved_filters"],
                },
                "estimated_tokens": 0,
                "priority": 95,
                "trust_level": "confirmed",
            }
            citation = {
                "citation_uuid": str(uuid4()),
                "source_type": "data_query",
                "source_id": f"query:{result['query_uuid']}",
                "source_version": result["source_version"]["canonical_sha256"],
                "label": (
                    f"{result['source_version']['filename']} · "
                    f"{str(result['source_version']['version_uuid'])[:8]}"
                ),
                "locator": source["locator"],
                "excerpt": answer[:1000],
                "score": 1.0,
            }
            return AgentToolDecision(
                intent=intent,
                answer=answer,
                actions=[{
                    "action": "open_data_query",
                    "label": "查看完整问数结果",
                    "query_uuid": result["query_uuid"],
                }],
                tool_results=[{
                    "tool": "data_query",
                    "status": "succeeded",
                    "data": result,
                }],
                sources=[source],
                citations=[citation],
                progress_stage="data_query_completed",
                progress_message=(
                    f"已按数据版本执行 {len(plan.metrics)} 个受控指标，"
                    f"返回 {result['row_count']} 行"
                ),
            )

        if intent == "forecast":
            return AgentToolDecision(
                intent=intent,
                answer=(
                    "这个问题需要销量预测模型，不应把历史销量聚合或市场机会分当作预测值。"
                    "请进入销量预测并选择已训练的 SKU。"
                ),
                actions=[{
                    "action": "open_forecast",
                    "label": "去销量预测",
                    "href": "forecast",
                }],
                progress_stage="tool_routed",
                progress_message="已识别为预测请求，未执行历史问数",
            )

        if intent == "workflow":
            product_id = context.resolved_state.get("current_product_id")
            if not product_id:
                return AgentToolDecision(
                    intent=intent,
                    answer="已识别为市场分析请求，但还没有确定产品。请先选择产品或明确输入产品名 / SKU。",
                    actions=[{
                        "action": "select_product",
                        "label": "选择产品",
                    }],
                    progress_stage="workflow_needs_input",
                    progress_message="工作流缺少产品绑定",
                )
            target = _workflow_target(question)
            return AgentToolDecision(
                intent=intent,
                answer=f"已识别为分析工作流请求，将使用当前产品运行至“{target}”节点。",
                actions=[{
                    "action": "run_workflow",
                    "label": "运行分析",
                    "target_node": target,
                    "product_id": product_id,
                    "dataset_id": context.resolved_state.get("current_dataset_id"),
                }],
                progress_stage="workflow_routed",
                progress_message=f"已路由到分析工作流工具，目标节点 {target}",
            )

        if intent == "rag":
            knowledge_sources = [
                item for item in context.sources
                if item.get("type") == "knowledge_document"
            ]
            if not knowledge_sources:
                return AgentToolDecision(
                    intent=intent,
                    answer=(
                        "当前可访问知识库没有达到相关性阈值的内容，我不能把模型常识伪装成企业文档结论。"
                        "请上传或绑定对应资料后再问。"
                    ),
                    actions=[{
                        "action": "manage_knowledge",
                        "label": "管理知识库",
                    }],
                    progress_stage="rag_no_evidence",
                    progress_message="知识检索无高置信匹配，已按策略拒答",
                )
            return AgentToolDecision(
                intent=intent,
                progress_stage="rag_grounded",
                progress_message=f"已检索到 {len(knowledge_sources)} 条高相关文档证据",
            )

        return AgentToolDecision(intent="conversation")
