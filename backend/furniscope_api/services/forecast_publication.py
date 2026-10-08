"""The sole model/catalog switch used by training publication and rollback."""

import json

from sqlalchemy import text

from ..errors import BusinessError
from ..repositories.forecast_repository import ForecastRepository
from .forecast_catalog import ForecastCatalog


class ForecastPublication:
    async def activate(self, session, *, tenant_id, model_id, user_id, catalog,
                       expected_deployment_id, source):
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                              {"key": 7_040_000 + tenant_id})
        current = await ForecastRepository().active_deployment(session, tenant_id=tenant_id)
        if (current["deployment_id"] if current else None) != expected_deployment_id:
            raise BusinessError("FORECAST_DEPLOYMENT_CHANGED", "当前部署已变化，请刷新后重试",
                                status_code=409)
        await session.execute(text("""
            SELECT id FROM products WHERE tenant_id=:tenant AND deleted_at IS NULL FOR SHARE
        """), {"tenant": tenant_id})
        service = ForecastCatalog()
        checked = await service.preview(session, tenant_id, [
            {**row, "sku": row["source_sku"]} for row in catalog], frozen=catalog)
        items = service.require_valid(checked)
        counts = await service.replace(session, tenant_id, items)
        await session.execute(text("""
            UPDATE forecast_model_deployments SET status='inactive',retired_at=now()
             WHERE tenant_id=:tenant AND scenario_code='sales_forecast' AND status='active'
        """), {"tenant": tenant_id})
        deployed = (await session.execute(text("""
            INSERT INTO forecast_model_deployments
              (tenant_id,model_id,scenario_code,status,route_policy,deployed_by)
            VALUES(:tenant,:model,'sales_forecast','active',CAST(:policy AS jsonb),:user)
            RETURNING deployment_uuid::text,deployed_at
        """), {"tenant": tenant_id, "model": model_id, "user": user_id,
                 "policy": json.dumps({"source": source, "catalog_snapshot": items})})).mappings().one()
        return {**dict(deployed), "catalog_skus": counts[0], "catalog_pairs": counts[1]}

    async def rollback(self, session, registry, *, tenant_id, deployment_uuid, user_id):
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                              {"key": 7_040_000 + tenant_id})
        target = (await session.execute(text("""
            SELECT d.id,d.model_id,d.route_policy,m.version,m.status AS model_status,m.state_uri,
                   m.state_checksum,m.model_scope,m.owner_tenant_id
              FROM forecast_model_deployments d JOIN forecast_models m ON m.id=d.model_id
             WHERE d.tenant_id=:tenant AND d.scenario_code='sales_forecast'
               AND d.deployment_uuid=CAST(:uuid AS uuid)
        """), {"tenant": tenant_id, "uuid": str(deployment_uuid)})).mappings().one_or_none()
        if target is None:
            raise BusinessError("FORECAST_DEPLOYMENT_NOT_FOUND", "模型版本不存在", status_code=404)
        target = dict(target)
        if target["model_status"] != "active":
            raise BusinessError("FORECAST_MODEL_NOT_DEPLOYABLE", "该模型不能部署", status_code=409)
        current = await ForecastRepository().active_deployment(session, tenant_id=tenant_id)
        if current and current["deployment_id"] == target["id"]:
            raise BusinessError("FORECAST_MODEL_ALREADY_ACTIVE", "该版本已是当前模型", status_code=409)
        catalog = target["route_policy"].get("catalog_snapshot")
        if not catalog:
            raise BusinessError("FORECAST_CATALOG_SNAPSHOT_REQUIRED",
                                "历史版本缺少可信SKU映射快照，请使用标准数据重新建模", status_code=409)
        try:
            runtime = registry.resolve(tenant_id=tenant_id, deployment=target)
            metadata = runtime.metadata()
            if not metadata.get("ready") or metadata.get("state_checksum") != target["state_checksum"]:
                raise ValueError("checksum mismatch or missing files")
            engine_rows = await runtime.list_skus(None, 100_000)
            available = {(str(row["sku"]), str(row["site"])) for row in engine_rows}
            if any((row["source_sku"], row["site"]) not in available for row in catalog):
                raise ValueError("catalog disagrees with artifact")
        except (OSError, ValueError, RuntimeError) as exc:
            raise BusinessError("FORECAST_CHECKSUM_MISMATCH", "历史模型制品校验失败，未切换当前模型",
                                status_code=409) from exc
        deployed = await self.activate(session, tenant_id=tenant_id, model_id=target["model_id"],
            user_id=user_id, catalog=catalog,
            expected_deployment_id=current["deployment_id"] if current else None, source="admin_rollback")
        return {"tenant_id": tenant_id, "model_id": target["model_id"],
                "version": target["version"], **deployed}, current
