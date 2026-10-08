"""Confirmed tenant data -> independent training -> holdout gate -> atomic release."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from furniscope_forecast import tenant_engine

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.forecast_repository import ForecastRepository
from ..schemas.data_imports import ImportRules
from .forecast_catalog import ForecastCatalog
from .forecast_data_service import ForecastDataService
from .forecast_publication import ForecastPublication
from .forecast_runtime import ForecastRuntime, TenantForecastRuntimeRegistry, coerce_training_date
from .sales_data_cleaner import sha256, stable_json
from .training_lifecycle import TrainingLifecycle

SELECT_RUN = """SELECT training_uuid::text,status,update_kind,source_snapshot,metrics,
    data_version_id,parent_data_version_id,code_sha256,
    execution_attempts,heartbeat_at,lease_expires_at,
    error_code,error_message,created_at,started_at,completed_at FROM forecast_training_runs"""


class ForecastTrainingService:
    def __init__(self, settings: ApiSettings, registry: TenantForecastRuntimeRegistry):
        self.settings = settings
        self.registry = registry
        self.data = ForecastDataService(settings)
        self.lifecycle = TrainingLifecycle(settings)

    async def _inputs(self, session, *, tenant_id, version_uuid, mode):
        data = await self.data.row(session, tenant_id, version_uuid)
        incoming = self.data.records(tenant_id, data)
        current = await ForecastRepository().active_deployment(session, tenant_id=tenant_id)
        parent = None
        if current and current["model_scope"] == "tenant_private":
            parent = (await session.execute(text("""
                SELECT data_version_id,source_snapshot FROM forecast_training_runs
                 WHERE tenant_id=:tenant AND artifact_model_id=:model AND status='succeeded'
                   AND data_version_id IS NOT NULL ORDER BY id DESC LIMIT 1
            """), {"tenant": tenant_id, "model": current["model_id"]})).mappings().one_or_none()
        if mode == "append" and parent is None:
            raise BusinessError("FORECAST_TRUSTED_HISTORY_REQUIRED",
                                "当前模型没有本企业标准数据血缘，请上传完整历史进行首次建模",
                                status_code=409)
        if mode == "initial" and parent:
            raise BusinessError("FORECAST_ALREADY_INITIALIZED", "已有企业模型，请选择追加或完整重建",
                                status_code=409)
        ids = list(parent["source_snapshot"]["data_versions"]) if mode == "append" else []
        ids = [version for version in ids if version != data["version_uuid"]] + [data["version_uuid"]]
        rows = [await self.data.row(session, tenant_id, version) for version in ids]
        basis = data["rules"]["sales_basis"]
        if any(row["rules"].get("sales_basis") != basis for row in rows):
            raise BusinessError("DATA_CONTRACT_MISMATCH", "不能合并不同销量口径的数据版本",
                                status_code=422)
        merged = {}
        overwritten = []
        overlap = 0
        for row in rows:
            for record in self.data.records(tenant_id, row):
                key = (record["date"], record["sku"], record["site"])
                if key in merged and row["version_uuid"] == data["version_uuid"]:
                    overlap += 1
                    if merged[key]["sales"] != record["sales"]:
                        overwritten.append({"date": key[0], "sku": key[1], "site": key[2],
                                            "old_sales": merged[key]["sales"],
                                            "new_sales": record["sales"]})
                merged[key] = record
        records = sorted(merged.values(), key=lambda r: (r["sku"], r["site"], r["date"]))
        order_locations = {}
        for record in records:
            for order_key in record.get("order_keys", []):
                key = tuple(order_key)
                location = (record["date"], record["sku"], record["site"])
                if key in order_locations and order_locations[key] != location:
                    raise BusinessError("DATA_ORDER_CONFLICT",
                        "合并历史后同一订单行出现在不同日期或SKU，请更正原日期快照后重新预览", status_code=422)
                order_locations[key] = location
        gaps = []
        last_by_pair = {}
        for record in records:
            pair = (record["sku"], record["site"])
            day = date.fromisoformat(record["date"])
            previous = last_by_pair.get(pair)
            if previous and (day - previous).days > 1:
                gaps.append({"sku": pair[0], "site": pair[1],
                             "after": previous.isoformat(), "before": day.isoformat(),
                             "missing_days": (day - previous).days - 1})
            last_by_pair[pair] = day
        pairs = sorted({(r["sku"], r["site"]) for r in records})
        catalog = await ForecastCatalog().preview(session, tenant_id, [
            {"sku": sku, "site": site} for sku, site in pairs])
        return data, current, parent, rows, records, {
            "mode": mode, "input_rows": len(incoming), "merged_rows": len(records),
            "overlap_rows": overlap, "overwritten_rows": len(overwritten),
            "overwrites": overwritten[:50],
            "data_versions": ids, "sales_basis": basis,
            "missing_days": sum(gap["missing_days"] for gap in gaps), "gaps": gaps[:50],
            "catalog": catalog,
        }

    async def preview(self, session, *, tenant_id, version_uuid, mode):
        *_, preview = await self._inputs(session, tenant_id=tenant_id,
                                        version_uuid=version_uuid, mode=mode)
        return preview

    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     version_uuid: str, mode: str, allow_history_overwrite: bool,
                     idempotency_key: str) -> tuple[int, dict]:
        if mode not in {"initial", "append", "rebuild"}:
            raise BusinessError("TRAINING_MODE_INVALID", "训练模式无效", status_code=422)
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                              {"key": 7_040_000 + tenant_id})
        request_hash = sha256(stable_json({"version": version_uuid, "mode": mode,
                                          "overwrite": allow_history_overwrite}))
        existing = (await session.execute(text(SELECT_RUN + """
            WHERE tenant_id=:tenant AND idempotency_key=:idem
        """), {"tenant": tenant_id, "idem": idempotency_key})).mappings().one_or_none()
        if existing:
            if existing["source_snapshot"].get("request_hash") != request_hash:
                raise BusinessError("IDEMPOTENCY_KEY_REUSED", "幂等键已用于不同训练请求", status_code=409)
            return 200, self.projection(existing)
        active = (await session.execute(text("""
            SELECT id FROM forecast_training_runs WHERE tenant_id=:tenant
             AND status IN ('queued','running') LIMIT 1
        """), {"tenant": tenant_id})).scalar_one_or_none()
        if active:
            raise BusinessError("FORECAST_TRAINING_IN_PROGRESS", "已有训练任务正在执行", status_code=409)
        data, current, parent, sources, _, preview = await self._inputs(
            session, tenant_id=tenant_id, version_uuid=version_uuid, mode=mode)
        if preview["missing_days"]:
            raise BusinessError("DATA_MERGED_GAPS",
                                "数据版本之间存在日期缺口，请补齐完整历史后再训练",
                                status_code=422, details=[preview])
        ForecastCatalog.require_valid(preview["catalog"])
        if preview["overwritten_rows"] and not allow_history_overwrite:
            raise BusinessError("DATA_OVERWRITE_CONFIRMATION_REQUIRED",
                                f"将修改{preview['overwritten_rows']}条历史销量，请先查看合并预览再确认",
                                status_code=409, details=[preview])
        code_sha = sha256(Path(tenant_engine.__file__).read_bytes())
        snapshot = {**preview, "request_hash": request_hash,
                    "orders": {"filename": data["filename"]},
                    "sources": [{"uuid": row["version_uuid"], "sha256": row["canonical_sha256"]}
                                for row in sources]}
        result = (await session.execute(text("""
            INSERT INTO forecast_training_runs
              (tenant_id,created_by,source_snapshot,algorithm_version,feature_version,
               status,update_kind,idempotency_key,input_hash,data_version_id,parent_data_version_id,
               baseline_deployment_id,code_sha256)
            VALUES(:tenant,:user,CAST(:snapshot AS jsonb),:algorithm,'causal-lags-v1',
                   'queued',:mode,:idem,:hash,:data,:parent,:deployment,:code)
            RETURNING training_uuid::text
        """), {"tenant": tenant_id, "user": user_id, "snapshot": json.dumps(snapshot),
                 "algorithm": tenant_engine.ENGINE_VERSION, "mode": mode, "idem": idempotency_key,
                 "hash": sha256(stable_json(snapshot)), "data": data["id"],
                 "parent": parent["data_version_id"] if mode == "append" else None,
                 "deployment": current["deployment_id"] if current else None,
                 "code": code_sha})).scalar_one()
        return 202, await self.get(session, tenant_id=tenant_id, training_uuid=result)

    async def accept(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     idempotency_key: str, orders_filename: str, orders_content: bytes,
                     inventory_filename: str | None, inventory_content: bytes | None,
                     allow_history_overwrite: bool):
        """Compatibility path: strict daily ISO inputs only; same quality gate.

        Posting append explicitly confirms its documented daily gross-unit
        contract. Ambiguous formats must use the interactive preflight endpoints.
        """
        if inventory_content is not None:
            inventory = await self.data.upload(session, tenant_id=tenant_id, user_id=user_id,
                                              filename=inventory_filename or "inventory.xlsx",
                                              content=inventory_content)
            checked = await self.data.preflight(
                session, tenant_id=tenant_id, version_uuid=inventory["version_uuid"],
                rules=ImportRules(**{**inventory["rules"], "kind": "inventory"}))
            await self.data.confirm(session, tenant_id=tenant_id,
                                    version_uuid=inventory["version_uuid"],
                                    preview_sha256=checked["preview_sha256"])
            # Inventory snapshots are retained separately and never treated as unit-sales labels.
        upload = await self.data.upload(session, tenant_id=tenant_id, user_id=user_id,
                                        filename=orders_filename, content=orders_content)
        if upload["rules"]["kind"] != "sales":
            raise BusinessError("FORECAST_APPEND_ORDERS_REQUIRED", "订单文件不能是库存表", status_code=422)
        checked = await self.data.preflight(session, tenant_id=tenant_id,
                                           version_uuid=upload["version_uuid"],
                                           rules=ImportRules(**upload["rules"]))
        await self.data.confirm(session, tenant_id=tenant_id, version_uuid=upload["version_uuid"],
                                preview_sha256=checked["preview_sha256"])
        existing = (await session.execute(text(SELECT_RUN + """
            WHERE tenant_id=:tenant AND idempotency_key=:idem
        """), {"tenant": tenant_id, "idem": idempotency_key})).mappings().one_or_none()
        current = await ForecastRepository().active_deployment(session, tenant_id=tenant_id)
        mode = (existing["update_kind"] if existing else
                "append" if current and current["model_scope"] == "tenant_private" else "initial")
        return await self.create(session, tenant_id=tenant_id, user_id=user_id,
                                 version_uuid=upload["version_uuid"], mode=mode,
                                 allow_history_overwrite=allow_history_overwrite,
                                 idempotency_key=idempotency_key)

    @staticmethod
    def projection(row: Any) -> dict:
        data = dict(row)
        snapshot = data.pop("source_snapshot", {}) or {}
        data["orders_filename"] = (snapshot.get("orders") or {}).get("filename")
        data["inventory_filename"] = None
        data["data_versions"] = snapshot.get("data_versions", [])
        return data

    async def list(self, session: AsyncSession, *, tenant_id: int, limit: int = 20):
        rows = (await session.execute(text(SELECT_RUN + """
            WHERE tenant_id=:tenant ORDER BY created_at DESC LIMIT :limit
        """), {"tenant": tenant_id, "limit": limit})).mappings().all()
        return [self.projection(row) for row in rows]

    async def get(self, session: AsyncSession, *, tenant_id: int, training_uuid: str):
        row = (await session.execute(text(SELECT_RUN + """
            WHERE tenant_id=:tenant AND training_uuid=CAST(:uuid AS uuid)
        """), {"tenant": tenant_id, "uuid": training_uuid})).mappings().one_or_none()
        return self.projection(row) if row else None

    async def execute(self, session: AsyncSession, *, tenant_id: int, training_uuid: str,
                      execution_token: str | None = None):
        token = execution_token or str(uuid4())
        row = (await session.execute(text("""
            UPDATE forecast_training_runs
               SET status='running',started_at=now(),completed_at=NULL,
                   error_code=NULL,error_message=NULL,execution_token=CAST(:token AS uuid),
                   execution_attempts=execution_attempts+1,heartbeat_at=clock_timestamp(),
                   lease_expires_at=clock_timestamp()+:seconds*interval '1 second'
             WHERE tenant_id=:tenant AND training_uuid=CAST(:uuid AS uuid)
               AND status='queued' AND execution_attempts<:maximum
             RETURNING *
        """), {"tenant": tenant_id, "uuid": training_uuid, "token": token,
                 "seconds": self.settings.worker_lease_ms / 1000,
                 "maximum": self.settings.worker_max_attempts})).mappings().one_or_none()
        await session.commit()
        if row is None:
            return
        work = asyncio.create_task(self._execute_owned(
            session, tenant_id=tenant_id, training_uuid=training_uuid, row=row, token=token))
        pulse = asyncio.create_task(self.lifecycle.heartbeat(
            session.bind, tenant_id=tenant_id, training_uuid=training_uuid, token=token))
        tasks = (work, pulse)
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED,
                                         timeout=self.settings.worker_job_timeout_seconds)
            if not done:
                raise TimeoutError("训练执行超时")
            await (work if work in done else pulse)
        except (Exception, asyncio.CancelledError) as exc:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await session.rollback()
            interrupted = isinstance(exc, asyncio.CancelledError)
            await session.execute(text("""
                UPDATE forecast_training_runs
                   SET status=CASE WHEN :interrupted AND execution_attempts<:maximum
                                   THEN 'queued' ELSE 'failed' END,
                       error_code=:code,error_message=:message,lease_expires_at=NULL,
                       completed_at=CASE WHEN :interrupted AND execution_attempts<:maximum
                                         THEN NULL ELSE now() END
                 WHERE tenant_id=:tenant AND training_uuid=CAST(:uuid AS uuid)
                   AND execution_token=CAST(:token AS uuid) AND status='running'
            """), {"tenant": tenant_id, "uuid": training_uuid, "token": token,
                     "maximum": self.settings.worker_max_attempts, "interrupted": interrupted,
                     "code": "FORECAST_INTERRUPTED" if interrupted else "FORECAST_TRAINING_FAILED",
                     "message": "训练中断，将恢复执行" if interrupted else
                                "训练失败；当前模型保持不变，请检查数据版本或联系管理员"})
            await session.commit()
            raise
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute_owned(self, session, *, tenant_id, training_uuid, row, token):
        stage = final = None
        published = False
        artifact_name = f"{training_uuid}-{token}"
        try:
            code = Path(tenant_engine.__file__).read_bytes()
            if sha256(code) != row["code_sha256"]:
                raise ValueError("排队期间训练代码已变化，请创建新训练任务")
            merged = {}
            for source in row["source_snapshot"]["sources"]:
                data = await self.data.row(session, tenant_id, source["uuid"])
                if data["canonical_sha256"] != source["sha256"]:
                    raise ValueError("训练数据版本校验失败")
                for record in self.data.records(tenant_id, data):
                    merged[(record["date"], record["sku"], record["site"])] = record
            await session.commit()  # Do not hold a database transaction through CPU training.
            tenant_root = Path(self.settings.forecast_artifact_root).resolve() / str(tenant_id)
            tenant_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            stage, final = tenant_root / f".staging-{artifact_name}", tenant_root / artifact_name
            if stage.exists() or final.exists():
                raise ValueError("训练制品目录已存在，请创建新任务")
            stage.mkdir(mode=0o700)
            (stage / "forecast.py").write_bytes(code)
            lineage = {"tenant_id": tenant_id, "training_uuid": training_uuid, "execution_token": token,
                       "sources": row["source_snapshot"]["sources"], "code_sha256": row["code_sha256"]}
            evaluation = await self.lifecycle.train(list(merged.values()), stage, lineage)
            if not evaluation["passed"]:
                await self.lifecycle.fence(session, tenant_id=tenant_id,
                                            training_uuid=training_uuid, token=token)
                await session.execute(text("""
                    UPDATE forecast_training_runs SET status='rejected',metrics=CAST(:metrics AS jsonb),
                      error_code='FORECAST_QUALITY_GATE',error_message=:message,completed_at=now(),
                      lease_expires_at=NULL
                     WHERE id=:id AND tenant_id=:tenant
                """), {"id": row["id"], "tenant": tenant_id, "metrics": json.dumps(evaluation),
                         "message": "；".join(evaluation["reasons"])})
                await session.commit()
                return
            runtime = ForecastRuntime(self.settings, state_dir=stage,
                                      model_version=f"{tenant_engine.ENGINE_VERSION}-{training_uuid[:8]}")
            metadata = runtime.metadata()
            if not metadata.get("ready"):
                raise ValueError("训练制品未通过完整性校验")
            sku_rows = await runtime.list_skus(None, 100_000)
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                                  {"key": 7_040_000 + tenant_id})
            await self.lifecycle.fence(session, tenant_id=tenant_id,
                                        training_uuid=training_uuid, token=token)
            current = await ForecastRepository().active_deployment(session, tenant_id=tenant_id)
            if (current["deployment_id"] if current else None) != row["baseline_deployment_id"]:
                raise ValueError("训练期间部署已变化，新模型未发布，请重新训练")
            frozen = row["source_snapshot"].get("catalog", {}).get("items")
            if not frozen:
                raise ValueError("旧训练任务缺少SKU映射快照，请重新创建任务")
            catalog = ForecastCatalog.require_valid(await ForecastCatalog().preview(
                session, tenant_id, sku_rows, frozen=frozen))
            stage.rename(final)
            stage = None
            checksum = metadata["state_checksum"]
            model_metrics = {"evaluation": evaluation, "lineage": lineage,
                             "checksum_algorithm": "sha256-content-v1", "catalog_snapshot": catalog}
            model_id = (await session.execute(text("""
                INSERT INTO forecast_models
                  (model_code,owner_tenant_id,model_scope,version,engine,state_uri,state_checksum,
                   status,training_data_through,metrics)
                VALUES('sales_forecast',:tenant,'tenant_private',:version,:engine,:uri,:checksum,
                       'active',:through,CAST(:metrics AS jsonb)) RETURNING id
            """), {"tenant": tenant_id, "version": runtime.model_version, "engine": metadata["engine"],
                     "uri": f"server-managed://tenant/{tenant_id}/{artifact_name}", "checksum": checksum,
                     "through": coerce_training_date(metadata["data_through"]),
                     "metrics": json.dumps(model_metrics)})).scalar_one()
            deployed = await ForecastPublication().activate(
                session, tenant_id=tenant_id, model_id=model_id, user_id=row["created_by"],
                catalog=catalog, expected_deployment_id=row["baseline_deployment_id"],
                source="standard_training")
            await session.execute(text("""
                UPDATE forecast_training_runs SET status='succeeded',metrics=CAST(:metrics AS jsonb),
                       artifact_model_id=:model,completed_at=now(),lease_expires_at=NULL
                 WHERE id=:id AND tenant_id=:tenant
            """), {"id": row["id"], "tenant": tenant_id, "model": model_id,
                     "metrics": json.dumps({**model_metrics, "model_version": runtime.model_version,
                                            "state_checksum": checksum,
                                            "catalog_pairs": deployed["catalog_pairs"],
                                            "catalog_skus": deployed["catalog_skus"]})})
            await session.commit()
            published = True
        finally:
            if stage and stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            if final and final.exists() and not published:
                # A lost connection can make COMMIT's outcome unknown. Never
                # delete artifacts until the DB proves they are unreferenced.
                try:
                    await session.rollback()
                    referenced = await session.scalar(text("""
                        SELECT EXISTS(SELECT FROM forecast_models
                         WHERE owner_tenant_id=:tenant AND state_uri=:uri)
                    """), {"tenant": tenant_id, "uri": f"server-managed://tenant/{tenant_id}/{artifact_name}"})
                except Exception:
                    referenced = True  # Retain for reconciliation when DB returns.
                if not referenced:
                    shutil.rmtree(final, ignore_errors=True)
