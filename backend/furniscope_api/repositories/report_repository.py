"""Tenant-scoped dashboard, report and evidence queries."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ReportRepository:
    async def dashboard(self, session: AsyncSession, *, tenant_id: int) -> dict[str, Any]:
        result = await session.execute(text("""
            WITH latest_report AS (
              SELECT r.analysis_job_id task_id,r.report_uuid,r.title report_title,r.created_at analyzed_at,
                     COALESCE(r.data_scope_snapshot->>'data_class','unknown') data_class,
                     r.data_scope_snapshot->>'source' data_source,
                     t.task_uuid,p.sku product_sku,p.name product_name
                FROM analysis_reports r
                JOIN analysis_tasks t ON t.id=r.analysis_job_id AND t.tenant_id=r.tenant_id
                JOIN products p ON p.id=t.product_id AND p.tenant_id=t.tenant_id
               WHERE r.tenant_id=:tenant_id AND r.status='draft'
               ORDER BY r.created_at DESC,r.id DESC
               LIMIT 1
            )
            SELECT
              (SELECT count(*) FROM products WHERE tenant_id=:tenant_id AND deleted_at IS NULL) products,
              (SELECT count(*) FROM analysis_tasks WHERE tenant_id=:tenant_id
                 AND status IN ('queued','running')) running_tasks,
              (SELECT count(*) FROM user_confirmations WHERE tenant_id=:tenant_id
                 AND status='pending' AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP)) pending_confirmations,
              (SELECT count(*) FROM analysis_tasks WHERE tenant_id=:tenant_id
                 AND status IN ('waiting_human','failed')) failed_tasks,
              (SELECT count(*) FROM products p WHERE p.tenant_id=:tenant_id AND p.deleted_at IS NULL
                 AND EXISTS (
                   SELECT 1 FROM product_attributes pa
                    WHERE pa.profile_version_id=p.current_profile_version_id
                      AND pa.confirmation_status='conflicted'
                 )) conflicted_products,
              (SELECT count(*) FROM analysis_reports WHERE tenant_id=:tenant_id AND status='draft') reports,
              (SELECT count(*) FROM forecast_jobs WHERE tenant_id=:tenant_id
                 AND status IN ('draft','queued','running','succeeded')) forecast_jobs,
              (SELECT jsonb_build_object(
                'task_uuid',lr.task_uuid,'report_uuid',lr.report_uuid,
                'report_title',lr.report_title,'product_sku',lr.product_sku,
                'product_name',lr.product_name,'data_class',lr.data_class,
                'data_source',lr.data_source,'analyzed_at',lr.analyzed_at,
                'pain_point_count',(SELECT count(*) FROM review_aspects a
                  WHERE a.tenant_id=:tenant_id AND a.analysis_job_id=lr.task_id
                    AND a.sentiment='negative'),
                'high_frequency_issue_count',(SELECT count(*) FROM (
                  SELECT a.taxonomy_code
                    FROM review_aspects a
                   WHERE a.tenant_id=:tenant_id AND a.analysis_job_id=lr.task_id
                     AND a.sentiment='negative'
                   GROUP BY a.taxonomy_code
                  HAVING count(*) >= GREATEST(1,(SELECT count(*) * 0.05
                    FROM review_aspects total
                   WHERE total.tenant_id=:tenant_id AND total.analysis_job_id=lr.task_id
                     AND total.sentiment='negative'))
                ) frequent),
                'competitor_count',(SELECT count(*) FROM competitor_matches c
                  WHERE c.tenant_id=:tenant_id AND c.analysis_job_id=lr.task_id
                    AND c.competitor_type<>'excluded'),
                'price_band_low',(SELECT percentile_cont(0.25) WITHIN GROUP (ORDER BY l.sale_price)
                  FROM competitor_matches c JOIN market_listings l
                    ON l.id=c.listing_id AND l.tenant_id=c.tenant_id
                 WHERE c.tenant_id=:tenant_id AND c.analysis_job_id=lr.task_id
                   AND c.competitor_type<>'excluded' AND l.sale_price IS NOT NULL),
                'price_band_high',(SELECT percentile_cont(0.75) WITHIN GROUP (ORDER BY l.sale_price)
                  FROM competitor_matches c JOIN market_listings l
                    ON l.id=c.listing_id AND l.tenant_id=c.tenant_id
                 WHERE c.tenant_id=:tenant_id AND c.analysis_job_id=lr.task_id
                   AND c.competitor_type<>'excluded' AND l.sale_price IS NOT NULL),
                'price_currency',(SELECT CASE WHEN count(DISTINCT l.currency)=1 THEN min(l.currency) END
                  FROM competitor_matches c JOIN market_listings l
                    ON l.id=c.listing_id AND l.tenant_id=c.tenant_id
                 WHERE c.tenant_id=:tenant_id AND c.analysis_job_id=lr.task_id
                   AND c.competitor_type<>'excluded' AND l.sale_price IS NOT NULL),
                'recommendation_count',(SELECT count(*) FROM product_recommendations pr
                  JOIN market_opportunities o ON o.id=pr.opportunity_id AND o.tenant_id=pr.tenant_id
                 WHERE pr.tenant_id=:tenant_id AND o.analysis_job_id=lr.task_id),
                'high_priority_recommendation_count',(SELECT count(*) FROM product_recommendations pr
                  JOIN market_opportunities o ON o.id=pr.opportunity_id AND o.tenant_id=pr.tenant_id
                 WHERE pr.tenant_id=:tenant_id AND o.analysis_job_id=lr.task_id AND pr.priority='high'),
                'opportunity_count',(SELECT count(*) FROM market_opportunities o
                  WHERE o.tenant_id=:tenant_id AND o.analysis_job_id=lr.task_id),
                'priority_opportunity_count',(SELECT count(*) FROM market_opportunities o
                  WHERE o.tenant_id=:tenant_id AND o.analysis_job_id=lr.task_id
                    AND o.recommendation_level='prioritize_validate')
              ) FROM latest_report lr) insight_snapshot
        """), {"tenant_id": tenant_id})
        row = dict(result.mappings().one())
        return {
            **{key: int(row[key] or 0) for key in (
                "products", "running_tasks", "pending_confirmations", "failed_tasks",
                "conflicted_products", "reports", "forecast_jobs",
            )},
            "insight_snapshot": row["insight_snapshot"],
        }

    async def list_reports(self, session: AsyncSession, *, tenant_id: int,
                           offset: int, limit: int, keyword: str | None = None,
                           product_id: int | None = None, target_country: str | None = None,
                           created_since: str | None = None) -> tuple[list[dict[str, Any]], int]:
        filters = ["r.tenant_id=:tenant_id", "r.status='draft'"]
        params: dict[str, Any] = {"tenant_id": tenant_id, "offset": offset, "limit": limit}
        if product_id:
            filters.append("t.product_id=:product_id")
            params["product_id"] = product_id
        if target_country:
            filters.append("trim(t.target_country)=:target_country")
            params["target_country"] = target_country
        if created_since:
            filters.append("r.created_at>=CAST(:created_since AS timestamptz)")
            params["created_since"] = created_since
        if keyword:
            filters.append("""(
                r.title ILIKE :keyword OR p.sku ILIKE :keyword OR p.name ILIKE :keyword
                OR COALESCE(t.job_name,'') ILIKE :keyword
            )""")
            params["keyword"] = f"%{keyword}%"
        where = " AND ".join(filters)
        total = int(await session.scalar(text(f"""
            SELECT count(*) FROM analysis_reports r
              JOIN analysis_tasks t ON t.id=r.analysis_job_id AND t.tenant_id=r.tenant_id
              JOIN products p ON p.id=t.product_id AND p.tenant_id=t.tenant_id
             WHERE {where}
        """), params) or 0)
        rows = await session.execute(text(f"""
            SELECT r.report_uuid::text,r.title,r.overall_opportunity_score,r.overall_confidence,
                   r.decision_recommendation,
                   COALESCE(r.data_scope_snapshot->>'data_class','authorized_market_data') data_class,
                   r.created_at,t.task_uuid::text,t.product_id,t.job_name,
                   trim(t.target_country) target_country,t.target_platform,
                   p.sku product_sku,p.name product_name
              FROM analysis_reports r
              JOIN analysis_tasks t ON t.id=r.analysis_job_id AND t.tenant_id=r.tenant_id
              JOIN products p ON p.id=t.product_id AND p.tenant_id=t.tenant_id
             WHERE {where}
             ORDER BY r.created_at DESC,r.id DESC OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in rows.mappings().all()], total

    async def archive_reports(self, session: AsyncSession, *, tenant_id: int,
                              report_uuids: list[str]) -> int:
        params = {"tenant_id": tenant_id, "uuids": report_uuids}
        result = await session.execute(text("""
            UPDATE analysis_reports SET status='superseded',updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND status='draft'
               AND CAST(report_uuid AS text) = ANY(:uuids)
        """), params)
        return int(result.rowcount or 0)

    async def detail(self, session: AsyncSession, *, tenant_id: int,
                     report_uuid: str) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT r.report_uuid::text,r.title,r.executive_summary,r.overall_opportunity_score,
                   r.overall_confidence,r.decision_recommendation,r.data_scope_snapshot,
                   r.product_profile_snapshot,r.enterprise_profile_snapshot,r.target_user_summary,
                   r.price_summary,r.risk_summary,r.pending_validation_items,r.sections,
                   r.partial_failures_snapshot,r.version_bundle,r.created_at,
                   COALESCE(r.data_scope_snapshot->>'data_class','authorized_market_data') data_class,
                   t.task_uuid::text,t.product_id,t.job_name,trim(t.target_country) target_country,
                   t.target_platform,p.sku product_sku,p.name product_name,
                   jsonb_build_object('provider',m.provider,'model_id',m.model_id,
                     'prompt_version',m.prompt_version,'input_tokens',m.input_tokens,
                     'output_tokens',m.output_tokens,'latency_ms',m.latency_ms,
                     'status',m.status,'schema_valid',m.schema_valid) model_run
              FROM analysis_reports r
              JOIN analysis_tasks t ON t.id=r.analysis_job_id AND t.tenant_id=r.tenant_id
              JOIN products p ON p.id=t.product_id AND p.tenant_id=t.tenant_id
              JOIN ai_model_runs m ON m.id=r.generated_model_run_id AND m.tenant_id=r.tenant_id
             WHERE r.tenant_id=:tenant_id AND r.report_uuid=CAST(:report_uuid AS uuid)
                   AND r.status='draft'
        """), {"tenant_id": tenant_id, "report_uuid": report_uuid})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def evidence(self, session: AsyncSession, *, tenant_id: int,
                       report_uuid: str) -> list[dict[str, Any]] | None:
        task_id = await session.scalar(text("""
            SELECT analysis_job_id FROM analysis_reports
             WHERE tenant_id=:tenant_id AND report_uuid=CAST(:report_uuid AS uuid)
                   AND status='draft'
        """), {"tenant_id": tenant_id, "report_uuid": report_uuid})
        if task_id is None:
            return None
        result = await session.execute(text("""
            SELECT e.claim_type,e.claim_id,e.claim_path,e.claim_category,e.evidence_type,e.evidence_id,
                   e.support_type,e.relevance_score::float8,e.is_primary,e.display_order,
                   ra.evidence_quote,ra.sentiment,ra.severity,ra.taxonomy_code,
                   l.title listing_title,l.brand listing_brand,l.sale_price::float8 listing_price
              FROM evidence_links e
              LEFT JOIN review_aspects ra
                ON ra.id=e.evidence_id AND e.evidence_type='review_aspect' AND ra.tenant_id=e.tenant_id
              LEFT JOIN reviews rv
                ON rv.id=ra.review_id AND rv.tenant_id=ra.tenant_id
              LEFT JOIN market_listings l
                ON l.id=rv.listing_id AND l.tenant_id=rv.tenant_id
             WHERE e.tenant_id=:tenant_id AND e.analysis_job_id=:task_id
             ORDER BY e.is_primary DESC,e.display_order,e.id
        """), {"tenant_id": tenant_id, "task_id": task_id})
        return [dict(row) for row in result.mappings().all()]
