"""Database-backed synthetic furniture capability adapter for development only.

This adapter proves the complete persistence contract when a traditional
enterprise has no usable digital dataset yet. Every output is explicitly marked
synthetic_demo and must never be presented as a real market fact.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import asyncpg

from .contracts import CapabilityResult
from .demo_support import DeterministicDemoToolbox
from .state import FurniScopeGraphState


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
            "disclaimer": "演示合成数据，不代表真实市场结论",
        }
        if capability in {"report_compose", "deterministic_report"}:
            report_uuid = await self._persist_synthetic_report(state)
            result.state_update["report_ref"] = {
                "report_uuid": report_uuid,
                "data_class": "synthetic_demo",
            }
            result.output_ref["report_uuid"] = report_uuid
        return result

    async def _persist_synthetic_report(self, state: FurniScopeGraphState) -> str:
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
                """INSERT INTO furniscope.ai_model_runs
                   (tenant_id,task_id,stage_run_id,provider,model_id,task_type,input_hash,
                    output_schema_version,latency_ms,status,retry_count,schema_valid)
                   VALUES($1,$2,$3,'synthetic_demo','deterministic-fixture','report',$4,
                          'synthetic-report-v1',0,'cached',0,true) RETURNING id""",
                state["tenant_id"], state["task_id"], stage_run_id, input_hash,
            )
            version_bundle = state.get("version_bundle", {})
            report_uuid = await conn.fetchval(
                """INSERT INTO furniscope.analysis_reports
                   (tenant_id,analysis_job_id,title,executive_summary,decision_recommendation,
                    overall_opportunity_score,overall_confidence,data_scope_snapshot,
                    product_profile_snapshot,enterprise_profile_snapshot,target_user_summary,
                    price_summary,risk_summary,pending_validation_items,sections,
                    partial_failures_snapshot,version_bundle,generated_model_run_id)
                   VALUES($1,$2,'合成案例：美国家具市场洞察',
                          '本报告仅用于验证FurniScope端到端流程，全部市场数字为合成示例。',
                          'validate_before_decision',72.50,0.6800,$3::jsonb,$4::jsonb,$5::jsonb,
                          $6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb,$10::jsonb,$11::jsonb,$12::jsonb,$13)
                   RETURNING report_uuid::text""",
                state["tenant_id"], state["task_id"],
                json.dumps({"data_class": "synthetic_demo", "country": "US", "platform": "amazon", "limitations": ["非真实市场数据"]}),
                json.dumps({"product_id": state["product_id"], "profile_version": state["product_profile_version"], "data_class": "synthetic_demo"}),
                json.dumps({"data_class": "synthetic_demo"}),
                json.dumps({"primary_user": "one_person_company"}),
                json.dumps({"currency": "USD", "data_class": "synthetic_demo"}),
                json.dumps({"level": "high", "items": ["必须用真实授权数据复核"]}),
                json.dumps([{"item": "接入真实商品与评论数据后复算"}]),
                json.dumps([{"section_code": "synthetic_disclaimer", "title": "演示数据声明", "sort_order": 1, "content": "全部数据为合成示例"}]),
                json.dumps(state.get("partial_failures", [])),
                json.dumps({**version_bundle, "data_class": "synthetic_demo"}),
                model_run_id,
            )
            return str(report_uuid)

