"""Tenant-isolated weekly data append, retraining, and atomic model publication."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.forecast_repository import ForecastRepository
from .demo_storage import DemoStorage
from .forecast_runtime import ForecastRuntime, TenantForecastRuntimeRegistry


class ForecastTrainingService:
    def __init__(self, settings: ApiSettings,
                 registry: TenantForecastRuntimeRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self.storage = DemoStorage(settings)

    @staticmethod
    def _validate_workbook(filename: str, content: bytes, label: str) -> None:
        if Path(filename).suffix.lower() != ".xlsx":
            raise BusinessError("FORECAST_APPEND_FILE_INVALID",
                                f"{label}必须是 .xlsx 工作簿", status_code=415)
        if not content:
            raise BusinessError("FILE_EMPTY", f"{label}为空", status_code=400)
        if not content.startswith(b"PK"):
            raise BusinessError("FORECAST_APPEND_FILE_INVALID",
                                f"{label}不是有效的 Excel 工作簿", status_code=422)

    async def accept(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     idempotency_key: str, orders_filename: str, orders_content: bytes,
                     inventory_filename: str | None, inventory_content: bytes | None) -> tuple[int, dict[str, Any]]:
        self._validate_workbook(orders_filename, orders_content, "订单文件")
        if inventory_content is not None:
            self._validate_workbook(inventory_filename or "inventory.xlsx",
                                    inventory_content, "库存文件")
        orders_digest = hashlib.sha256(orders_content).hexdigest()
        inventory_digest = (hashlib.sha256(inventory_content).hexdigest()
                            if inventory_content is not None else None)
        input_hash = hashlib.sha256(
            f"{orders_digest}:{inventory_digest or ''}".encode()).hexdigest()

        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                              {"key": 7_040_000 + tenant_id})
        existing = (await session.execute(text("""
            SELECT training_uuid::text,status,input_hash,source_snapshot,metrics,
                   error_code,error_message,created_at,started_at,completed_at
              FROM forecast_training_runs
             WHERE tenant_id=:tenant AND idempotency_key=:idem
        """), {"tenant": tenant_id, "idem": idempotency_key})).mappings().one_or_none()
        if existing is not None:
            if existing["input_hash"] != input_hash:
                raise BusinessError("IDEMPOTENCY_KEY_REUSED",
                                    "幂等键已用于不同的追加文件", status_code=409)
            return 200, self.projection(existing)

        active = (await session.execute(text("""
            SELECT training_uuid::text FROM forecast_training_runs
             WHERE tenant_id=:tenant AND status IN ('queued','running') LIMIT 1
        """), {"tenant": tenant_id})).scalar_one_or_none()
        if active:
            raise BusinessError("FORECAST_TRAINING_IN_PROGRESS",
                                "已有数据追加任务正在执行，请等待完成", status_code=409)

        orders_key, _ = self.storage.store(
            tenant_id=tenant_id, filename=orders_filename,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            content=orders_content)
        inventory_key = None
        if inventory_content is not None:
            inventory_key, _ = self.storage.store(
                tenant_id=tenant_id, filename=inventory_filename or "inventory.xlsx",
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                content=inventory_content)
        snapshot = {
            "orders": {"filename": Path(orders_filename).name, "storage_key": orders_key,
                       "size": len(orders_content), "sha256": orders_digest},
            "inventory": ({"filename": Path(inventory_filename or "inventory.xlsx").name,
                           "storage_key": inventory_key, "size": len(inventory_content or b""),
                           "sha256": inventory_digest} if inventory_key else None),
        }
        row = (await session.execute(text("""
            INSERT INTO forecast_training_runs
              (tenant_id,created_by,source_snapshot,algorithm_version,feature_version,
               status,update_kind,idempotency_key,input_hash)
            VALUES(:tenant,:user,CAST(:snapshot AS jsonb),'sales-forecast-v4','v4',
                   'queued','append',:idem,:input_hash)
            RETURNING training_uuid::text,status,input_hash,source_snapshot,metrics,
                      error_code,error_message,created_at,started_at,completed_at
        """), {"tenant": tenant_id, "user": user_id, "snapshot": json.dumps(snapshot),
                 "idem": idempotency_key, "input_hash": input_hash})).mappings().one()
        return 202, self.projection(row)

    @staticmethod
    def projection(row: Any) -> dict[str, Any]:
        data = dict(row)
        snapshot = data.pop("source_snapshot", {}) or {}
        data.pop("input_hash", None)
        data["orders_filename"] = (snapshot.get("orders") or {}).get("filename")
        data["inventory_filename"] = (snapshot.get("inventory") or {}).get("filename")
        return data

    async def list(self, session: AsyncSession, *, tenant_id: int,
                   limit: int = 20) -> list[dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT training_uuid::text,status,input_hash,source_snapshot,metrics,
                   error_code,error_message,created_at,started_at,completed_at
              FROM forecast_training_runs WHERE tenant_id=:tenant
             ORDER BY created_at DESC LIMIT :limit
        """), {"tenant": tenant_id, "limit": limit})).mappings().all()
        return [self.projection(row) for row in rows]

    async def get(self, session: AsyncSession, *, tenant_id: int,
                  training_uuid: str) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            SELECT training_uuid::text,status,input_hash,source_snapshot,metrics,
                   error_code,error_message,created_at,started_at,completed_at
              FROM forecast_training_runs
             WHERE tenant_id=:tenant AND training_uuid=CAST(:uuid AS uuid)
        """), {"tenant": tenant_id, "uuid": training_uuid})).mappings().one_or_none()
        return self.projection(row) if row else None

    def _stored_path(self, storage_key: str) -> Path:
        root = Path(self.settings.demo_storage_root).resolve()
        target = (root / storage_key).resolve()
        if root not in target.parents or not target.is_file():
            raise RuntimeError("上传工作簿已丢失或路径无效")
        return target

    async def execute(self, session: AsyncSession, *, tenant_id: int,
                      training_uuid: str) -> None:
        stage: Path | None = None
        final: Path | None = None
        published = False
        try:
            row = (await session.execute(text("""
                SELECT id,created_by,status,source_snapshot FROM forecast_training_runs
                 WHERE tenant_id=:tenant AND training_uuid=CAST(:uuid AS uuid) FOR UPDATE
            """), {"tenant": tenant_id, "uuid": training_uuid})).mappings().one_or_none()
            if row is None or row["status"] in {"succeeded", "cancelled"}:
                return
            if row["status"] == "running":
                return
            await session.execute(text("""
                UPDATE forecast_training_runs SET status='running',started_at=now(),
                       error_code=NULL,error_message=NULL
                 WHERE id=:id
            """), {"id": row["id"]})
            await session.commit()

            deployment = (await session.execute(text("""
                SELECT d.id deployment_id,d.route_policy,m.id model_id,m.state_uri,m.version
                  FROM forecast_model_deployments d JOIN forecast_models m ON m.id=d.model_id
                 WHERE d.tenant_id=:tenant AND d.scenario_code='sales_forecast'
                   AND d.status='active' AND m.status='active'
                 ORDER BY d.deployed_at DESC LIMIT 1
            """), {"tenant": tenant_id})).mappings().one_or_none()
            if deployment is None:
                raise RuntimeError("当前租户尚未发布可追加的预测模型")

            source = self.registry.state_dir(tenant_id=tenant_id,
                                             state_uri=deployment["state_uri"])
            artifact_root = Path(self.settings.forecast_artifact_root).resolve()
            tenant_root = artifact_root / str(tenant_id)
            tenant_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            stage = tenant_root / f".staging-{training_uuid}"
            final = tenant_root / training_uuid
            if stage.exists() or final.exists():
                raise RuntimeError("训练制品目录已存在")
            shutil.copytree(source, stage)

            snapshot = row["source_snapshot"]
            orders_path = self._stored_path(snapshot["orders"]["storage_key"])
            inventory = snapshot.get("inventory")
            inventory_path = self._stored_path(inventory["storage_key"]) if inventory else None
            runtime = ForecastRuntime(self.settings, state_dir=stage,
                                      model_version=f"sales-forecast-v4-append-{training_uuid[:8]}")
            append_metrics = await runtime.append_and_train(orders_path, inventory_path)
            metadata = runtime.metadata()
            if not metadata.get("ready"):
                raise RuntimeError(metadata.get("error", "新模型校验失败"))
            sku_rows = await runtime.list_skus(None, 5000)
            catalog_skus, catalog_pairs = await ForecastRepository().replace_catalog_from_engine(
                session, tenant_id=tenant_id, sku_rows=sku_rows)
            stage.rename(final)
            stage = None

            final_runtime = ForecastRuntime(self.settings, state_dir=final,
                                            model_version=runtime.model_version)
            final_metadata = final_runtime.metadata()
            checksum = final_metadata["state_checksum"]
            state_uri = f"server-managed://tenant/{tenant_id}/{training_uuid}"
            model_metrics = {"append": append_metrics, "sku_count": final_metadata.get("sku_count"),
                             "granularities": final_metadata.get("granularities")}
            training_date = final_metadata.get("data_through")
            model = (await session.execute(text("""
                INSERT INTO forecast_models
                  (model_code,owner_tenant_id,model_scope,version,engine,state_uri,state_checksum,
                   status,training_data_through,metrics)
                VALUES('sales_forecast',:tenant,'tenant_private',:version,:engine,:uri,:checksum,
                       'active',CAST(:through AS date),CAST(:metrics AS jsonb)) RETURNING id
            """), {"tenant": tenant_id, "version": runtime.model_version,
                     "engine": final_metadata["engine"], "uri": state_uri,
                     "checksum": checksum, "through": training_date,
                     "metrics": json.dumps(model_metrics)})).mappings().one()
            await session.execute(text("""
                UPDATE forecast_model_deployments SET status='inactive',retired_at=now()
                 WHERE tenant_id=:tenant AND scenario_code='sales_forecast' AND status='active'
            """), {"tenant": tenant_id})
            await session.execute(text("""
                INSERT INTO forecast_model_deployments
                  (tenant_id,model_id,scenario_code,status,route_policy,deployed_by)
                VALUES(:tenant,:model,'sales_forecast','active',CAST(:policy AS jsonb),:user)
            """), {"tenant": tenant_id, "model": model["id"],
                     "policy": json.dumps(deployment["route_policy"] or {}),
                     "user": row["created_by"]})
            result_metrics = {**append_metrics, "model_version": runtime.model_version,
                              "state_checksum": checksum, "catalog_pairs": catalog_pairs,
                              "catalog_skus": catalog_skus}
            await session.execute(text("""
                UPDATE forecast_training_runs SET status='succeeded',metrics=CAST(:metrics AS jsonb),
                       artifact_model_id=:model,completed_at=now()
                 WHERE id=:id
            """), {"metrics": json.dumps(result_metrics), "model": model["id"], "id": row["id"]})
            await session.commit()
            published = True
        except Exception as exc:
            await session.rollback()
            safe_message = str(exc)[:1000] or "数据追加与训练失败"
            await session.execute(text("""
                UPDATE forecast_training_runs SET status='failed',error_code='FORECAST_TRAINING_FAILED',
                       error_message=:message,completed_at=now()
                 WHERE tenant_id=:tenant AND training_uuid=CAST(:uuid AS uuid)
                   AND status<>'succeeded'
            """), {"tenant": tenant_id, "uuid": training_uuid, "message": safe_message})
            await session.commit()
            raise
        finally:
            if stage and stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            if final and final.exists() and not published:
                shutil.rmtree(final, ignore_errors=True)
