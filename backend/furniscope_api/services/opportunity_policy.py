"""Tenant policy snapshots and append-only decision feedback."""

import json

from sqlalchemy import text

from furniscope_agent.enterprise_decision import POLICY_TEMPLATES, default_policy
from ..errors import BusinessError
from ..schemas.opportunity_policy import OpportunityFeedbackSave, OpportunityPolicySave


class OpportunityPolicyService:
    async def current(self, session, tenant_id: int) -> dict:
        row = (await session.execute(text("""
            SELECT version,config,created_at,created_by FROM enterprise_opportunity_policies
             WHERE tenant_id=:t ORDER BY version DESC LIMIT 1
        """), {"t": tenant_id})).mappings().one_or_none()
        return ({**row["config"], "version": row["version"],
                 "created_at": row["created_at"], "created_by": row["created_by"]} if row else default_policy())

    async def get(self, session, tenant_id: int) -> dict:
        return {"current": await self.current(session, tenant_id), "templates": POLICY_TEMPLATES,
                "calibration": "uncalibrated_heuristic"}

    async def save(self, session, tenant_id: int, user_id: int, body: OpportunityPolicySave) -> dict:
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 7_060_000 + tenant_id})
        current = await self.current(session, tenant_id)
        if body.expected_version != current["version"]:
            raise BusinessError("POLICY_VERSION_CONFLICT", "策略已被修改，请刷新后重试", status_code=409)
        config = body.model_dump(exclude={"expected_version"})
        config["calibration"] = "uncalibrated_heuristic"
        await session.execute(text("""
            INSERT INTO enterprise_opportunity_policies(tenant_id,version,config,created_by)
            VALUES(:t,:version,CAST(:config AS jsonb),:u)
        """), {"t": tenant_id, "version": current["version"] + 1, "config": json.dumps(config), "u": user_id})
        return await self.current(session, tenant_id)

    async def feedback(self, session, tenant_id: int, opportunity_id: int) -> dict:
        exists = await session.scalar(text("""
            SELECT id FROM market_opportunities WHERE id=:o AND tenant_id=:t
        """), {"o": opportunity_id, "t": tenant_id})
        if exists is None:
            raise BusinessError("OPPORTUNITY_NOT_FOUND", "机会不存在或不可访问", status_code=404)
        rows = (await session.execute(text("""
            SELECT id,revision,status,reason,score_snapshot,created_by,created_at
              FROM opportunity_feedback_events WHERE tenant_id=:t AND opportunity_id=:o
             ORDER BY revision DESC
        """), {"t": tenant_id, "o": opportunity_id})).mappings().all()
        return {"current": dict(rows[0]) if rows else None, "history": [dict(r) for r in rows]}

    async def save_feedback(self, session, tenant_id: int, user_id: int,
                            opportunity_id: int, body: OpportunityFeedbackSave) -> dict:
        # Lock opportunity so multiple users cannot create the same revision.
        opportunity = (await session.execute(text("""
            SELECT o.*,t.task_uuid::text,t.enterprise_profile_snapshot
              FROM market_opportunities o JOIN analysis_tasks t
                ON t.id=o.analysis_job_id AND t.tenant_id=o.tenant_id
             WHERE o.id=:o AND o.tenant_id=:t FOR UPDATE OF o
        """), {"t": tenant_id, "o": opportunity_id})).mappings().one_or_none()
        if opportunity is None:
            raise BusinessError("OPPORTUNITY_NOT_FOUND", "机会不存在或不可访问", status_code=404)
        current = (await self.feedback(session, tenant_id, opportunity_id))["current"]
        revision = current["revision"] if current else 0
        if revision != body.expected_revision:
            raise BusinessError("FEEDBACK_VERSION_CONFLICT", "处理结果已更新，请刷新后重试", status_code=409)
        keys = ("task_uuid", "opportunity_code", "market_score", "adjusted_score", "base_score",
                "confidence", "recommendation_level", "weight_config", "scoring_version",
                "manufacturing_fit", "policy_snapshot", "enterprise_profile_snapshot",
                "demand_heat_score", "demand_growth_score", "unmet_need_score",
                "competition_space_score", "profit_space_score",
                "enterprise_fit_score", "enterprise_fit_confidence")
        frozen = opportunity["decision_snapshot"]
        snapshot = ({**frozen["opportunity"], **frozen["task"],
                     "feature_capture": frozen} if frozen else
                    {**{key: opportunity[key] for key in keys},
                     "feature_status": "legacy_not_reconstructable"})
        await session.execute(text("""
            INSERT INTO opportunity_feedback_events
              (tenant_id,opportunity_id,analysis_job_id,revision,status,reason,score_snapshot,created_by)
            VALUES(:t,:o,:task,:r,:status,:reason,CAST(:snapshot AS jsonb),:u)
        """), {"t": tenant_id, "o": opportunity_id, "task": opportunity["analysis_job_id"],
                 "r": revision + 1, "status": body.status, "reason": body.reason,
                 "snapshot": json.dumps(snapshot, default=str), "u": user_id})
        return await self.feedback(session, tenant_id, opportunity_id)

    async def export(self, session, tenant_id: int, after_id: int = 0, limit: int = 500) -> dict:
        rows = (await session.execute(text("""
            SELECT id,opportunity_id,revision,status,reason,score_snapshot,created_by,created_at
              FROM opportunity_feedback_events WHERE tenant_id=:t AND id>:after
             ORDER BY id LIMIT :limit
        """), {"t": tenant_id, "after": after_id, "limit": limit + 1})).mappings().all()
        page = rows[:limit]
        return {"format_version": "opportunity-feedback-v1", "label_semantics": "user_decision_not_business_outcome",
                "items": [dict(r) for r in page],
                "next_after_id": page[-1]["id"] if len(rows) > limit else None}
