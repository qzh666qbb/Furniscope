"""Tenant-scoped confirmation and analysis insight projections."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class InsightRepository:
    async def confirmations(self, session: AsyncSession, *, tenant_id: int, status: str,
                            offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        params = {"tenant_id": tenant_id, "status": status, "offset": offset, "limit": limit}
        total = int(await session.scalar(text("""
            SELECT count(*) FROM user_confirmations
            WHERE tenant_id=:tenant_id AND status=:status
        """), params) or 0)
        rows = await session.execute(text("""
            SELECT c.confirmation_id::text,c.confirmation_type,c.question,c.recommended_option,
                   c.options,c.evidence_refs,c.impact,c.checkpoint_stage,c.expires_at,c.created_at,
                   t.task_uuid::text,t.status task_status
              FROM user_confirmations c JOIN analysis_tasks t
                ON t.id=c.task_id AND t.tenant_id=c.tenant_id
             WHERE c.tenant_id=:tenant_id AND c.status=:status
             ORDER BY c.created_at DESC,c.id DESC OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in rows.mappings().all()], total

    async def _task_id(self, session: AsyncSession, tenant_id: int, task_uuid: str) -> int | None:
        return await session.scalar(text("""
            SELECT id FROM analysis_tasks
             WHERE tenant_id=:tenant_id AND task_uuid=CAST(:task_uuid AS uuid)
        """), {"tenant_id": tenant_id, "task_uuid": task_uuid})

    async def task_projection(self, session: AsyncSession, *, tenant_id: int,
                              task_uuid: str, projection: str) -> list[dict[str, Any]] | None:
        task_id = await self._task_id(session, tenant_id, task_uuid)
        if task_id is None:
            return None
        params = {"tenant_id": tenant_id, "task_id": task_id}
        queries = {
            "competitors": """
                SELECT c.id competitor_id,c.competitor_type,c.overall_score::float8,c.rerank_score::float8,
                       c.match_reasons,c.set_version,l.platform_listing_id,l.title,l.brand,l.sale_price::float8,
                       l.currency,l.rating::float8,l.review_count
                  FROM competitor_matches c JOIN market_listings l
                    ON l.id=c.listing_id AND l.tenant_id=c.tenant_id
                 WHERE c.tenant_id=:tenant_id AND c.analysis_job_id=:task_id
                 ORDER BY c.set_version DESC,c.overall_score DESC,c.id
            """,
            "review_aspects": """
                SELECT id aspect_id,review_id,aspect_index,taxonomy_code,sentiment,severity,
                       person_codes,scenario_codes,product_attribute_codes,evidence_start,evidence_end,
                       evidence_quote,extraction_confidence::float8,model_run_id,created_at
                  FROM review_aspects WHERE tenant_id=:tenant_id AND analysis_job_id=:task_id
                 ORDER BY review_id,aspect_index,id
            """,
            "clusters": """
                SELECT id cluster_id,cluster_code,taxonomy_code,name cluster_name,summary,
                       sentiment_distribution,aspect_count,review_count,listing_count,
                       mention_rate::float8,importance_score::float8,cluster_confidence::float8,
                       representative_aspect_ids,created_at
                  FROM insight_clusters WHERE tenant_id=:tenant_id AND analysis_job_id=:task_id
                 ORDER BY importance_score DESC,id
            """,
            "opportunities": """
                SELECT id opportunity_id,opportunity_code,title,description,target_country,target_platform,
                       target_user_codes,usage_scenario_codes,primary_cluster_ids,price_band_id,status,
                       demand_heat_score::float8,demand_growth_score::float8,unmet_need_score::float8,
                       competition_space_score::float8,profit_space_score::float8,
                       enterprise_fit_score::float8,enterprise_fit_confidence::float8,
                       base_score::float8,confidence::float8,
                       recommendation_level,weight_config,scoring_version,manufacturing_fit,
                       calculated_at,created_at,updated_at
                  FROM market_opportunities WHERE tenant_id=:tenant_id AND analysis_job_id=:task_id
                 ORDER BY base_score DESC,id
            """,
            "recommendations": """
                SELECT r.id recommendation_id,r.opportunity_id,r.recommendation_type,
                       r.problem_statement,r.root_cause_hypotheses,r.recommended_action,
                       r.target_attribute_code,r.target_value,r.expected_benefit,r.impact_dimensions,
                       r.cost_impact_min::float8,r.cost_impact_max::float8,r.cost_currency,
                       r.priority,r.confidence::float8,r.validation_method,r.evidence_cluster_ids,
                       r.risk_level,r.model_run_id,r.created_at,r.updated_at
                  FROM product_recommendations r JOIN market_opportunities o ON o.id=r.opportunity_id
                 WHERE r.tenant_id=:tenant_id AND o.analysis_job_id=:task_id
                 ORDER BY CASE r.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,r.id
            """,
            "evidence": """
                SELECT id evidence_link_id,claim_type,claim_id,claim_path,claim_category,
                       evidence_type,evidence_id,support_type,relevance_score::float8,is_primary,display_order
                  FROM evidence_links WHERE tenant_id=:tenant_id AND analysis_job_id=:task_id
                 ORDER BY is_primary DESC,display_order,id
            """,
        }
        if projection not in queries:
            raise ValueError("unsupported projection")
        rows = await session.execute(text(queries[projection]), params)
        return [dict(row) for row in rows.mappings().all()]
