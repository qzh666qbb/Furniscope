"""Tenant-scoped persistence for API-INS-01 through API-INS-04."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AnalysisTaskRepository:
    async def list(self, session: AsyncSession, *, tenant_id: int,
                   offset: int, limit: int, source: str | None = None) -> tuple[list[dict[str, Any]], int]:
        params = {"tenant_id": tenant_id, "offset": offset, "limit": limit, "source": source}
        where = (
            "t.tenant_id=:tenant_id AND t.status<>'cancelled' AND "
            "(CAST(:source AS text) IS NULL "
            "OR t.analysis_config->>'source'=CAST(:source AS text))"
        )
        total = int(await session.scalar(text(
            f"SELECT count(*) FROM analysis_tasks t WHERE {where}"
        ), params) or 0)
        result = await session.execute(text(f"""
            SELECT t.task_uuid::text,t.job_name,t.job_type,t.status,
                   t.external_stage AS stage,t.progress_percent,t.product_id,
                   p.sku AS product_sku,p.name AS product_name,
                   trim(t.target_country) AS target_country,t.target_platform,
                   t.analysis_config->>'source' AS source,
                   NULLIF(t.analysis_config->>'workspace_uuid','')::uuid AS workspace_uuid,
                   report.report_uuid::text,t.created_at,t.updated_at
              FROM analysis_tasks t
              JOIN products p ON p.id=t.product_id AND p.tenant_id=t.tenant_id
              LEFT JOIN LATERAL (
                SELECT r.report_uuid
                  FROM analysis_reports r
                 WHERE r.analysis_job_id=t.id AND r.tenant_id=t.tenant_id
                   AND r.status='draft'
                 ORDER BY r.report_version DESC,r.id DESC LIMIT 1
              ) report ON true
             WHERE {where}
             ORDER BY t.updated_at DESC,t.id DESC
             OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in result.mappings().all()], total

    async def archive_tasks(self, session: AsyncSession, *, tenant_id: int,
                            task_uuids: list[str]) -> int:
        params = {"tenant_id": tenant_id, "uuids": task_uuids}
        await session.execute(text("""
            UPDATE analysis_reports SET status='superseded',updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND status='draft'
               AND analysis_job_id IN (
                 SELECT id FROM analysis_tasks
                  WHERE tenant_id=:tenant_id AND CAST(task_uuid AS text)=ANY(:uuids)
               )
        """), params)
        result = await session.execute(text("""
            UPDATE analysis_tasks SET status='cancelled',updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND CAST(task_uuid AS text)=ANY(:uuids)
               AND status<>'cancelled'
        """), params)
        return int(result.rowcount or 0)

    async def input_scope(self, session: AsyncSession, *, tenant_id: int, product_id: int,
                          profile_version_id: int, dataset_id: int) -> dict[str, Any]:
        result = await session.execute(text("""
            SELECT p.id AS product_id,p.category_code,p.analysis_status,
                   pv.id AS profile_version_id,pv.status AS profile_status,
                   d.id AS dataset_id,d.status AS dataset_status,d.platform,
                   d.market_country,d.category_code AS dataset_category_code
              FROM products p
              LEFT JOIN product_profile_versions pv
                ON pv.id=:profile_version_id AND pv.product_id=p.id AND pv.tenant_id=p.tenant_id
              LEFT JOIN market_datasets d ON d.id=:dataset_id AND d.tenant_id=p.tenant_id
             WHERE p.id=:product_id AND p.tenant_id=:tenant_id AND p.deleted_at IS NULL
        """), {"tenant_id": tenant_id, "product_id": product_id,
                "profile_version_id": profile_version_id, "dataset_id": dataset_id})
        row = result.mappings().one_or_none()
        return dict(row) if row else {}

    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     idempotency_key: str, payload: dict[str, Any],
                     versions: dict[str, str]) -> dict[str, Any]:
        snapshot = await session.scalar(text("""
            SELECT jsonb_build_object(
              'profile_version',COALESCE((SELECT profile_version FROM enterprise_profiles
                                           WHERE tenant_id=:tenant_id),0),
              'captured_at',CURRENT_TIMESTAMP,
              'profile',(SELECT to_jsonb(ep)-'id'-'tenant_id'
                           FROM enterprise_profiles ep WHERE ep.tenant_id=:tenant_id),
              'capabilities',COALESCE((
                SELECT jsonb_agg(to_jsonb(mc)-'id'-'tenant_id'-'created_by'
                                 ORDER BY mc.capability_type,mc.capability_code)
                  FROM manufacturing_capabilities mc WHERE mc.tenant_id=:tenant_id
              ),'[]'::jsonb))
        """), {"tenant_id": tenant_id})
        snapshot = snapshot or {"profile_version": 0, "profile": None, "capabilities": []}
        params = {**payload, **versions, "tenant_id": tenant_id, "user_id": user_id,
                  "idempotency_key": idempotency_key,
                  "analysis_config_json": json.dumps(payload["analysis_config"], ensure_ascii=False),
                  "enterprise_profile_version": int(snapshot.get("profile_version") or 0),
                  "enterprise_profile_snapshot": json.dumps(snapshot, ensure_ascii=False, default=str)}
        result = await session.execute(text("""
            INSERT INTO analysis_tasks
              (tenant_id,job_name,job_type,product_id,product_profile_version_id,dataset_id,
               target_country,target_platform,analysis_currency,analysis_config,ontology_version,
               scoring_version,prompt_bundle_version,model_route_version,idempotency_key,created_by,
               enterprise_profile_version,enterprise_profile_snapshot,
               external_stage,internal_stage)
            VALUES
              (:tenant_id,:job_name,:job_type,:product_id,:product_profile_version_id,:dataset_id,
               :target_country,:target_platform,:analysis_currency,CAST(:analysis_config_json AS jsonb),
               :ontology_version,:scoring_version,:prompt_bundle_version,:model_route_version,
               :idempotency_key,:user_id,:enterprise_profile_version,
               CAST(:enterprise_profile_snapshot AS jsonb),'understanding_product','task_initializing')
            RETURNING id,task_uuid::text,job_name,job_type,status,
                      external_stage AS stage,progress_percent
        """), params)
        return dict(result.mappings().one())

    async def task_for_update(self, session: AsyncSession, *, tenant_id: int,
                              task_uuid: str) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT t.*,pv.status AS profile_status,d.status AS dataset_status,
                   d.platform AS dataset_platform,d.market_country AS dataset_country,
                   d.category_code AS dataset_category,p.category_code AS product_category
              FROM analysis_tasks t
              JOIN products p ON p.id=t.product_id AND p.tenant_id=t.tenant_id
              JOIN product_profile_versions pv
                ON pv.id=t.product_profile_version_id AND pv.product_id=t.product_id
              JOIN market_datasets d ON d.id=t.dataset_id AND d.tenant_id=t.tenant_id
             WHERE t.task_uuid=CAST(:task_uuid AS uuid) AND t.tenant_id=:tenant_id
             FOR UPDATE OF t
        """), {"tenant_id": tenant_id, "task_uuid": task_uuid})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def queue(self, session: AsyncSession, *, tenant_id: int,
                    task_id: int) -> dict[str, Any]:
        result = await session.execute(text("""
            UPDATE analysis_tasks
               SET status='queued',external_stage='understanding_product',
                   internal_stage='task_initializing',progress_percent=0,
                   failure_code=NULL,failure_message=NULL,updated_at=CURRENT_TIMESTAMP
             WHERE id=:task_id AND tenant_id=:tenant_id AND status='draft'
            RETURNING task_uuid::text,status,external_stage AS stage,progress_percent,checkpoint_stage
        """), {"tenant_id": tenant_id, "task_id": task_id})
        row = result.mappings().one_or_none()
        if row is None:
            raise RuntimeError("Draft task could not be queued")
        return dict(row)

    async def status(self, session: AsyncSession, *, tenant_id: int,
                     task_uuid: str) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT t.id,t.task_uuid::text,t.status,t.external_stage AS stage,
                   t.progress_percent,t.checkpoint_stage,t.job_name,t.product_id,
                   t.failure_message,COALESCE(t.analysis_config, '{}'::jsonb) AS analysis_config,r.report_uuid::text,
                   EXISTS(SELECT 1 FROM workflow_checkpoints w
                           WHERE w.task_id=t.id AND w.tenant_id=t.tenant_id
                             AND w.stage_code=t.checkpoint_stage AND w.is_safe_resume
                             AND w.status='active') AS safe_checkpoint,
                   EXISTS(SELECT 1 FROM workflow_partial_failures f
                           WHERE f.task_id=t.id AND f.tenant_id=t.tenant_id AND f.retryable
                             AND f.resolved_status IN ('open','retry_scheduled')) AS retryable_failure
              FROM analysis_tasks t
              LEFT JOIN analysis_reports r
                ON r.analysis_job_id=t.id AND r.tenant_id=t.tenant_id AND r.status='draft'
             WHERE t.task_uuid=CAST(:task_uuid AS uuid) AND t.tenant_id=:tenant_id
             ORDER BY r.report_version DESC NULLS LAST LIMIT 1
        """), {"tenant_id": tenant_id, "task_uuid": task_uuid})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def stage_runs(self, session: AsyncSession, *, tenant_id: int, task_id: int,
                         limit: int, admin: bool) -> list[dict[str, Any]]:
        result = await session.execute(text("""
            SELECT stage_code,attempt_no,status,started_at,ended_at,retryable,
                   CASE WHEN :admin THEN error_code ELSE NULL END AS error_code,
                   error_message
              FROM task_stage_runs
             WHERE tenant_id=:tenant_id AND task_id=:task_id
             ORDER BY created_at DESC,id DESC LIMIT :limit
        """), {"tenant_id": tenant_id, "task_id": task_id, "limit": limit, "admin": admin})
        rows = [dict(row) for row in result.mappings().all()]
        if not admin:
            for row in rows:
                row.pop("error_code", None)
        return rows

    async def partial_failures(self, session: AsyncSession, *, tenant_id: int,
                               task_id: int) -> list[dict[str, Any]]:
        result = await session.execute(text("""
            SELECT unit_type,failed_count,total_count,impact,retryable
              FROM workflow_partial_failures
             WHERE tenant_id=:tenant_id AND task_id=:task_id ORDER BY created_at,id
        """), {"tenant_id": tenant_id, "task_id": task_id})
        return [dict(row) for row in result.mappings().all()]

    async def pending_confirmation(self, session: AsyncSession, *, tenant_id: int,
                                   task_id: int) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT confirmation_id::text,confirmation_type,question,recommended_option,
                   options,evidence_refs,impact,checkpoint_stage,expires_at
              FROM user_confirmations
             WHERE tenant_id=:tenant_id AND task_id=:task_id AND status='pending'
             ORDER BY created_at DESC LIMIT 1
        """), {"tenant_id": tenant_id, "task_id": task_id})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def aggregate_result(self, session: AsyncSession, *, tenant_id: int, task_id: int,
                               opportunity_limit: int,
                               recommendation_limit: int) -> dict[str, Any] | None:
        report_result = await session.execute(text("""
            SELECT t.task_uuid::text,t.status,t.target_country,t.target_platform,
                   d.data_start_date,d.data_end_date,d.listing_count,d.valid_review_count,d.limitations,
                   r.report_uuid::text,r.title,r.executive_summary,r.decision_recommendation,
                   r.overall_opportunity_score,r.overall_confidence,r.data_scope_snapshot
              FROM analysis_tasks t
              JOIN market_datasets d ON d.id=t.dataset_id AND d.tenant_id=t.tenant_id
              LEFT JOIN analysis_reports r
                ON r.analysis_job_id=t.id AND r.tenant_id=t.tenant_id AND r.status='draft'
             WHERE t.id=:task_id AND t.tenant_id=:tenant_id
             ORDER BY r.report_version DESC NULLS LAST LIMIT 1
        """), {"tenant_id": tenant_id, "task_id": task_id})
        report = report_result.mappings().one_or_none()
        if report is None:
            return None
        data = dict(report)
        competitor = (await session.execute(text("""
            SELECT COALESCE(max(set_version),0) AS competitor_set_version,
                   count(*) FILTER(WHERE competitor_type='direct') AS direct,
                   count(*) FILTER(WHERE competitor_type='benchmark') AS benchmark,
                   count(*) FILTER(WHERE competitor_type='substitute') AS substitute,
                   count(*) FILTER(WHERE competitor_type='excluded') AS excluded,
                   COALESCE(sum(l.review_count) FILTER(WHERE c.competitor_type<>'excluded'),0) AS available_review_count
              FROM competitor_matches c JOIN market_listings l ON l.id=c.listing_id
             WHERE c.analysis_job_id=:task_id AND c.tenant_id=:tenant_id
        """), {"tenant_id": tenant_id, "task_id": task_id})).mappings().one()
        clusters = (await session.execute(text("""
            SELECT id AS cluster_id,name AS cluster_name,
                   COALESCE((SELECT key FROM jsonb_each_text(sentiment_distribution)
                             ORDER BY value::numeric DESC LIMIT 1),'mixed') AS sentiment,
                   importance_score,cluster_confidence
              FROM insight_clusters WHERE analysis_job_id=:task_id AND tenant_id=:tenant_id
             ORDER BY importance_score DESC,id LIMIT 20
        """), {"tenant_id": tenant_id, "task_id": task_id})).mappings().all()
        opportunities = (await session.execute(text("""
            SELECT id AS opportunity_id,opportunity_code,title,base_score,confidence,recommendation_level
              FROM market_opportunities WHERE analysis_job_id=:task_id AND tenant_id=:tenant_id
             ORDER BY base_score DESC,confidence DESC,id LIMIT :limit
        """), {"tenant_id": tenant_id, "task_id": task_id, "limit": opportunity_limit})).mappings().all()
        recommendations = (await session.execute(text("""
            SELECT pr.id AS recommendation_id,pr.opportunity_id,pr.recommendation_type,
                   pr.recommended_action,pr.priority,pr.confidence
              FROM product_recommendations pr JOIN market_opportunities o ON o.id=pr.opportunity_id
             WHERE o.analysis_job_id=:task_id AND pr.tenant_id=:tenant_id
             ORDER BY CASE pr.priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                      pr.confidence DESC,pr.id LIMIT :limit
        """), {"tenant_id": tenant_id, "task_id": task_id,
                "limit": recommendation_limit})).mappings().all()
        return data | {"competitor_summary": dict(competitor),
                       "insight_clusters": [dict(row) for row in clusters],
                       "opportunities": [dict(row) for row in opportunities],
                       "recommendations": [dict(row) for row in recommendations]}
