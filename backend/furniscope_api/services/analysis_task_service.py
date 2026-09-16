"""Application services for analysis task creation, dispatch and projections."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.analysis_task_repository import AnalysisTaskRepository
from .idempotency_service import IdempotencyService
from .audit_service import AuditService


class AnalysisTaskService:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.repository = AnalysisTaskRepository()
        self.idempotency = IdempotencyService()
        self.audit = AuditService()

    def _versions(self) -> dict[str, str]:
        return {"ontology_version": self.settings.analysis_ontology_version,
                "scoring_version": self.settings.analysis_scoring_version,
                "prompt_bundle_version": self.settings.analysis_prompt_bundle_version,
                "model_route_version": self.settings.analysis_model_route_version}

    @staticmethod
    def _json_value(value: Any, fallback: Any) -> Any:
        if value is None:
            return fallback
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return fallback
        return value

    async def _validate_scope(self, session: AsyncSession, *, tenant_id: int,
                              payload: dict[str, Any]) -> None:
        workspace_uuid = payload.get("analysis_config", {}).get("workspace_uuid")
        if workspace_uuid:
            try:
                UUID(str(workspace_uuid))
            except ValueError as exc:
                raise BusinessError("ANALYSIS_WORKSPACE_INVALID", "分析工作台标识无效", status_code=422) from exc
        target_node = payload.get("analysis_config", {}).get("target_node", "report")
        if target_node not in {"product", "market", "score", "plan", "report"}:
            raise BusinessError("ANALYSIS_TARGET_INVALID", "不支持的工作流运行终点", status_code=422)
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
                     response_envelope, request_id: str) -> tuple[int, dict[str, Any]]:
        decision = await self.idempotency.begin(
            session, tenant_id=tenant_id, actor_user_id=user_id, route_code="API-INS-01",
            http_method="POST", idempotency_key=idempotency_key, request_payload=payload)
        if decision.action == "replay":
            return int(decision.response_status), dict(decision.response_body or {})
        await self._validate_scope(session, tenant_id=tenant_id, payload=payload)
        config = dict(payload.get("analysis_config") or {})
        workspace_uuid = config.get("workspace_uuid")
        from ..repositories.workspace_repository import WorkspaceRepository
        workspaces = WorkspaceRepository()
        if workspace_uuid:
            try:
                UUID(str(workspace_uuid))
            except ValueError:
                workspace_uuid = None
        if not workspace_uuid:
            workspace_uuid = await workspaces.find_canonical_for_product(
                session, tenant_id=tenant_id, product_id=payload["product_id"],
                source=config.get("source") or "node_workflow_canvas",
            ) or str(uuid4())
        config["workspace_uuid"] = str(workspace_uuid)
        payload = {**payload, "analysis_config": config}
        row = await self.repository.create(session, tenant_id=tenant_id, user_id=user_id,
                                           idempotency_key=idempotency_key, payload=payload,
                                           versions=self._versions())
        await self.audit.record(
            session, tenant_id=tenant_id, actor_user_id=user_id,
            action_code="analysis_task.create", resource_type="analysis_task",
            resource_id=row["id"], request_id=request_id,
            after={"task_uuid": row["task_uuid"], "job_type": row["job_type"],
                   "status": row["status"], "target_country": payload["target_country"],
                   "target_platform": payload["target_platform"]},
        )
        row["report_uuid"] = None
        row["workspace_uuid"] = str(workspace_uuid) if workspace_uuid else None
        workspace_uuid = payload.get("analysis_config", {}).get("workspace_uuid")
        if workspace_uuid:
            from ..repositories.workspace_repository import WorkspaceRepository
            workspaces = WorkspaceRepository()
            await workspaces.upsert(session, tenant_id=tenant_id, user_id=user_id, payload={
                "workspace_uuid": workspace_uuid, "name": payload["job_name"],
                "source": payload.get("analysis_config", {}).get("source") or "node_workflow_canvas",
                "product_id": payload.get("product_id"),
            })
            await workspaces.touch_task(
                session, tenant_id=tenant_id, workspace_uuid=str(workspace_uuid),
                task_id=row["id"], product_id=payload.get("product_id"),
            )
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
        if self.settings.analysis_worker_mode not in {"demo_only", "token_plan_demo", "external"}:
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
        if not isinstance(task.get("analysis_config"), dict):
            task["analysis_config"] = {}
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
        snapshot = self._json_value(data.get("data_scope_snapshot"), {})
        limitations = self._json_value(data.get("limitations"), [])
        forecast_result = await session.execute(text("""
            SELECT j.job_uuid::text,j.granularity,j.horizon,r.metrics,m.version AS model_version
              FROM forecast_jobs j
              JOIN forecast_runs r ON r.job_id=j.id AND r.tenant_id=j.tenant_id
                                  AND r.status='succeeded'
              JOIN forecast_models m ON m.id=r.model_id
             WHERE j.tenant_id=:tenant_id AND j.analysis_task_id=:task_id
                   AND j.status='succeeded'
             ORDER BY r.completed_at DESC LIMIT 1
        """), {"tenant_id": tenant_id, "task_id": task["id"]})
        forecast = forecast_result.mappings().one_or_none()
        forecast_summary = None
        if forecast:
            metrics = self._json_value(forecast["metrics"], {})
            forecast_summary = {
                "job_uuid": forecast["job_uuid"], "granularity": forecast["granularity"],
                "horizon": forecast["horizon"], "model_version": forecast["model_version"],
                "total_forecast": metrics.get("total_forecast"),
                "safety_stock": metrics.get("safety_stock"),
                "recommended_production": metrics.get("recommended_production"),
            }
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
                "limitations": self._json_value(snapshot.get("limitations"), limitations)},
            "competitor_summary": data["competitor_summary"],
            "insight_clusters": data["insight_clusters"], "opportunities": data["opportunities"],
            "recommendations": data["recommendations"],
            "partial_failures": await self.repository.partial_failures(
                session, tenant_id=tenant_id, task_id=task["id"]),
            "forecast_summary": forecast_summary}
