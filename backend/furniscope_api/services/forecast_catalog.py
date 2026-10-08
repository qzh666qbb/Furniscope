"""Review SKU identity before training; freeze it for deployment and inference."""

import json

from sqlalchemy import text

from ..errors import BusinessError
from .sales_data_cleaner import sha256, stable_json


def contract_context(tenant_id):
    return f"data-contract://tenant/{tenant_id}/sales-v1"


def key(value):
    return str(value).strip().upper()


class ForecastCatalog:
    async def products(self, session, tenant_id):
        rows = (await session.execute(text("""
            SELECT id AS product_id,sku,category_code FROM products
             WHERE tenant_id=:tenant AND deleted_at IS NULL ORDER BY id
        """), {"tenant": tenant_id})).mappings().all()
        return [dict(row) for row in rows]

    async def aliases(self, session, tenant_id, context=None):
        rows = (await session.execute(text("""
            SELECT product_sku,source_sku FROM tenant_forecast_sku_aliases
             WHERE tenant_id=:tenant AND source_context=:context ORDER BY source_sku
        """), {"tenant": tenant_id, "context": context or contract_context(tenant_id)})).mappings().all()
        return [dict(row) for row in rows]

    async def configuration(self, session, tenant_id):
        items = await self.aliases(session, tenant_id)
        return {"items": items, "revision": sha256(stable_json(items)),
                "products": await self.products(session, tenant_id)}

    async def save(self, session, tenant_id, user_id, body):
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                              {"key": 7_040_000 + tenant_id})
        before = await self.configuration(session, tenant_id)
        if before["revision"] != body.expected_revision:
            raise BusinessError("SKU_MAPPING_CHANGED", "SKU映射已更新，请刷新后重试", status_code=409)
        products = {key(p["sku"]): p for p in before["products"]}
        items, sources, targets = [], set(), set()
        for item in body.items:
            source, target = key(item.source_sku), key(item.product_sku)
            if target not in products:
                raise BusinessError("SKU_MAPPING_PRODUCT_INVALID", "映射产品不存在或不属于本企业",
                                    status_code=422)
            if source in sources or target in targets:
                raise BusinessError("SKU_MAPPING_AMBIGUOUS", "SKU映射必须一一对应，不能重复或合并产品",
                                    status_code=422)
            sources.add(source)
            targets.add(target)
            items.append({"product_sku": products[target]["sku"], "source_sku": item.source_sku.strip()})
        await session.execute(text("""
            DELETE FROM tenant_forecast_sku_aliases WHERE tenant_id=:tenant AND source_context=:context
        """), {"tenant": tenant_id, "context": contract_context(tenant_id)})
        for item in items:
            await session.execute(text("""
                INSERT INTO tenant_forecast_sku_aliases(tenant_id,source_context,product_sku,source_sku)
                VALUES(:tenant,:context,:product_sku,:source_sku)
            """), {"tenant": tenant_id, "context": contract_context(tenant_id), **item})
        await session.execute(text("""
            INSERT INTO audit_logs(tenant_id,actor_user_id,action_code,resource_type,before_snapshot,after_snapshot)
            VALUES(:tenant,:user,'forecast.sku_mapping.save','forecast_sku_mapping',
                   CAST(:before AS jsonb),CAST(:after AS jsonb))
        """), {"tenant": tenant_id, "user": user_id, "before": json.dumps({"items": before["items"]}),
                 "after": json.dumps({"items": items})})
        return await self.configuration(session, tenant_id)

    async def preview(self, session, tenant_id, sku_rows, *, context=None, frozen=None):
        products = await self.products(session, tenant_id)
        by_sku = {}
        for product in products:
            by_sku.setdefault(key(product["sku"]), []).append(product)
        aliases = (frozen if frozen is not None else
                   await self.aliases(session, tenant_id, context))
        sources = {}
        for alias in aliases:
            sources.setdefault(key(alias["source_sku"]), []).append(alias)
        mapped, missing, conflicts, seen = [], [], [], {}
        for row in sku_rows:
            source, site = str(row["sku"]).strip(), key(row["site"])
            matches = sources.get(key(source), [])
            if len(matches) > 1:
                # Frozen rows may repeat the same binding across sites.
                matches = [a for a in matches if a.get("site") == site]
            alias = matches[0] if len(matches) == 1 else None
            target = (alias or {}).get("product_sku", (alias or {}).get("sku", source))
            candidates = by_sku.get(key(target), [])
            if len(matches) > 1 or len(candidates) > 1:
                conflicts.append({"source_sku": source, "site": site, "reason": "SKU大小写或映射存在歧义"})
                continue
            product = candidates[0] if candidates else None
            if product is None or (frozen is not None and (alias is None or
                    alias.get("product_id") != product["product_id"])):
                missing.append({"source_sku": source, "site": site, "product_sku": target})
                continue
            identity = (product["product_id"], site)
            if identity in seen:
                conflicts.append({"source_sku": source, "site": site,
                                  "product_sku": product["sku"],
                                  "reason": f"与 {seen[identity]} 指向同一产品和站点"})
                continue
            seen[identity] = source
            mapped.append({**product, "source_sku": source, "site": site,
                           "history_weeks": int(row.get("history_weeks") or 0)})
        return {"items": mapped, "unmapped": missing, "conflicts": conflicts,
                "can_publish": bool(mapped) and not missing and not conflicts}

    @staticmethod
    def require_valid(preview):
        if not preview["can_publish"]:
            def examples(rows):
                return "、".join(
                    f"{row.get('source_sku', '未知SKU')}@{row.get('site', '未知站点')}"
                    for row in rows[:5]
                )

            problems = []
            if preview["unmapped"]:
                problems.append(
                    f"未关联 {len(preview['unmapped'])} 个"
                    f"（{examples(preview['unmapped'])}）"
                )
            if preview["conflicts"]:
                problems.append(
                    f"冲突 {len(preview['conflicts'])} 个"
                    f"（{examples(preview['conflicts'])}）"
                )
            summary = "；".join(problems) or "没有可发布的产品身份"
            raise BusinessError("FORECAST_SKU_MAPPING_REQUIRED",
                                f"产品编码对照未完成：{summary}。"
                                "请在训练预览中将来源SKU一一绑定到产品中心SKU，"
                                "或先在产品中心创建同编码产品，然后重新预览",
                                status_code=422,
                                details=[preview])
        return preview["items"]

    async def replace(self, session, tenant_id, items):
        if not items:
            raise BusinessError("FORECAST_CATALOG_EMPTY", "不能发布空预测目录", status_code=422)
        await session.execute(text("DELETE FROM tenant_sku_catalog WHERE tenant_id=:tenant"),
                              {"tenant": tenant_id})
        for item in items:
            await session.execute(text("""
                INSERT INTO tenant_sku_catalog(tenant_id,sku,site,category_code,lifecycle_status,
                  label_status,history_weeks,model_eligible,attributes)
                VALUES(:tenant,:sku,:site,:category_code,'active','complete',:history_weeks,true,
                       CAST(:attributes AS jsonb))
            """), {"tenant": tenant_id, **item,
                     "attributes": json.dumps({"source_sku": item["source_sku"],
                                               "product_id": item["product_id"]})})
        return len({p["sku"] for p in items}), len(items)
