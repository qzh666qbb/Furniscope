"""Tenant-scoped market dataset read persistence."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import json


class DatasetRepository:
    _LIST_COLUMNS = """id AS dataset_id,name,platform,market_country,category_code,status,
        listing_count,review_count,valid_review_count,quality_score,data_start_date,data_end_date"""

    async def list(self, session: AsyncSession, *, tenant_id: int, offset: int, limit: int,
                   platform: str | None, market_country: str | None, category_code: str | None,
                   status: str | None) -> tuple[list[dict[str, Any]], int]:
        filters = ["tenant_id=:tenant_id", "deleted_at IS NULL"]
        params: dict[str, Any] = {"tenant_id": tenant_id, "offset": offset, "limit": limit}
        for column, value in (("platform", platform), ("market_country", market_country),
                              ("category_code", category_code), ("status", status)):
            if value:
                filters.append(f"{column}=:{column}")
                params[column] = value
        where = " AND ".join(filters)
        count = await session.scalar(text(f"SELECT count(*) FROM market_datasets WHERE {where}"), params)
        result = await session.execute(text(f"""
            SELECT {self._LIST_COLUMNS} FROM market_datasets WHERE {where}
             ORDER BY updated_at DESC,id DESC OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in result.mappings().all()], int(count or 0)

    async def get(self, session: AsyncSession, *, tenant_id: int, dataset_id: int) -> dict[str, Any] | None:
        result = await session.execute(text(f"""
            SELECT {self._LIST_COLUMNS},version_no,marketplace_code,source_type,source_name,
                   authorization_reference,field_mapping,quality_report,limitations
              FROM market_datasets
             WHERE id=:dataset_id AND tenant_id=:tenant_id AND deleted_at IS NULL
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
