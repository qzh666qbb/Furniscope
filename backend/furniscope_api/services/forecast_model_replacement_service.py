"""Validated tenant-specific forecast engine replacement and atomic deployment."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.forecast_repository import ForecastRepository
from .forecast_runtime import ForecastRuntime, TenantForecastRuntimeRegistry, coerce_training_date


class ForecastModelReplacementService:
    def __init__(self, settings: ApiSettings,
                 registry: TenantForecastRuntimeRegistry) -> None:
        self.settings = settings
        self.registry = registry

    def _validate(self, *, code_filename: str, code_content: bytes,
                  parameters_filename: str, parameters_content: bytes) -> dict[str, Any]:
        if Path(code_filename).suffix.lower() != ".py" or not code_content:
            raise BusinessError("FORECAST_MODEL_CODE_INVALID",
                                "模型代码必须是非空的 .py 文件", status_code=422)
        if len(code_content) > self.settings.upload_max_bytes:
            raise BusinessError("FILE_SIZE_EXCEEDED", "模型代码超过大小限制", status_code=413)
        try:
            source = code_content.decode("utf-8")
            compile(source, "forecast.py", "exec")
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise BusinessError("FORECAST_MODEL_CODE_INVALID",
                                "模型代码不是有效的 UTF-8 Python 文件", status_code=422) from exc
        if "class ForecastService" not in source:
            raise BusinessError("FORECAST_MODEL_INTERFACE_INVALID",
                                "模型代码必须实现 ForecastService", status_code=422)
        if Path(parameters_filename).suffix.lower() != ".json" or not parameters_content:
            raise BusinessError("FORECAST_MODEL_PARAMS_INVALID",
                                "模型参数必须是非空的 .json 文件", status_code=422)
        if len(parameters_content) > min(self.settings.upload_max_bytes, 1024 * 1024):
            raise BusinessError("FILE_SIZE_EXCEEDED", "模型参数文件不能超过 1 MB", status_code=413)
        try:
            parameters = json.loads(parameters_content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BusinessError("FORECAST_MODEL_PARAMS_INVALID",
                                "模型参数不是有效的 JSON", status_code=422) from exc
        if not isinstance(parameters, dict):
            raise BusinessError("FORECAST_MODEL_PARAMS_INVALID",
                                "模型参数顶层必须是 JSON 对象", status_code=422)
        return parameters

    async def replace(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                      version: str, algorithm: str, change_note: str,
                      code_filename: str, code_content: bytes,
                      parameters_filename: str, parameters_content: bytes) -> dict[str, Any]:
        parameters = self._validate(
            code_filename=code_filename, code_content=code_content,
            parameters_filename=parameters_filename, parameters_content=parameters_content)
        current = (await session.execute(text("""SELECT d.id AS deployment_id,
            d.deployment_uuid::text,m.state_uri,m.version,m.engine
            FROM forecast_model_deployments d JOIN forecast_models m ON m.id=d.model_id
            WHERE d.tenant_id=:tenant AND d.scenario_code='sales_forecast'
              AND d.status='active' AND m.status='active' FOR UPDATE OF d"""),
            {"tenant": tenant_id})).mappings().one_or_none()
        if current is None:
            raise BusinessError("FORECAST_DEPLOYMENT_NOT_FOUND",
                                "当前租户尚未部署可替换的预测模型", status_code=409)
        effective_algorithm = current["engine"] if algorithm == "current" else algorithm

        replacement_uuid = str(uuid4())
        artifact_root = Path(self.settings.forecast_artifact_root).resolve()
        tenant_root = artifact_root / str(tenant_id)
        tenant_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage = tenant_root / f".replacement-{replacement_uuid}"
        final = tenant_root / replacement_uuid
        published = False
        try:
            source = self.registry.state_dir(tenant_id=tenant_id, state_uri=current["state_uri"])
            shutil.copytree(source, stage)
            (stage / "forecast.py").write_bytes(code_content)
            (stage / "forecast.py").chmod(0o600)
            (stage / "model_params.json").write_text(
                json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")
            (stage / "model_manifest.json").write_text(json.dumps({
                "algorithm": effective_algorithm, "version": version,
                "change_note": change_note, "source_filename": Path(code_filename).name,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            runtime = ForecastRuntime(self.settings, state_dir=stage, model_version=version)
            await runtime.retrain()
            metadata = runtime.metadata()
            if not metadata.get("ready"):
                raise RuntimeError(metadata.get("error", "新模型未通过就绪校验"))
            sku_rows = await runtime.list_skus(None, 5000)
            catalog_skus, catalog_pairs = await ForecastRepository().replace_catalog_from_engine(
                session, tenant_id=tenant_id, sku_rows=sku_rows)
            stage.rename(final)
            state_uri = f"server-managed://tenant/{tenant_id}/{replacement_uuid}"
            model = (await session.execute(text("""INSERT INTO forecast_models
                (model_code,owner_tenant_id,model_scope,version,engine,state_uri,state_checksum,
                 status,training_data_through,metrics)
                VALUES('sales_forecast',:tenant,'tenant_private',:version,:engine,:uri,:checksum,
                       'active',CAST(:through AS date),CAST(:metrics AS jsonb))
                RETURNING id,model_uuid::text"""), {
                "tenant": tenant_id, "version": version, "engine": effective_algorithm,
                "uri": state_uri, "checksum": metadata["state_checksum"],
                "through": coerce_training_date(metadata.get("data_through")),
                "metrics": json.dumps({"sku_count": metadata.get("sku_count"),
                                       "replacement": {"parameters": parameters,
                                                       "change_note": change_note}}),
            })).mappings().one()
            await session.execute(text("""UPDATE forecast_model_deployments
                SET status='inactive',retired_at=now()
                WHERE tenant_id=:tenant AND scenario_code='sales_forecast' AND status='active'"""),
                {"tenant": tenant_id})
            deployment = (await session.execute(text("""INSERT INTO forecast_model_deployments
                (tenant_id,model_id,scenario_code,status,route_policy,deployed_by)
                VALUES(:tenant,:model,'sales_forecast','active',CAST(:policy AS jsonb),:user)
                RETURNING deployment_uuid::text,deployed_at"""), {
                "tenant": tenant_id, "model": model["id"], "user": user_id,
                "policy": json.dumps({"source": "admin_model_replacement",
                                      "algorithm": effective_algorithm,
                                      "previous_version": current["version"]}),
            })).mappings().one()
            verified = ForecastRuntime(self.settings, state_dir=final, model_version=version)
            self.registry.remember_verified(tenant_id=tenant_id, deployment={
                "model_scope": "tenant_private", "state_uri": state_uri,
                "version": version, "state_checksum": metadata["state_checksum"],
            }, runtime=verified)
            published = True
            return {"model_id": model["id"], "model_uuid": model["model_uuid"],
                    "deployment_uuid": deployment["deployment_uuid"], "version": version,
                    "engine": effective_algorithm, "deployed_at": deployment["deployed_at"],
                    "catalog_pairs": catalog_pairs, "catalog_skus": catalog_skus}
        except BusinessError:
            raise
        except Exception as exc:
            raise BusinessError("FORECAST_MODEL_REPLACEMENT_FAILED",
                                f"新模型校验或训练失败：{str(exc)[:500]}", status_code=422) from exc
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
            if final.exists() and not published:
                shutil.rmtree(final, ignore_errors=True)
