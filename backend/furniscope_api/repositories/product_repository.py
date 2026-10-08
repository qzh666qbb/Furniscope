"""Tenant-scoped product persistence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ProductRepository:
    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     sku: str, name: str, category_code: str, description: str | None,
                     lifecycle_status: str = "active") -> dict[str, Any] | None:
        result = await session.execute(text("""
            INSERT INTO products
              (tenant_id,sku,name,category_code,description,lifecycle_status,created_by)
            VALUES
              (:tenant_id,:sku,:name,:category_code,:description,:lifecycle_status,:user_id)
            ON CONFLICT DO NOTHING
            RETURNING id AS product_id,sku,name,category_code,lifecycle_status,
                      analysis_status,current_profile_version_id
        """), {"tenant_id": tenant_id, "user_id": user_id, "sku": sku, "name": name,
               "category_code": category_code, "description": description,
               "lifecycle_status": lifecycle_status})
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
            SELECT id AS product_id,sku,name,category_code,lifecycle_status,analysis_status,
                   current_profile_version_id,
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

    async def get_product(self, session: AsyncSession, *, tenant_id: int, product_id: int,
                          for_update: bool = False) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT id AS product_id,sku,name,category_code,lifecycle_status,description,analysis_status,
                   current_profile_version_id,updated_at
              FROM products WHERE id=:product_id AND tenant_id=:tenant_id AND deleted_at IS NULL
        """ + (" FOR UPDATE" if for_update else "")), {"product_id": product_id, "tenant_id": tenant_id})
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
            SELECT pv.id FROM product_profile_versions pv JOIN products p
              ON p.current_profile_version_id=pv.id AND p.tenant_id=pv.tenant_id
             WHERE pv.tenant_id=:tenant_id AND pv.product_id=:product_id AND p.id=:product_id
               AND pv.status='draft'
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
                     sku: str | None, name: str | None, category_code: str | None,
                     lifecycle_status: str | None, description: str | None,
                     description_is_set: bool, analysis_status: str | None,
                     profile_version_id: int | None, attributes: list[dict[str, Any]] | None) -> dict[str, Any]:
        await session.execute(text("""
            UPDATE products SET sku=COALESCE(:sku,sku),name=COALESCE(:name,name),
                   category_code=COALESCE(:category_code,category_code),
                   lifecycle_status=COALESCE(:lifecycle_status,lifecycle_status),
                   description=CASE WHEN :description_is_set THEN :description ELSE description END,
                   analysis_status=COALESCE(:analysis_status,analysis_status),
                   current_profile_version_id=COALESCE(:profile_version_id,current_profile_version_id)
             WHERE id=:product_id AND tenant_id=:tenant_id
        """), {"sku": sku, "name": name, "category_code": category_code,
                 "lifecycle_status": lifecycle_status, "description": description,
                 "description_is_set": description_is_set,
                 "analysis_status": analysis_status,
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
            SELECT id AS product_id,sku,name,category_code,lifecycle_status,description,analysis_status,
                   current_profile_version_id AS profile_version_id,updated_at
              FROM products WHERE id=:product_id AND tenant_id=:tenant_id
        """), {"product_id": product_id, "tenant_id": tenant_id})
        return dict(result.mappings().one())

    async def update_sku_references(
        self, session: AsyncSession, *, tenant_id: int, old_sku: str, new_sku: str
    ) -> None:
        await session.execute(text("""
            UPDATE tenant_forecast_sku_aliases SET product_sku=:new_sku
             WHERE tenant_id=:tenant_id AND upper(btrim(product_sku))=upper(btrim(:old_sku))
        """), {"tenant_id": tenant_id, "old_sku": old_sku, "new_sku": new_sku})
        await session.execute(text("""
            UPDATE tenant_sku_catalog SET sku=:new_sku
             WHERE tenant_id=:tenant_id AND upper(btrim(sku))=upper(btrim(:old_sku))
        """), {"tenant_id": tenant_id, "old_sku": old_sku, "new_sku": new_sku})
        await session.execute(text("""
            SELECT rename_tenant_sku_facts(:tenant_id,:old_sku,:new_sku)
        """), {"tenant_id": tenant_id, "old_sku": old_sku, "new_sku": new_sku})

    async def archive(
        self, session: AsyncSession, *, tenant_id: int, product_id: int
    ) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            UPDATE products
               SET lifecycle_status='discontinued',analysis_status='archived',
                   deleted_at=CURRENT_TIMESTAMP
             WHERE id=:product_id AND tenant_id=:tenant_id AND deleted_at IS NULL
            RETURNING id AS product_id,sku,lifecycle_status,analysis_status,
                      deleted_at AS archived_at
        """), {"tenant_id": tenant_id, "product_id": product_id})).mappings().one_or_none()
        if row:
            await session.execute(text("""
                DELETE FROM tenant_forecast_sku_aliases
                 WHERE tenant_id=:tenant_id
                   AND upper(btrim(product_sku))=upper(btrim(:sku))
            """), {"tenant_id": tenant_id, "sku": row["sku"]})
        return dict(row) if row else None

    async def relations(
        self, session: AsyncSession, *, tenant_id: int, product_id: int
    ) -> list[dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT g.id AS group_id,g.group_type,g.code AS group_code,
                   g.name AS group_name,m.member_role,m.quantity
              FROM product_group_members m
              JOIN product_groups g
                ON g.id=m.group_id AND g.tenant_id=m.tenant_id
             WHERE m.tenant_id=:tenant_id AND m.product_id=:product_id
             ORDER BY g.group_type,g.code,m.member_role
        """), {"tenant_id": tenant_id, "product_id": product_id})).mappings().all()
        return [dict(row) for row in rows]

    async def replace_relations(
        self, session: AsyncSession, *, tenant_id: int, user_id: int,
        product_id: int, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        await session.execute(text("""
            DELETE FROM product_group_members
             WHERE tenant_id=:tenant_id AND product_id=:product_id
        """), {"tenant_id": tenant_id, "product_id": product_id})
        for item in items:
            group_id = await session.scalar(text("""
                INSERT INTO product_groups
                  (tenant_id,group_type,code,name,created_by)
                VALUES
                  (:tenant_id,:group_type,:group_code,:group_name,:user_id)
                ON CONFLICT(tenant_id,group_type,code) DO UPDATE
                  SET name=EXCLUDED.name
                RETURNING id
            """), {"tenant_id": tenant_id, "user_id": user_id, **item})
            await session.execute(text("""
                INSERT INTO product_group_members
                  (tenant_id,group_id,product_id,member_role,quantity,created_by)
                VALUES
                  (:tenant_id,:group_id,:product_id,:member_role,:quantity,:user_id)
            """), {"tenant_id": tenant_id, "group_id": group_id,
                     "product_id": product_id, "user_id": user_id, **item})
        await session.execute(text("""
            DELETE FROM product_groups g
             WHERE g.tenant_id=:tenant_id
               AND NOT EXISTS(
                 SELECT 1 FROM product_group_members m
                  WHERE m.tenant_id=g.tenant_id AND m.group_id=g.id
               )
        """), {"tenant_id": tenant_id})
        return await self.relations(session, tenant_id=tenant_id, product_id=product_id)

    async def inventory_summary(
        self, session: AsyncSession, *, tenant_id: int, product_id: int, sku: str
    ) -> dict[str, Any]:
        rows = (await session.execute(text("""
            WITH ranked AS (
              SELECT f.site,f.inventory_units,f.fact_date,
                     v.version_uuid::text AS source_version_uuid,
                     row_number() OVER(
                       PARTITION BY f.site
                       ORDER BY f.fact_date DESC,v.confirmed_at DESC NULLS LAST,v.id DESC
                     ) AS row_rank
                FROM inventory_facts_daily f
                JOIN forecast_data_versions v
                  ON v.id=f.data_version_id AND v.tenant_id=f.tenant_id
                 AND v.status='confirmed'
               WHERE f.tenant_id=:tenant_id
                 AND upper(btrim(f.sku))=upper(btrim(:sku))
            )
            SELECT site,inventory_units,fact_date AS as_of_date,source_version_uuid
              FROM ranked WHERE row_rank=1 ORDER BY site
        """), {"tenant_id": tenant_id, "sku": sku})).mappings().all()
        latest = (await session.execute(text("""
            SELECT status,filename,version_uuid::text AS version_uuid,
                   COALESCE(confirmed_at,created_at) AS updated_at
              FROM forecast_data_versions
             WHERE tenant_id=:tenant_id AND rules->>'kind'='inventory'
             ORDER BY created_at DESC,id DESC LIMIT 1
        """), {"tenant_id": tenant_id})).mappings().one_or_none()
        sites = [dict(row) for row in rows]
        return {
            "product_id": product_id,
            "sku": sku,
            "is_realtime": False,
            "source": "inventory_facts_daily",
            "as_of_date": max((row["as_of_date"] for row in rows), default=None),
            "total_inventory_units": sum(
                (row["inventory_units"] for row in rows), start=0
            ),
            "sites": sites,
            "import_status": dict(latest) if latest else {"status": "none"},
        }

    async def confirm_profile(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                              product_id: int, profile_version_id: int,
                              codes: list[str]) -> dict[str, Any] | None:
        status = await session.scalar(text("""
            SELECT status FROM product_profile_versions
             WHERE id=:profile_version_id AND tenant_id=:tenant_id AND product_id=:product_id
             FOR UPDATE
        """), {"profile_version_id": profile_version_id, "tenant_id": tenant_id, "product_id": product_id})
        if status not in {"draft", "parsed"}:
            return None
        await session.execute(text("""
            UPDATE product_attributes SET confirmation_status='confirmed',
              source_locator=COALESCE(source_locator,'{}'::jsonb) ||
                jsonb_build_object('confirmed_by',CAST(:user_id AS bigint),'confirmed_at',CURRENT_TIMESTAMP)
             WHERE tenant_id=:tenant_id AND profile_version_id=:profile_version_id
               AND attribute_code=ANY(:codes)
        """), {"tenant_id": tenant_id, "profile_version_id": profile_version_id, "codes": codes, "user_id": user_id})
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
