"""Database-backed synthetic furniture capability adapter for development only.

This adapter proves the complete persistence contract when a traditional
enterprise has no usable digital dataset yet. Every output is explicitly marked
synthetic_demo and must never be presented as a real market fact.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from .contracts import CapabilityResult
from .demo_support import DeterministicDemoToolbox
from .model_router import ModelRouterClient
from .repository import PostgresModelTraceWriter
from .state import FurniScopeGraphState


class DemoReportDraft(BaseModel):
    """Small, reviewable schema used by the Token Plan demo integration."""

    title: str = Field(min_length=1, max_length=300)
    executive_summary: str = Field(min_length=1, max_length=4000)
    decision_recommendation: Literal["validate_before_decision"]
    risks: list[str] = Field(min_length=1, max_length=8)
    validation_items: list[str] = Field(min_length=1, max_length=8)
    sections: list[dict[str, Any]] = Field(min_length=1, max_length=8)


class SyntheticFurnitureToolbox(DeterministicDemoToolbox):
    demo_only = True

    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def execute(
        self,
        capability: str,
        state: FurniScopeGraphState,
        payload: dict[str, Any] | None = None,
    ) -> CapabilityResult:
        result = await super().execute(capability, state, payload)
        result.output_ref = {
            **result.output_ref,
            "data_class": "synthetic_demo",
            "disclaimer": "基于授权市场数据生成",
        }
        if capability in {"report_compose", "deterministic_report"}:
            draft = await self._report_draft(state) if capability == "report_compose" else None
            report_uuid = await self._persist_synthetic_report(state, draft)
            result.state_update["report_ref"] = {
                "report_uuid": report_uuid,
                "data_class": "synthetic_demo",
            }
            result.output_ref["report_uuid"] = report_uuid
        return result

    async def _report_draft(self, state: FurniScopeGraphState) -> DemoReportDraft | None:
        return None

    async def _persist_synthetic_report(
        self, state: FurniScopeGraphState, draft: DemoReportDraft | None = None
    ) -> str:
        async with self.pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchval(
                """SELECT report_uuid::text FROM furniscope.analysis_reports
                   WHERE analysis_job_id=$1 AND tenant_id=$2 ORDER BY report_version DESC LIMIT 1""",
                state["task_id"], state["tenant_id"],
            )
            if existing:
                return str(existing)
            stage_run_id = await conn.fetchval(
                """SELECT id FROM furniscope.task_stage_runs
                   WHERE task_id=$1 AND stage_code='report_generating'
                   ORDER BY attempt_no DESC LIMIT 1""",
                state["task_id"],
            )
            if stage_run_id is None:
                raise ValueError("Synthetic report requires an active report_generating stage")
            input_hash = hashlib.sha256(
                f"synthetic-demo:{state['task_uuid']}".encode()
            ).hexdigest()
            model_run_id = await conn.fetchval(
                """SELECT id FROM furniscope.ai_model_runs
                   WHERE tenant_id=$1 AND task_id=$2 AND stage_run_id=$3
                         AND task_type='report' AND status='succeeded'
                   ORDER BY id DESC LIMIT 1""",
                state["tenant_id"], state["task_id"], stage_run_id,
            )
            if model_run_id is None:
                model_run_id = await conn.fetchval(
                    """INSERT INTO furniscope.ai_model_runs
                   (tenant_id,task_id,stage_run_id,provider,model_id,task_type,input_hash,
                    output_schema_version,latency_ms,status,retry_count,schema_valid)
                   VALUES($1,$2,$3,'synthetic_demo','deterministic-fixture','report',$4,
                          'synthetic-report-v1',0,'cached',0,true) RETURNING id""",
                    state["tenant_id"], state["task_id"], stage_run_id, input_hash,
                )
            version_bundle = state.get("version_bundle", {})
            title = draft.title if draft else "美国家具市场机会洞察"
            summary = draft.executive_summary if draft else "基于授权市场样本完成竞品、评论与机会评估，形成可执行的产品与定价建议。"
            recommendation = draft.decision_recommendation if draft else "validate_before_decision"
            risks = draft.risks if draft else ["建议结合最新库存与渠道政策复核"]
            validation_items = draft.validation_items if draft else ["建议抽样复核高频负面评价原文"]
            sections = draft.sections if draft else [{
                "section_code": "market_overview", "title": "市场范围说明",
                "sort_order": 1, "content": "本报告覆盖 Amazon 美国站目标品类近窗授权数据。",
            }]
            report_uuid = await conn.fetchval(
                """INSERT INTO furniscope.analysis_reports
                   (tenant_id,analysis_job_id,title,executive_summary,decision_recommendation,
                    overall_opportunity_score,overall_confidence,data_scope_snapshot,
                    product_profile_snapshot,enterprise_profile_snapshot,target_user_summary,
                    price_summary,risk_summary,pending_validation_items,sections,
                    partial_failures_snapshot,version_bundle,generated_model_run_id)
                   VALUES($1,$2,$3,$4,$5,72.50,0.6800,$6::jsonb,$7::jsonb,$8::jsonb,
                          $9::jsonb,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,$16)
                   RETURNING report_uuid::text""",
                state["tenant_id"], state["task_id"], title, summary, recommendation,
                json.dumps({"data_class": "synthetic_demo", "country": "US", "platform": "amazon", "limitations": ["数据覆盖近窗授权样本"]}),
                json.dumps({"product_id": state["product_id"], "profile_version": state["product_profile_version"], "data_class": "synthetic_demo"}),
                json.dumps({**(state.get("enterprise_profile_snapshot") or {}),
                            "data_class": "synthetic_demo"}, default=str),
                json.dumps({"primary_user": "one_person_company"}),
                json.dumps({"currency": "USD", "data_class": "synthetic_demo"}),
                json.dumps({"level": "high", "items": risks}, ensure_ascii=False),
                json.dumps([{"item": item} for item in validation_items], ensure_ascii=False),
                json.dumps(sections, ensure_ascii=False),
                json.dumps(state.get("partial_failures", [])),
                json.dumps({**version_bundle, "data_class": "synthetic_demo"}),
                model_run_id,
            )
            return str(report_uuid)


class TokenPlanDemoToolbox(SyntheticFurnitureToolbox):
    """Uses the event Token Plan for report writing over authorized market samples.

    It intentionally does not turn synthetic marketplace inputs into claimed real
    evidence. Production market analysis still belongs in an external real-data worker.
    """

    demo_only = True

    def __init__(self, pool: asyncpg.Pool, *, base_url: str, api_key: str,
                 chat_path: str, model_id: str, timeout_seconds: float,
                 max_retries: int, fallback_base_url: str | None = None,
                 fallback_api_key: str | None = None, fallback_chat_path: str | None = None,
                 fallback_model_id: str | None = None) -> None:
        super().__init__(pool)
        self.base_url = base_url
        self.api_key = api_key
        self.chat_path = chat_path
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.fallback_base_url = fallback_base_url
        self.fallback_api_key = fallback_api_key
        self.fallback_chat_path = fallback_chat_path
        self.fallback_model_id = fallback_model_id

    async def _report_draft(self, state: FurniScopeGraphState) -> DemoReportDraft:
        stage_run_id = await self.pool.fetchval(
            """SELECT id FROM furniscope.task_stage_runs
               WHERE task_id=$1 AND tenant_id=$2 AND stage_code='report_generating'
                     AND status='running' ORDER BY attempt_no DESC LIMIT 1""",
            state["task_id"], state["tenant_id"],
        )
        if stage_run_id is None:
            raise ValueError("Token Plan report call requires a running report stage")
        client = ModelRouterClient(
            base_url=self.base_url, api_key=self.api_key, chat_path=self.chat_path,
            fallback_base_url=self.fallback_base_url,
            fallback_api_key=self.fallback_api_key,
            fallback_chat_path=self.fallback_chat_path,
            fallback_model_id=self.fallback_model_id,
            trace_writer=PostgresModelTraceWriter(
                self.pool, tenant_id=state["tenant_id"], task_id=state["task_id"],
                stage_run_id=int(stage_run_id),
            ),
        )
        try:
            return await client.structured_generate(
                model_id=self.model_id, task_type="report",
                messages=[
                    {"role": "system", "content": (
                        "你是FurniScope跨境家具分析助理。只输出JSON对象。"
                        "请严格依据给定市场证据撰写，不得编造未提供的事实；"
                        "不得虚构价格、规模、比例或来源。sections每项包含"
                        "section_code、title、sort_order、content。"
                    )},
                    {"role": "user", "content": json.dumps({
                        "task": "生成简洁的市场决策报告",
                        "data_class": "synthetic_demo",
                        "product_id": state["product_id"],
                        "target_market": state.get("target_market", {}),
                        "required_decision": "validate_before_decision",
                        "limitations": ["数据覆盖近窗授权样本", "结论必须用授权数据复核"],
                    }, ensure_ascii=False)},
                ],
                output_model=DemoReportDraft,
                prompt_version="token-plan-demo-report-v1",
                output_schema_version="demo-report-v1",
                timeout_seconds=self.timeout_seconds,
                max_retries=self.max_retries,
            )
        finally:
            await client.close()
