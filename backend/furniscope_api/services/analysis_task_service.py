"""Application services for analysis task creation, dispatch and projections."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.analysis_task_repository import AnalysisTaskRepository
from .idempotency_service import IdempotencyService


class AnalysisTaskService:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.repository = AnalysisTaskRepository()
        self.idempotency = IdempotencyService()

    def _versions(self) -> dict[str, str]:
        return {"ontology_version": self.settings.analysis_ontology_version,
                "scoring_version": self.settings.analysis_scoring_version,
                "prompt_bundle_version": self.settings.analysis_prompt_bundle_version,
                "model_route_version": self.settings.analysis_model_route_version}

    async def _validate_scope(self, session: AsyncSession, *, tenant_id: int,
                              payload: dict[str, Any]) -> None:
        scope = await self.repository.input_scope(
            session, tenant_id=tenant_id, product_id=payload["product_id"],
            profile_version_id=payload["product_profile_version_id"], dataset_id=payload["dataset_id"])
        if not scope:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        if scope.get("profile_version_id") is None or scope["profile_status"] != "confirmed":
            raise BusinessError("PRODUCT_PROFILE_NOT_CONFIRMED", "产品画像不存在或未确认", status_code=422)
        if scope.get("dataset_id") is None:
            raise BusinessError("DATASET_NOT_FOUND", "数据集不存在或不可访问", status_code=404)
        if scope["dataset_status"] != "ready":
            raise BusinessError("DATASET_NOT_READY", "数据集尚未就绪", status_code=422)
        if (scope["platform"] != payload["target_platform"]
                or scope["market_country"].strip() != payload["target_country"]
                or scope["category_code"] != scope["dataset_category_code"]):
            raise BusinessError("TASK_SCOPE_MISMATCH", "产品、数据集与目标市场范围不一致", status_code=422)

    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     idempotency_key: str, payload: dict[str, Any],
                     response_envelope) -> tuple[int, dict[str, Any]]:
        decision = await self.idempotency.begin(
            session, tenant_id=tenant_id, actor_user_id=user_id, route_code="API-INS-01",
            http_method="POST", idempotency_key=idempotency_key, request_payload=payload)
        if decision.action == "replay":
            return int(decision.response_status), dict(decision.response_body or {})
        await self._validate_scope(session, tenant_id=tenant_id, payload=payload)
        row = await self.repository.create(session, tenant_id=tenant_id, user_id=user_id,
                                           idempotency_key=idempotency_key, payload=payload,
                                           versions=self._versions())
        row["report_uuid"] = None
        envelope = response_envelope(row)
        await self.idempotency.finish(
            session, tenant_id=tenant_id, record_id=decision.record_id,
            response_status=201, response_body=envelope, resource_type="analysis_tasks",
            resource_public_id=row["task_uuid"])
        return 201, envelope

    async def start(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                    task_uuid: str, idempotency_key: str,
                    response_envelope) -> tuple[int, dict[str, Any], dict[str, Any] | None]:
        decision = await self.idempotency.begin(
            session, tenant_id=tenant_id, actor_user_id=user_id, route_code="API-INS-02",
            http_method="POST", idempotency_key=idempotency_key,
            request_payload={"task_uuid": task_uuid})
        if decision.action == "replay":
            return int(decision.response_status), dict(decision.response_body or {}), None
        task = await self.repository.task_for_update(session, tenant_id=tenant_id, task_uuid=task_uuid)
        if task is None:
            raise BusinessError("TASK_NOT_FOUND", "任务不存在或不可访问", status_code=404)
        if task["status"] != "draft":
            raise BusinessError("TASK_ALREADY_STARTED", "任务已经启动", status_code=409)
        await self._validate_scope(session, tenant_id=tenant_id, payload={
            "product_id": task["product_id"],
            "product_profile_version_id": task["product_profile_version_id"],
            "dataset_id": task["dataset_id"], "target_country": task["target_country"].strip(),
            "target_platform": task["target_platform"]})
        if self.settings.analysis_worker_mode != "demo_only":
            raise BusinessError("TASK_PREFLIGHT_FAILED", "分析Worker尚未配置", status_code=422)
        row = await self.repository.queue(session, tenant_id=tenant_id, task_id=task["id"])
        row["retryable"] = False
        envelope = response_envelope(row)
        await self.idempotency.finish(
            session, tenant_id=tenant_id, record_id=decision.record_id,
            response_status=202, response_body=envelope, resource_type="analysis_tasks",
            resource_public_id=task_uuid)
        return 202, envelope, {"tenant_id": tenant_id, "task_id": task["id"],
                               "task_uuid": task_uuid}

    async def status(self, session: AsyncSession, *, tenant_id: int, task_uuid: str,
                     include_stage_runs: bool, stage_run_limit: int,
                     admin: bool) -> dict[str, Any]:
        task = await self.repository.status(session, tenant_id=tenant_id, task_uuid=task_uuid)
        if task is None:
            raise BusinessError("TASK_NOT_FOUND", "任务不存在或不可访问", status_code=404)
        if task["stage"] not in {"understanding_product", "researching_market",
                                  "evaluating_opportunity", "generating_recommendation", "completed"}:
            raise BusinessError("WORKFLOW_STATE_INCONSISTENT", "任务展示阶段不一致", status_code=409)
        task_id = task.pop("id")
        task["stage_runs"] = (await self.repository.stage_runs(
            session, tenant_id=tenant_id, task_id=task_id, limit=stage_run_limit, admin=admin)
            if include_stage_runs else [])
        task["partial_failures"] = await self.repository.partial_failures(
            session, tenant_id=tenant_id, task_id=task_id)
        task["user_confirmation"] = await self.repository.pending_confirmation(
            session, tenant_id=tenant_id, task_id=task_id)
        task["retryable"] = bool(task.pop("retryable_failure") or
            (task.pop("safe_checkpoint") and task["status"] in
             {"waiting_human", "failed", "partial_succeeded"}))
        return task

    async def result(self, session: AsyncSession, *, tenant_id: int, task_uuid: str,
                     opportunity_limit: int, recommendation_limit: int) -> dict[str, Any]:
        task = await self.repository.status(session, tenant_id=tenant_id, task_uuid=task_uuid)
        if task is None:
            raise BusinessError("TASK_NOT_FOUND", "任务不存在或不可访问", status_code=404)
        if task["status"] != "succeeded" or not task["report_uuid"]:
            raise BusinessError("TASK_RESULT_NOT_READY", "任务结果尚未完成", status_code=409)
        data = await self.repository.aggregate_result(
            session, tenant_id=tenant_id, task_id=task["id"],
            opportunity_limit=opportunity_limit, recommendation_limit=recommendation_limit)
        if data is None or not data.get("report_uuid"):
            raise BusinessError("TASK_RESULT_INCOMPLETE", "任务结果证据或报告不完整", status_code=422)
        snapshot = data.get("data_scope_snapshot") or {}
        return {
            "task_uuid": data["task_uuid"], "report_uuid": data["report_uuid"],
            "report_summary": {"title": data["title"], "executive_summary": data["executive_summary"],
                "decision_recommendation": data["decision_recommendation"],
                "overall_opportunity_score": data["overall_opportunity_score"],
                "overall_confidence": data["overall_confidence"]},
            "data_scope": {"target_country": data["target_country"].strip(),
                "target_platform": data["target_platform"], "data_start_date": data["data_start_date"],
                "data_end_date": data["data_end_date"], "listing_count": data["listing_count"],
                "valid_review_count": data["valid_review_count"],
                "limitations": snapshot.get("limitations", data["limitations"])},
            "competitor_summary": data["competitor_summary"],
            "insight_clusters": data["insight_clusters"], "opportunities": data["opportunities"],
            "recommendations": data["recommendations"],
            "partial_failures": await self.repository.partial_failures(
                session, tenant_id=tenant_id, task_id=task["id"])}
