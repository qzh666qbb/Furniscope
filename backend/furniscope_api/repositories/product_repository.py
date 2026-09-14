"""Tenant-scoped product persistence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ProductRepository:
    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     sku: str, name: str, category_code: str, description: str | None) -> dict[str, Any] | None:
        result = await session.execute(text("""
            INSERT INTO products (tenant_id,sku,name,category_code,description,created_by)
            VALUES (:tenant_id,:sku,:name,:category_code,:description,:user_id)
            ON CONFLICT (tenant_id,sku) DO NOTHING
            RETURNING id AS product_id,sku,name,category_code,analysis_status,current_profile_version_id
        """), {"tenant_id": tenant_id, "user_id": user_id, "sku": sku, "name": name,
               "category_code": category_code, "description": description})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list(self, session: AsyncSession, *, tenant_id: int, offset: int, limit: int,
                   analysis_status: str | None, category_code: str | None,
                   keyword: str | None) -> tuple[list[dict[str, Any]], int]:
        filters = ["tenant_id=:tenant_id", "deleted_at IS NULL"]
        params: dict[str, Any] = {"tenant_id": tenant_id, "offset": offset, "limit": limit}
        if analysis_status:
            filters.append("analysis_status=:analysis_status")
            params["analysis_status"] = analysis_status
        if category_code:
            filters.append("category_code=:category_code")
            params["category_code"] = category_code
        if keyword:
            filters.append("(sku ILIKE :keyword OR name ILIKE :keyword)")
            params["keyword"] = f"%{keyword}%"
        where = " AND ".join(filters)
        count = await session.scalar(text(f"SELECT count(*) FROM products WHERE {where}"), params)
        result = await session.execute(text(f"""
            SELECT id AS product_id,sku,name,category_code,analysis_status,current_profile_version_id,
                   created_at,updated_at,
                   EXISTS(
                     SELECT 1 FROM product_attributes pa
                      WHERE pa.profile_version_id=products.current_profile_version_id
                        AND pa.confirmation_status='conflicted'
                   ) AS has_conflicts,
                   (SELECT pa.value #>> '{{}}' FROM product_attributes pa
                     WHERE pa.profile_version_id=products.current_profile_version_id
                       AND pa.attribute_code='moq' LIMIT 1) AS moq,
                   (SELECT pa.value #>> '{{}}' FROM product_attributes pa
                     WHERE pa.profile_version_id=products.current_profile_version_id
                       AND pa.attribute_code='factory_price' LIMIT 1) AS factory_price
              FROM products WHERE {where}
             ORDER BY updated_at DESC,id DESC OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in result.mappings().all()], int(count or 0)

    async def get_product(self, session: AsyncSession, *, tenant_id: int, product_id: int) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT id AS product_id,sku,name,category_code,description,analysis_status,
                   current_profile_version_id,updated_at
              FROM products WHERE id=:product_id AND tenant_id=:tenant_id AND deleted_at IS NULL
        """), {"product_id": product_id, "tenant_id": tenant_id})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_profile(self, session: AsyncSession, *, tenant_id: int, product_id: int,
                          profile_version_id: int | None) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT pv.id AS profile_version_id,pv.version_no AS profile_version,pv.status,
                   pv.completeness_score,pv.source_summary,pv.updated_at
              FROM product_profile_versions pv JOIN products p ON p.id=pv.product_id
             WHERE pv.tenant_id=:tenant_id AND pv.product_id=:product_id
               AND pv.id=COALESCE(:profile_version_id,p.current_profile_version_id)
        """), {"tenant_id": tenant_id, "product_id": product_id, "profile_version_id": profile_version_id})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def attributes(self, session: AsyncSession, *, tenant_id: int, profile_version_id: int) -> list[dict[str, Any]]:
        result = await session.execute(text("""
            SELECT attribute_code,attribute_code AS attribute_name,jsonb_typeof(value) AS value_type,
                   value AS attribute_value,unit,source_type,source_locator,confidence,confirmation_status
              FROM product_attributes WHERE tenant_id=:tenant_id AND profile_version_id=:profile_version_id
             ORDER BY attribute_code
        """), {"tenant_id": tenant_id, "profile_version_id": profile_version_id})
        return [dict(row) for row in result.mappings().all()]

    async def ensure_draft_profile(self, session: AsyncSession, *, tenant_id: int, product_id: int) -> int:
        existing = await session.scalar(text("""
            SELECT id FROM product_profile_versions WHERE tenant_id=:tenant_id AND product_id=:product_id
              AND status='draft' ORDER BY version_no DESC LIMIT 1
        """), {"tenant_id": tenant_id, "product_id": product_id})
        if existing:
            return int(existing)
        result = await session.execute(text("""
            INSERT INTO product_profile_versions(tenant_id,product_id,version_no,schema_version,status)
            SELECT :tenant_id,:product_id,COALESCE(max(version_no),0)+1,'sofa-profile-v1','draft'
              FROM product_profile_versions WHERE product_id=:product_id
            RETURNING id
        """), {"tenant_id": tenant_id, "product_id": product_id})
        profile_id = int(result.scalar_one())
        await session.execute(text("""
            INSERT INTO product_attributes
              (tenant_id,profile_version_id,attribute_code,value,unit,source_type,source_asset_id,
               source_locator,confidence,confirmation_status)
            SELECT pa.tenant_id,:profile_id,pa.attribute_code,pa.value,pa.unit,pa.source_type,
                   pa.source_asset_id,pa.source_locator,pa.confidence,pa.confirmation_status
              FROM product_attributes pa
              JOIN products p ON p.current_profile_version_id=pa.profile_version_id
             WHERE p.id=:product_id AND p.tenant_id=:tenant_id
            ON CONFLICT(profile_version_id,attribute_code) DO NOTHING
        """), {"profile_id": profile_id, "product_id": product_id, "tenant_id": tenant_id})
        return profile_id

    async def update(self, session: AsyncSession, *, tenant_id: int, product_id: int,
                     name: str | None, description: str | None, analysis_status: str | None,
                     profile_version_id: int | None, attributes: list[dict[str, Any]] | None) -> dict[str, Any]:
        await session.execute(text("""
            UPDATE products SET name=COALESCE(:name,name),description=COALESCE(:description,description),
                   analysis_status=COALESCE(:analysis_status,analysis_status),
                   current_profile_version_id=COALESCE(:profile_version_id,current_profile_version_id)
             WHERE id=:product_id AND tenant_id=:tenant_id
        """), {"name": name, "description": description, "analysis_status": analysis_status,
                 "profile_version_id": profile_version_id, "product_id": product_id, "tenant_id": tenant_id})
        if attributes is not None and profile_version_id is not None:
            for item in attributes:
                await session.execute(text("""
                    INSERT INTO product_attributes
                      (tenant_id,profile_version_id,attribute_code,value,unit,source_type,source_locator,confidence,confirmation_status)
                    VALUES(:tenant_id,:profile_version_id,:attribute_code,CAST(:value AS jsonb),:unit,:source_type,
                           CAST(:source_locator AS jsonb),:confidence,:confirmation_status)
                    ON CONFLICT(profile_version_id,attribute_code) DO UPDATE SET
                      value=excluded.value,unit=excluded.unit,source_type=excluded.source_type,
                      source_locator=excluded.source_locator,confidence=excluded.confidence,
                      confirmation_status=excluded.confirmation_status
                """), item | {"tenant_id": tenant_id, "profile_version_id": profile_version_id})
        result = await session.execute(text("""
            SELECT id AS product_id,sku,name,category_code,description,analysis_status,
                   current_profile_version_id AS profile_version_id,updated_at
              FROM products WHERE id=:product_id AND tenant_id=:tenant_id
        """), {"product_id": product_id, "tenant_id": tenant_id})
        return dict(result.mappings().one())

    async def confirm_profile(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                              product_id: int, profile_version_id: int,
                              codes: list[str]) -> dict[str, Any] | None:
        await session.execute(text("""
            UPDATE product_attributes SET confirmation_status='confirmed'
             WHERE tenant_id=:tenant_id AND profile_version_id=:profile_version_id
               AND attribute_code=ANY(:codes)
        """), {"tenant_id": tenant_id, "profile_version_id": profile_version_id, "codes": codes})
        stats = (await session.execute(text("""
            SELECT count(*) total,count(*) FILTER(WHERE confirmation_status='confirmed') confirmed,
                   count(*) FILTER(WHERE confirmation_status='conflicted') conflicted
              FROM product_attributes WHERE tenant_id=:tenant_id AND profile_version_id=:profile_version_id
        """), {"tenant_id": tenant_id, "profile_version_id": profile_version_id})).mappings().one()
        if stats["conflicted"]:
            raise ValueError("conflicted")
        if not stats["total"] or stats["confirmed"] != stats["total"]:
            raise ValueError("incomplete")
        result = await session.execute(text("""
            UPDATE product_profile_versions SET status='confirmed',completeness_score=1,
                   confirmed_by=:user_id,confirmed_at=CURRENT_TIMESTAMP
             WHERE id=:profile_version_id AND tenant_id=:tenant_id AND product_id=:product_id
               AND status IN ('draft','parsed')
            RETURNING product_id,id AS profile_version_id,status,completeness_score,confirmed_at
        """), {"user_id": user_id, "profile_version_id": profile_version_id,
                 "tenant_id": tenant_id, "product_id": product_id})
        row = result.mappings().one_or_none()
        if row:
            await session.execute(text("""
                UPDATE products SET current_profile_version_id=:profile_version_id,
                       analysis_status='ready' WHERE id=:product_id AND tenant_id=:tenant_id
            """), {"profile_version_id": profile_version_id,
                     "product_id": product_id, "tenant_id": tenant_id})
        return dict(row) if row else None
