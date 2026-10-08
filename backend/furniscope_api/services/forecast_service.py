"""Forecast job orchestration and deterministic production projections."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.forecast_repository import ForecastRepository
from .forecast_runtime import TenantForecastRuntimeRegistry
from .idempotency_service import IdempotencyService, canonical_request_hash


class ForecastService:
    def __init__(self, settings: ApiSettings, runtime: TenantForecastRuntimeRegistry) -> None:
        self.settings = settings
        self.runtime = runtime
        self.repository = ForecastRepository()
        self.idempotency = IdempotencyService()

    @staticmethod
    def projection(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row.get(key) for key in (
            "job_uuid", "job_name", "status", "granularity", "horizon", "skus", "sites",
            "progress_percent", "model_version", "failure_code", "failure_message",
            "created_at", "started_at", "completed_at",
        )}

    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     idempotency_key: str, payload: dict[str, Any], response_envelope):
        decision = await self.idempotency.begin(
            session, tenant_id=tenant_id, actor_user_id=user_id, route_code="API-FRC-02",
            http_method="POST", idempotency_key=idempotency_key, request_payload=payload)
        if decision.action == "replay":
            return int(decision.response_status), dict(decision.response_body or {})
        pair_count = len(payload["skus"]) * len(payload["sites"])
        if pair_count > self.settings.forecast_max_pairs:
            raise BusinessError("FORECAST_SCOPE_TOO_LARGE", "单次预测组合数量超过限制", status_code=422,
                                details=[{"pair_count": pair_count,
                                          "maximum": self.settings.forecast_max_pairs}])
        sku_scope = await self.repository.validate_sku_scope(
            session, tenant_id=tenant_id, skus=payload["skus"], sites=payload["sites"])
        missing = [{"sku": row["sku"], "site": row["site"]} for row in sku_scope
                   if row["lifecycle_status"] is None]
        unavailable = [{"sku": row["sku"], "site": row["site"],
                        "lifecycle_status": row["lifecycle_status"]} for row in sku_scope
                       if row["lifecycle_status"] not in {None, "active"}]
        if missing or unavailable:
            raise BusinessError(
                "FORECAST_SKU_SCOPE_INVALID", "SKU 不属于当前租户或当前不可预测", status_code=422,
                details=[{"missing": missing, "unavailable": unavailable}],
            )
        if payload.get("product_id") is not None:
            mismatched = [{"sku": row["sku"], "site": row["site"]} for row in sku_scope
                          if row["product_id"] != payload["product_id"]]
            if mismatched:
                raise BusinessError(
                    "FORECAST_PRODUCT_SKU_MISMATCH", "预测 SKU 与所选产品不一致",
                    status_code=422, details=mismatched,
                )
        scenario = payload.get("scenario", {})
        cold = [{"sku": row["sku"], "site": row["site"],
                 "history_weeks": row["history_weeks"]} for row in sku_scope
                if not row["model_eligible"]]
        if cold and scenario.get("baseline") is None and not scenario.get("reference_sku"):
            eligible = await self.repository.eligible_sites_by_sku(
                session, tenant_id=tenant_id, skus=payload["skus"])
            remapped: set[str] | None = None
            unresolved = []
            for sku in payload["skus"]:
                available = eligible.get(sku.upper(), set())
                if not available:
                    unresolved.append(sku)
                    continue
                overlap = {site for site in payload["sites"] if site in available}
                picked = overlap or set(available)
                remapped = picked if remapped is None else (
                    remapped & picked if remapped & picked else remapped | picked)
            if unresolved or not remapped:
                raise BusinessError(
                    "FORECAST_COLD_START_INPUT_REQUIRED",
                    "冷启动 SKU 需要 baseline 或 reference_sku，不能伪造历史数据",
                    status_code=422, details=cold,
                )
            payload["sites"] = sorted(remapped)
            sku_scope = await self.repository.validate_sku_scope(
                session, tenant_id=tenant_id, skus=payload["skus"], sites=payload["sites"])
            cold = [{"sku": row["sku"], "site": row["site"],
                     "history_weeks": row["history_weeks"]} for row in sku_scope
                    if not row["model_eligible"]]
            if cold:
                raise BusinessError(
                    "FORECAST_COLD_START_INPUT_REQUIRED",
                    "冷启动 SKU 需要 baseline 或 reference_sku，不能伪造历史数据",
                    status_code=422, details=cold,
                )
        scope = await self.repository.resolve_scope(
            session, tenant_id=tenant_id, product_id=payload.get("product_id"),
            analysis_task_uuid=payload.get("analysis_task_uuid"))
        if scope is None:
            raise BusinessError("FORECAST_SCOPE_NOT_FOUND", "产品或分析任务不存在或不可访问", status_code=404)
        storage_payload = {key: value for key, value in payload.items() if key != "scenario"}
        storage_payload["scenario_config"] = payload["scenario"]
        row = await self.repository.create(
            session, tenant_id=tenant_id, user_id=user_id, idempotency_key=idempotency_key,
            payload=storage_payload, input_hash=canonical_request_hash(storage_payload), scope=scope)
        envelope = response_envelope(self.projection(row))
        await self.idempotency.finish(
            session, tenant_id=tenant_id, record_id=decision.record_id, response_status=201,
            response_body=envelope, resource_type="forecast_jobs", resource_public_id=row["job_uuid"])
        return 201, envelope

    async def start(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                    job_uuid: str, idempotency_key: str, response_envelope):
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                              {"key": 7_040_000 + tenant_id})
        decision = await self.idempotency.begin(
            session, tenant_id=tenant_id, actor_user_id=user_id, route_code="API-FRC-03",
            http_method="POST", idempotency_key=idempotency_key,
            request_payload={"job_uuid": job_uuid})
        if decision.action == "replay":
            return int(decision.response_status), dict(decision.response_body or {}), None
        deployment = await self.repository.active_deployment(session, tenant_id=tenant_id)
        if deployment is None:
            raise BusinessError("FORECAST_DEPLOYMENT_NOT_FOUND", "当前租户尚未部署预测模型", status_code=503)
        runtime = self.runtime.resolve(tenant_id=tenant_id, deployment=deployment)
        metadata = runtime.metadata()
        if not metadata.get("ready"):
            raise BusinessError("FORECAST_MODEL_NOT_READY", "销量预测模型尚未就绪", status_code=503,
                                details=[{"reason": metadata.get("error", "not initialized")}])
        live_checksum = metadata.get("state_checksum")
        if live_checksum and live_checksum != deployment["state_checksum"]:
            raise BusinessError("FORECAST_CHECKSUM_MISMATCH",
                                "模型制品校验失败，请重新训练或由管理员核验旧制品", status_code=409)
        job = await self.repository.get(session, tenant_id=tenant_id, job_uuid=job_uuid, for_update=True)
        if job is None:
            raise BusinessError("FORECAST_JOB_NOT_FOUND", "预测任务不存在或不可访问", status_code=404)
        if job["status"] != "draft":
            raise BusinessError("FORECAST_JOB_ALREADY_STARTED", "预测任务已经启动", status_code=409)
        scope = await self.repository.validate_sku_scope(
            session, tenant_id=tenant_id, skus=job["skus"], sites=job["sites"])
        scenario = job["scenario_config"] or {}
        if any(row["lifecycle_status"] != "active" for row in scope):
            raise BusinessError("FORECAST_SKU_SCOPE_INVALID", "产品已变化，请重新选择预测范围", status_code=422)
        if any(not row["model_eligible"] for row in scope) and (
                scenario.get("baseline") is None and not scenario.get("reference_sku")):
            raise BusinessError("FORECAST_COLD_START_INPUT_REQUIRED",
                                "模型覆盖范围已变化，请提供新品基线或参考产品", status_code=422)
        reference = None
        if scenario.get("reference_sku"):
            references = await self.repository.validate_sku_scope(
                session, tenant_id=tenant_id, skus=[scenario["reference_sku"]], sites=job["sites"])
            if any(not row["model_eligible"] or row["lifecycle_status"] != "active" for row in references):
                raise BusinessError("FORECAST_REFERENCE_INVALID", "参考SKU必须是本企业有模型历史的产品",
                                    status_code=422)
            reference = references[0].get("source_sku") or references[0]["sku"]
        routing = {"items": scope, "reference_source_sku": reference}
        await self.repository.queue(session, tenant_id=tenant_id, job_id=job["id"],
                                    model_id=deployment["model_id"],
                                    deployment_id=deployment["deployment_id"], routing_snapshot=routing)
        job.update(status="queued", progress_percent=5)
        job["model_version"] = deployment["version"]
        envelope = response_envelope(self.projection(job))
        await self.idempotency.finish(
            session, tenant_id=tenant_id, record_id=decision.record_id, response_status=202,
            response_body=envelope, resource_type="forecast_jobs", resource_public_id=job_uuid)
        return 202, envelope, {"tenant_id": tenant_id, "job_uuid": job_uuid}

    async def execute(self, session: AsyncSession, *, tenant_id: int, job_uuid: str) -> None:
        job = await self.repository.get(session, tenant_id=tenant_id, job_uuid=job_uuid, for_update=True)
        if job is None or job["status"] != "queued":
            return
        run_id: int | None = None
        try:
            runtime = self.runtime.resolve(tenant_id=tenant_id, deployment=job)
            metadata = runtime.metadata()
            if not metadata.get("ready"):
                raise RuntimeError("Forecast runtime is not ready")
            live_checksum = metadata.get("state_checksum")
            if live_checksum and live_checksum != job.get("state_checksum"):
                raise RuntimeError("Forecast model content checksum mismatch")
            run_id, _ = await self.repository.begin_run(
                session, tenant_id=tenant_id, job=job)
            routing = job.get("routing_snapshot") or {}
            scope = routing.get("items")
            if not scope:
                raise RuntimeError("Legacy forecast lacks frozen SKU mapping; recreate the job")
            scenario = dict(job["scenario_config"] or {})
            if routing.get("reference_source_sku"):
                scenario["reference_sku"] = routing["reference_source_sku"]
            await session.commit()
            output = await runtime.predict({
                "skus": job["skus"], "sites": job["sites"],
                "granularity": job["granularity"], "horizon": job["horizon"],
                "start_date": job["start_date"].isoformat() if job["start_date"] else None,
                "scenario_config": scenario,
                "engine_skus": {row["sku"]: (row.get("source_sku") or row["sku"]) for row in scope},
            })
            scenario = job["scenario_config"] or {}
            output["metrics"]["routing"] = [
                {"sku": row["sku"], "site": row["site"],
                 "strategy": ("baseline_cold_start" if scenario.get("baseline") is not None
                              else "reference_sku_transfer" if scenario.get("reference_sku")
                              else "mapped_history" if row.get("source_sku")
                              else "v4"),
                 "history_weeks": row["history_weeks"],
                 "source_sku": row.get("source_sku")}
                for row in scope
            ]
            await self.repository.complete(
                session, tenant_id=tenant_id, job=job, run_id=run_id, output=output)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            await self.repository.fail(
                session, tenant_id=tenant_id, job_id=job["id"], run_id=run_id,
                code="FORECAST_EXECUTION_FAILED",
                message=f"{type(exc).__name__}: forecast execution failed"[:1000])
            await session.commit()
            raise

    async def result(self, session: AsyncSession, *, tenant_id: int,
                     job_uuid: str) -> dict[str, Any]:
        data = await self.repository.result(session, tenant_id=tenant_id, job_uuid=job_uuid)
        if data is None:
            raise BusinessError("FORECAST_JOB_NOT_FOUND", "预测任务不存在或不可访问", status_code=404)
        if data["job"]["status"] != "succeeded" or data["run"] is None:
            raise BusinessError("FORECAST_RESULT_NOT_READY", "预测结果尚未完成", status_code=409)
        metrics = dict(data["run"]["metrics"])
        summaries = metrics.pop("summaries", [])
        return {
            "job": self.projection(data["job"]), "run_uuid": data["run"]["run_uuid"],
            "model": {"version": data["run"]["version"], "engine": data["run"]["engine"],
                      "state_checksum": data["run"]["state_checksum"],
                      "training_data_through": data["run"]["training_data_through"]},
            "metrics": metrics, "summaries": summaries, "points": data["points"],
        }
