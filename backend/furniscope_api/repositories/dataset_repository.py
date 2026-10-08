"""Tenant-scoped market dataset read persistence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import json


class DatasetRepository:
    _LIST_COLUMNS = """d.id AS dataset_id,d.name,d.platform,d.market_country,d.category_code,d.status,
        (SELECT count(*) FROM market_listings l WHERE l.dataset_id=d.id AND l.tenant_id=d.tenant_id) AS listing_count,
        (SELECT count(*) FROM reviews r WHERE r.dataset_id=d.id AND r.tenant_id=d.tenant_id) AS review_count,
        (SELECT count(*) FROM reviews r WHERE r.dataset_id=d.id AND r.tenant_id=d.tenant_id AND r.is_valid) AS valid_review_count,
        d.quality_score,d.data_start_date,d.data_end_date,d.created_at,d.updated_at,d.version_no,
        d.quality_report,d.source_type,d.source_name"""

    async def list(self, session: AsyncSession, *, tenant_id: int, offset: int, limit: int,
                   platform: str | None, market_country: str | None, category_code: str | None,
                   status: str | None) -> tuple[list[dict[str, Any]], int]:
        filters = ["d.tenant_id=:tenant_id", "d.deleted_at IS NULL"]
        params: dict[str, Any] = {"tenant_id": tenant_id, "offset": offset, "limit": limit}
        for column, value in (("platform", platform), ("market_country", market_country),
                              ("category_code", category_code), ("status", status)):
            if value:
                filters.append(f"d.{column}=:{column}")
                params[column] = value
        where = " AND ".join(filters)
        count = await session.scalar(text(f"SELECT count(*) FROM market_datasets d WHERE {where}"), params)
        result = await session.execute(text(f"""
            SELECT {self._LIST_COLUMNS} FROM market_datasets d WHERE {where}
             ORDER BY d.updated_at DESC,d.id DESC OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in result.mappings().all()], int(count or 0)

    async def get(self, session: AsyncSession, *, tenant_id: int, dataset_id: int) -> dict[str, Any] | None:
        result = await session.execute(text(f"""
            SELECT {self._LIST_COLUMNS},d.marketplace_code,
                   d.authorization_reference,d.field_mapping,d.limitations
              FROM market_datasets d
             WHERE d.id=:dataset_id AND d.tenant_id=:tenant_id AND d.deleted_at IS NULL
        """), {"dataset_id": dataset_id, "tenant_id": tenant_id})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     payload: dict[str, Any]) -> dict[str, Any]:
        version_no = int(await session.scalar(text("""
            SELECT COALESCE(max(version_no),0)+1 FROM market_datasets
             WHERE tenant_id=:tenant_id AND name=:name
        """), {"tenant_id": tenant_id, "name": payload["name"]}) or 1)
        result = await session.execute(text("""
            INSERT INTO market_datasets
              (tenant_id,name,version_no,platform,market_country,category_code,data_start_date,
               data_end_date,source_type,source_name,authorization_reference,field_mapping,created_by)
            VALUES (:tenant_id,:name,:version_no,:platform,:market_country,:category_code,
                    :data_start_date,:data_end_date,:source_type,:source_name,:authorization_reference,
                    CAST(:field_mapping AS jsonb),:user_id)
            RETURNING id AS dataset_id,name,status,platform,market_country,category_code,version_no
        """), payload | {"tenant_id": tenant_id, "user_id": user_id, "version_no": version_no,
                          "field_mapping": json.dumps(payload["field_mapping"], ensure_ascii=False)})
        return dict(result.mappings().one())

    async def list_listings(self, session: AsyncSession, *, tenant_id: int, dataset_id: int,
                            offset: int, limit: int, query: str | None,
                            market_country: str | None = None) -> tuple[list[dict[str, Any]], int]:
        filters = ["l.tenant_id=:tenant", "l.dataset_id=:dataset"]
        params: dict[str, Any] = {"tenant": tenant_id, "dataset": dataset_id, "offset": offset, "limit": limit}
        if query:
            filters.append("(l.title ILIKE :query OR l.platform_listing_id ILIKE :query OR COALESCE(l.brand,'') ILIKE :query)")
            params["query"] = f"%{query}%"
        if market_country:
            filters.append("COALESCE(l.normalized_attributes->>'market_country', d.market_country)=:market")
            params["market"] = market_country
        where = " AND ".join(filters)
        join = " FROM market_listings l JOIN market_datasets d ON d.id=l.dataset_id AND d.tenant_id=l.tenant_id"
        total = int(await session.scalar(text(f"SELECT count(*){join} WHERE {where}"), params) or 0)
        rows = await session.execute(text(f"""
            SELECT l.id AS listing_id,l.platform_listing_id,l.title,l.brand,l.category_code,l.currency,
                   l.sale_price,l.list_price,l.rating,l.review_count,l.captured_at,l.first_available_date,
                   COALESCE(l.normalized_attributes->>'market_country', d.market_country) AS market_country,
                   l.normalized_attributes
              {join} WHERE {where}
             ORDER BY l.captured_at DESC,l.id DESC OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in rows.mappings().all()], total

    async def list_reviews(self, session: AsyncSession, *, tenant_id: int, dataset_id: int,
                           offset: int, limit: int, query: str | None,
                           sentiment: str | None) -> tuple[list[dict[str, Any]], int]:
        filters = ["r.tenant_id=:tenant", "r.dataset_id=:dataset"]
        params: dict[str, Any] = {"tenant": tenant_id, "dataset": dataset_id, "offset": offset, "limit": limit}
        if query:
            filters.append("(r.content_original ILIKE :query OR r.platform_review_id ILIKE :query OR l.title ILIKE :query)")
            params["query"] = f"%{query}%"
        if sentiment:
            filters.append("""COALESCE(r.sentiment, CASE
                WHEN r.rating>=4 THEN 'positive' WHEN r.rating<=2 THEN 'negative' ELSE 'neutral' END)=:sentiment""")
            params["sentiment"] = sentiment
        where = " AND ".join(filters)
        join = " FROM reviews r JOIN market_listings l ON l.id=r.listing_id"
        total = int(await session.scalar(text(f"SELECT count(*){join} WHERE {where}"), params) or 0)
        rows = await session.execute(text(f"""
            SELECT r.id AS review_id,r.platform_review_id,l.title AS listing_title,r.rating,
                   r.content_original,r.language_code,r.reviewer_location,r.sentiment,
                   r.reviewed_at,r.verified_purchase,r.is_valid,r.invalid_reason
              {join} WHERE {where}
             ORDER BY r.reviewed_at DESC NULLS LAST,r.id DESC OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in rows.mappings().all()], total

    async def list_bound_tasks(self, session: AsyncSession, *, tenant_id: int,
                               dataset_id: int) -> list[dict[str, Any]]:
        rows = await session.execute(text("""
            SELECT t.task_uuid::text,t.job_name,t.status,p.sku AS product_sku,p.name AS product_name,
                   report.report_uuid::text,t.created_at
              FROM analysis_tasks t
              LEFT JOIN products p ON p.id=t.product_id AND p.tenant_id=t.tenant_id
              LEFT JOIN LATERAL (
                SELECT r.report_uuid FROM analysis_reports r
                 WHERE r.analysis_job_id=t.id AND r.tenant_id=t.tenant_id
                 ORDER BY r.report_version DESC,r.id DESC LIMIT 1
              ) report ON true
             WHERE t.tenant_id=:tenant AND t.dataset_id=:dataset
             ORDER BY t.updated_at DESC,t.id DESC LIMIT 20
        """), {"tenant": tenant_id, "dataset": dataset_id})
        return [dict(row) for row in rows.mappings().all()]

    async def soft_delete(self, session: AsyncSession, *, tenant_id: int, dataset_id: int) -> bool:
        result = await session.execute(text("""
            UPDATE market_datasets SET deleted_at=CURRENT_TIMESTAMP,status='archived',updated_at=CURRENT_TIMESTAMP
             WHERE id=:dataset AND tenant_id=:tenant AND deleted_at IS NULL
        """), {"dataset": dataset_id, "tenant": tenant_id})
        return bool(result.rowcount)
