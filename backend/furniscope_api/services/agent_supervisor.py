"""Durable executor for the first-party AI employee skills."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.agent_repository import AgentRepository
from ..repositories.forecast_repository import ForecastRepository
from ..schemas.data_query import DataQueryFilters, DataQueryPlan
from .analysis_agent_adapter import AnalysisAgentAdapter
from .analysis_task_service import AnalysisTaskService
from .agent_planner import tool_definition
from .data_query import DataQueryService
from .forecast_catalog import ForecastCatalog


def _sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(slots=True)
class AgentNeedsApproval(Exception):
    approval_type: str
    title: str
    question: str
    reason: str
    href: str
    impact: dict[str, Any] | None = None
    options: list[dict[str, Any]] | None = None
    recommended_option: str = "continue"


class AgentSupervisor:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.repository = AgentRepository()

    async def execute(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        run_uuid: str,
    ) -> dict[str, Any]:
        context = await self.repository.run_context(
            session,
            tenant_id=tenant_id,
            run_uuid=run_uuid,
            for_update=True,
        )
        if context is None:
            raise BusinessError("AGENT_RUN_NOT_FOUND", "执行实例不存在", status_code=404)
        if context["status"] in {
            "succeeded",
            "partial_succeeded",
            "failed",
            "cancelled",
        }:
            return self._run_projection(context)
        if context["cancel_requested"]:
            await self._finish_cancelled(session, context)
            await session.commit()
            return self._run_projection({**context, "status": "cancelled"})
        if context["pause_requested"]:
            await self.repository.set_run_state(
                session,
                tenant_id=tenant_id,
                run_id=int(context["id"]),
                goal_id=int(context["goal_id"]),
                status="paused",
                progress_percent=float(context["progress_percent"]),
                current_step_id=context.get("current_step_id"),
            )
            await self.repository.append_event(
                session,
                tenant_id=tenant_id,
                goal_id=int(context["goal_id"]),
                run_id=int(context["id"]),
                event_type="run.paused",
                payload={},
            )
            await session.commit()
            return self._run_projection({**context, "status": "paused"})

        steps = await self.repository.plan_steps(
            session,
            tenant_id=tenant_id,
            plan_id=int(context["plan_id"]),
        )
        await self.repository.set_run_state(
            session,
            tenant_id=tenant_id,
            run_id=int(context["id"]),
            goal_id=int(context["goal_id"]),
            status="running",
            progress_percent=float(context["progress_percent"]),
            current_step_id=context.get("current_step_id"),
        )
        await self.repository.append_event(
            session,
            tenant_id=tenant_id,
            goal_id=int(context["goal_id"]),
            run_id=int(context["id"]),
            event_type="run.started",
            payload={"run_uuid": run_uuid},
        )
        await session.commit()

        for step in steps:
            if step["status"] == "succeeded":
                continue
            context = await self.repository.run_context(
                session,
                tenant_id=tenant_id,
                run_uuid=run_uuid,
                for_update=True,
            )
            if context["cancel_requested"]:
                await self._finish_cancelled(session, context)
                await session.commit()
                return self._run_projection({**context, "status": "cancelled"})
            if context["pause_requested"]:
                await self.repository.set_run_state(
                    session,
                    tenant_id=tenant_id,
                    run_id=int(context["id"]),
                    goal_id=int(context["goal_id"]),
                    status="paused",
                    progress_percent=float(context["progress_percent"]),
                    current_step_id=step["id"],
                )
                await self.repository.append_event(
                    session,
                    tenant_id=tenant_id,
                    goal_id=int(context["goal_id"]),
                    run_id=int(context["id"]),
                    event_type="run.paused",
                    payload={"before_step": step["title"]},
                )
                await session.commit()
                return self._run_projection({**context, "status": "paused"})
            outputs = await self.repository.step_outputs(
                session,
                tenant_id=tenant_id,
                run_id=int(context["id"]),
            )
            input_material = {
                "goal": {
                    "objective": context["objective"],
                    "constraints": context["constraints"],
                },
                "step": {
                    "capability_id": step["capability_id"],
                    "version": step["capability_version"],
                },
                "prior_outputs": outputs,
            }
            step_run_id = await self.repository.start_step(
                session,
                tenant_id=tenant_id,
                run_id=int(context["id"]),
                step=step,
                input_sha256=_sha256(input_material),
            )
            await self.repository.set_run_state(
                session,
                tenant_id=tenant_id,
                run_id=int(context["id"]),
                goal_id=int(context["goal_id"]),
                status="running",
                progress_percent=float(context["progress_percent"]),
                current_step_id=int(step["id"]),
            )
            await self.repository.append_event(
                session,
                tenant_id=tenant_id,
                goal_id=int(context["goal_id"]),
                run_id=int(context["id"]),
                event_type="step.started",
                payload={
                    "step_uuid": str(step["step_uuid"]),
                    "title": step["title"],
                    "capability_id": step["capability_id"],
                },
            )
            await session.commit()
            started = perf_counter()
            try:
                output = await self._execute_capability(
                    session,
                    context=context,
                    step=step,
                    outputs=outputs,
                )
                duration_ms = max(0, round((perf_counter() - started) * 1000))
                output = _plain(output)
                await self.repository.complete_step(
                    session,
                    tenant_id=tenant_id,
                    run_id=int(context["id"]),
                    step=step,
                    step_run_id=step_run_id,
                    output=output,
                    output_sha256=_sha256(output),
                    duration_ms=duration_ms,
                )
                progress = round(step["ordinal"] / len(steps) * 100, 2)
                next_status = "delivering" if step["ordinal"] == len(steps) else "running"
                await self.repository.set_run_state(
                    session,
                    tenant_id=tenant_id,
                    run_id=int(context["id"]),
                    goal_id=int(context["goal_id"]),
                    status=next_status,
                    progress_percent=progress,
                    current_step_id=int(step["id"]),
                )
                await self.repository.append_event(
                    session,
                    tenant_id=tenant_id,
                    goal_id=int(context["goal_id"]),
                    run_id=int(context["id"]),
                    event_type="step.succeeded",
                    payload={
                        "step_uuid": str(step["step_uuid"]),
                        "title": step["title"],
                        "progress_percent": progress,
                        "receipt_sha256": _sha256(output),
                    },
                )
                await session.commit()
            except AgentNeedsApproval as approval:
                await self.repository.wait_step(
                    session,
                    tenant_id=tenant_id,
                    step_id=int(step["id"]),
                    step_run_id=step_run_id,
                )
                pending = await self.repository.pending_approval_for_step(
                    session,
                    tenant_id=tenant_id,
                    run_id=int(context["id"]),
                    step_id=int(step["id"]),
                )
                if pending is None:
                    approval_uuid = await self.repository.create_approval(
                        session,
                        tenant_id=tenant_id,
                        goal_id=int(context["goal_id"]),
                        run_id=int(context["id"]),
                        step_id=int(step["id"]),
                        approval_type=approval.approval_type,
                        title=approval.title,
                        question=approval.question,
                        reason=approval.reason,
                        href=approval.href,
                        impact=approval.impact,
                        options=approval.options,
                        recommended_option=approval.recommended_option,
                    )
                else:
                    approval_uuid = pending["approval_uuid"]
                await self.repository.set_run_state(
                    session,
                    tenant_id=tenant_id,
                    run_id=int(context["id"]),
                    goal_id=int(context["goal_id"]),
                    status="waiting_human",
                    progress_percent=float(context["progress_percent"]),
                    current_step_id=int(step["id"]),
                )
                await self.repository.append_event(
                    session,
                    tenant_id=tenant_id,
                    goal_id=int(context["goal_id"]),
                    run_id=int(context["id"]),
                    event_type="approval.required",
                    payload={
                        "approval_uuid": approval_uuid,
                        "title": approval.title,
                        "href": approval.href,
                    },
                )
                await session.commit()
                return {
                    **self._run_projection(context),
                    "status": "waiting_human",
                    "approval_uuid": approval_uuid,
                }
            except Exception as exc:
                code = (
                    exc.code
                    if isinstance(exc, BusinessError)
                    else "AGENT_STEP_EXECUTION_FAILED"
                )
                message = (
                    exc.message
                    if isinstance(exc, BusinessError)
                    else f"{type(exc).__name__}: AI 员工步骤执行失败"
                )
                await self.repository.fail_step(
                    session,
                    tenant_id=tenant_id,
                    step_id=int(step["id"]),
                    step_run_id=step_run_id,
                    error_code=code,
                    error_message=message,
                )
                await self.repository.set_run_state(
                    session,
                    tenant_id=tenant_id,
                    run_id=int(context["id"]),
                    goal_id=int(context["goal_id"]),
                    status="failed",
                    progress_percent=float(context["progress_percent"]),
                    current_step_id=int(step["id"]),
                    failure_code=code,
                    failure_message=message,
                )
                await self.repository.append_event(
                    session,
                    tenant_id=tenant_id,
                    goal_id=int(context["goal_id"]),
                    run_id=int(context["id"]),
                    event_type="run.failed",
                    payload={"code": code, "message": message, "step": step["title"]},
                )
                await self.repository.append_message(
                    session,
                    tenant_id=tenant_id,
                    goal_id=int(context["goal_id"]),
                    role="employee",
                    content=f"执行在“{step['title']}”停止：{message}",
                    created_by=None,
                )
                await session.commit()
                return {
                    **self._run_projection(context),
                    "status": "failed",
                    "failure_code": code,
                    "failure_message": message,
                }

        await self.repository.set_run_state(
            session,
            tenant_id=tenant_id,
            run_id=int(context["id"]),
            goal_id=int(context["goal_id"]),
            status="succeeded",
            progress_percent=100,
            current_step_id=int(steps[-1]["id"]) if steps else None,
        )
        await self.repository.append_event(
            session,
            tenant_id=tenant_id,
            goal_id=int(context["goal_id"]),
            run_id=int(context["id"]),
            event_type="run.succeeded",
            payload={"progress_percent": 100},
        )
        await self.repository.append_message(
            session,
            tenant_id=tenant_id,
            goal_id=int(context["goal_id"]),
            role="employee",
            content="目标已完成并通过确定性检查，交付物已登记。",
            created_by=None,
        )
        await session.commit()
        return {**self._run_projection(context), "status": "succeeded", "progress_percent": 100}

    async def _execute_capability(
        self,
        session: AsyncSession,
        *,
        context: dict[str, Any],
        step: dict[str, Any],
        outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        capability = step["capability_id"]
        definition = tool_definition(capability)
        permission_code = definition["permission_code"]
        allowed = await self.repository.has_effective_permission(
            session,
            tenant_id=int(context["tenant_id"]),
            user_id=int(context["goal_created_by"]),
            permission_code=permission_code,
        )
        if not allowed:
            raise BusinessError(
                "AGENT_CAPABILITY_PERMISSION_REVOKED",
                f"执行权限已撤销：{permission_code}",
                status_code=403,
            )
        budget = dict(context.get("budget") or {})
        try:
            max_tool_calls = int(budget.get("max_tool_calls"))
        except (TypeError, ValueError) as exc:
            raise BusinessError(
                "AGENT_BUDGET_INVALID",
                "执行实例缺少有效的 max_tool_calls 预算",
                status_code=409,
            ) from exc
        if max_tool_calls < 0:
            raise BusinessError(
                "AGENT_BUDGET_INVALID",
                "执行实例缺少有效的 max_tool_calls 预算",
                status_code=409,
            )
        reserved_count = await self.repository.reserve_tool_call(
            session,
            tenant_id=int(context["tenant_id"]),
            run_id=int(context["id"]),
            max_tool_calls=max_tool_calls,
        )
        if reserved_count is None:
            raise BusinessError(
                "AGENT_TOOL_BUDGET_EXCEEDED",
                f"工具调用次数已达到预算上限 {max_tool_calls}",
                status_code=409,
            )
        context["tool_call_count"] = reserved_count
        handler = getattr(self, f"_capability_{capability.replace('.', '_')}", None)
        if handler is None:
            raise BusinessError(
                "AGENT_CAPABILITY_UNAVAILABLE",
                f"执行能力不可用：{capability}",
                status_code=422,
            )
        return await handler(session, context=context, step=step, outputs=outputs)

    async def _capability_product_read(self, session, *, context, **_) -> dict[str, Any]:
        product_id = (context.get("constraints") or {}).get("product_id")
        row = (await session.execute(text("""
            SELECT p.id,p.sku,p.name,p.category_code,p.current_profile_version_id,
                   pv.status AS profile_status,p.updated_at
              FROM products p
              LEFT JOIN product_profile_versions pv
                ON pv.id=p.current_profile_version_id AND pv.tenant_id=p.tenant_id
             WHERE p.tenant_id=:tenant AND p.id=:product
               AND p.deleted_at IS NULL
        """), {
            "tenant": context["tenant_id"],
            "product": product_id,
        })).mappings().one_or_none()
        if row is None or row["profile_status"] != "confirmed":
            raise AgentNeedsApproval(
                "product_profile_required",
                "确认产品档案",
                "请先补齐并确认产品画像，再继续执行市场评估。",
                "市场评估只能引用已确认的产品事实。",
                f"product-detail?id={product_id}" if product_id else "products",
            )
        return dict(row)

    async def _capability_market_dataset_read(self, session, *, context, outputs, **_) -> dict[str, Any]:
        constraints = context.get("constraints") or {}
        product = outputs.get("product.read") or {}
        row = (await session.execute(text("""
            SELECT id,name,version_no,platform,market_country,category_code,status,
                   quality_score,data_start_date,data_end_date,updated_at
              FROM market_datasets
             WHERE tenant_id=:tenant AND id=:dataset AND deleted_at IS NULL
               AND status='ready'
        """), {
            "tenant": context["tenant_id"],
            "dataset": constraints.get("dataset_id"),
        })).mappings().one_or_none()
        if (
            row is None
            or row["market_country"] != constraints.get("market", "US")
            or row["category_code"] != product.get("category_code")
        ):
            raise AgentNeedsApproval(
                "market_dataset_required",
                "选择可用市场数据",
                "请导入或选择与产品品类、目标市场匹配的已就绪数据集。",
                "缺少匹配的授权数据时，AI 员工不会生成市场结论。",
                "market-data",
            )
        return dict(row)

    async def _capability_market_analysis_run(self, session, *, context, step, outputs) -> dict[str, Any]:
        product = outputs["product.read"]
        dataset = outputs["market.dataset.read"]
        country = str(dataset["market_country"]).strip().upper()
        currency = {"US": "USD", "GB": "GBP", "DE": "EUR", "JP": "JPY"}.get(country, "USD")
        payload = {
            "job_name": context["objective"][:200],
            "job_type": "product_market_fit",
            "product_id": int(product["id"]),
            "product_profile_version_id": int(product["current_profile_version_id"]),
            "dataset_id": int(dataset["id"]),
            "target_country": country,
            "target_platform": dataset["platform"],
            "analysis_currency": currency,
            "analysis_config": {
                "source": "ai_employee",
                "agent_goal_uuid": context["goal_uuid"],
                "target_node": "report",
            },
        }
        service = AnalysisTaskService(self.settings)
        _, created = await service.create(
            session,
            tenant_id=int(context["tenant_id"]),
            user_id=int(context["goal_created_by"]),
            idempotency_key=f"agent:{context['run_uuid']}:analysis:create",
            payload=payload,
            response_envelope=lambda data: data,
            request_id=f"agent-{context['run_uuid']}",
        )
        task_uuid = str(created["task_uuid"])
        _, _, dispatch = await service.start(
            session,
            tenant_id=int(context["tenant_id"]),
            user_id=int(context["goal_created_by"]),
            task_uuid=task_uuid,
            idempotency_key=f"agent:{context['run_uuid']}:analysis:start",
            response_envelope=lambda data: data,
        )
        approved = await self.repository.approved_step_response(
            session,
            tenant_id=int(context["tenant_id"]),
            run_id=int(context["id"]),
            step_id=int(step["id"]),
        )
        await session.commit()
        adapter = AnalysisAgentAdapter(self.settings)
        if approved and (approved.get("impact") or {}).get("analysis_confirmation_id"):
            response = approved.get("response") or {}
            await adapter.accept_confirmation(
                confirmation_id=approved["impact"]["analysis_confirmation_id"],
                selected_option=response.get("selected_option"),
                user_input=response.get("user_input"),
                user_id=int(context["goal_created_by"]),
                tenant_id=int(context["tenant_id"]),
            )
            await adapter.resume_next_confirmation(int(context["tenant_id"]))
        elif dispatch:
            await adapter.run(**dispatch)
        status = await service.status(
            session,
            tenant_id=int(context["tenant_id"]),
            task_uuid=task_uuid,
            include_stage_runs=False,
            stage_run_limit=1,
            admin=False,
        )
        confirmation = status.get("user_confirmation")
        if status["status"] == "waiting_human" and confirmation:
            options = list(confirmation.get("options") or [])
            options.append({"value": "cancel", "label": "取消整个目标"})
            raise AgentNeedsApproval(
                "analysis_confirmation",
                "分析需要业务确认",
                confirmation["question"],
                "底层分析工作流在安全检查点等待确认。",
                f"workflow?task={task_uuid}&from=employee",
                impact={
                    "analysis_confirmation_id": str(confirmation["confirmation_id"]),
                    "analysis_task_uuid": task_uuid,
                    "checkpoint_stage": confirmation["checkpoint_stage"],
                },
                options=options,
                recommended_option=confirmation["recommended_option"],
            )
        if status["status"] != "succeeded":
            raise BusinessError(
                "AGENT_ANALYSIS_INCOMPLETE",
                status.get("failure_message") or "市场分析未成功完成",
                status_code=409,
            )
        return {
            "task_uuid": task_uuid,
            "report_uuid": str(status["report_uuid"]),
            "status": status["status"],
            "open_href": f"report-detail?id={status['report_uuid']}&from=employee",
        }

    async def _capability_report_verify(self, session, *, context, outputs, **_) -> dict[str, Any]:
        task_uuid = outputs["market_analysis.run"]["task_uuid"]
        result = await AnalysisTaskService(self.settings).result(
            session,
            tenant_id=int(context["tenant_id"]),
            task_uuid=task_uuid,
            opportunity_limit=10,
            recommendation_limit=20,
        )
        evidence_count = int(await session.scalar(text("""
            SELECT count(*)
              FROM evidence_links e
              JOIN analysis_tasks t
                ON t.id=e.analysis_job_id AND t.tenant_id=e.tenant_id
             WHERE e.tenant_id=:tenant AND t.task_uuid=CAST(:task AS uuid)
        """), {"tenant": context["tenant_id"], "task": task_uuid}) or 0)
        if not result.get("report_uuid") or evidence_count < 1:
            raise BusinessError(
                "AGENT_REPORT_VERIFICATION_FAILED",
                "决策报告缺少可核验的报告标识或证据引用",
                status_code=422,
            )
        return {
            "verified": True,
            "report_uuid": str(result["report_uuid"]),
            "task_uuid": task_uuid,
            "evidence_count": evidence_count,
            "summary": _plain(result["report_summary"]),
            "data_scope": _plain(result["data_scope"]),
            "open_href": f"report-detail?id={result['report_uuid']}&from=employee",
        }

    async def _capability_business_query(self, session, *, context, **_) -> dict[str, Any]:
        constraints = context.get("constraints") or {}
        plan = DataQueryPlan(
            metrics=["sales_units", "sales_revenue", "average_daily_sales"],
            grain="weekly",
            group_by=["sku"],
            filters=DataQueryFilters(
                relative_days=int(constraints.get("period_days") or 28),
                sites=[constraints["site"]] if constraints.get("site") else [],
            ),
            data_version_uuid=constraints.get("data_version_uuid"),
            limit=100,
        )
        return await DataQueryService(self.settings).execute(
            session,
            tenant_id=int(context["tenant_id"]),
            user_id=int(context["goal_created_by"]),
            plan=plan,
        )

    async def _capability_forecast_status_read(self, session, *, context, **_) -> dict[str, Any]:
        deployment = await ForecastRepository().active_deployment(
            session,
            tenant_id=int(context["tenant_id"]),
        )
        if deployment is None:
            return {
                "available": False,
                "status": "not_published",
                "message": "当前没有已发布的企业预测模型，周报仅陈述历史销量。",
            }
        return {
            "available": True,
            "status": "published",
            "deployment_uuid": deployment["deployment_uuid"],
            "training_data_through": deployment["training_data_through"],
            "model_scope": deployment["model_scope"],
        }

    async def _capability_sales_review_compose(self, _session, *, outputs, **_) -> dict[str, Any]:
        query = outputs["business.query"]
        rows = query.get("rows") or []
        total_units = sum(float(row.get("sales_units") or 0) for row in rows)
        total_revenue = sum(float(row.get("sales_revenue") or 0) for row in rows)
        ranked = sorted(
            rows,
            key=lambda item: float(item.get("sales_units") or 0),
            reverse=True,
        )
        return {
            "title": "每周销量复盘",
            "summary": {
                "sales_units": round(total_units, 2),
                "sales_revenue": round(total_revenue, 2),
                "row_count": query.get("row_count", 0),
                "top_skus": ranked[:5],
            },
            "forecast_status": outputs["forecast.status.read"],
            "query_uuid": query["query_uuid"],
            "data_version_sha256": query["source_version"]["canonical_sha256"],
            "limitations": query.get("limitations") or [],
        }

    async def _capability_forecast_data_inspect(self, session, *, context, **_) -> dict[str, Any]:
        version_uuid = (context.get("constraints") or {}).get("data_version_uuid")
        row = (await session.execute(text("""
            SELECT id,version_uuid::text,filename,canonical_sha256,rules,
                   quality,columns_info,confirmed_at
              FROM forecast_data_versions
             WHERE tenant_id=:tenant AND status='confirmed'
               AND rules->>'kind'='sales'
               AND (CAST(:version AS text) IS NULL
                    OR version_uuid=CAST(:version AS uuid))
             ORDER BY confirmed_at DESC,id DESC LIMIT 1
        """), {
            "tenant": context["tenant_id"],
            "version": version_uuid,
        })).mappings().one_or_none()
        if row is None:
            raise AgentNeedsApproval(
                "confirmed_sales_data_required",
                "确认标准销量数据",
                "请先上传、预检并确认一个销量数据版本。",
                "数据就绪检查只能读取不可变的已确认版本。",
                "forecast?tab=training",
            )
        return dict(row)

    async def _capability_sku_mapping_inspect(self, session, *, context, outputs, **_) -> dict[str, Any]:
        version = outputs.get("forecast.data.inspect")
        await DataQueryService(self.settings).project_version(
            session,
            tenant_id=int(context["tenant_id"]),
            version_uuid=version["version_uuid"],
        )
        sku_rows = (await session.execute(text("""
            SELECT sku,site,
                   GREATEST(1,ceil(count(DISTINCT fact_date)::numeric/7))::int
                     AS history_weeks
              FROM sales_facts_daily
             WHERE tenant_id=:tenant AND data_version_id=:version
             GROUP BY sku,site ORDER BY sku,site
        """), {
            "tenant": context["tenant_id"],
            "version": version["id"],
        })).mappings().all()
        preview = await ForecastCatalog().preview(
            session,
            int(context["tenant_id"]),
            [dict(row) for row in sku_rows],
        )
        return {
            "mapped_count": len(preview["items"]),
            "unmapped_count": len(preview["unmapped"]),
            "conflict_count": len(preview["conflicts"]),
            "can_publish": preview["can_publish"],
            "unmapped": preview["unmapped"][:20],
            "conflicts": preview["conflicts"][:20],
            "open_href": "forecast?tab=training",
        }

    async def _capability_readiness_verify(self, _session, *, outputs, **_) -> dict[str, Any]:
        version = outputs["forecast.data.inspect"]
        mapping = outputs["sku.mapping.inspect"]
        quality = dict(version.get("quality") or {})
        blockers: list[dict[str, Any]] = []
        if mapping["unmapped_count"]:
            blockers.append({
                "code": "UNMAPPED_SKUS",
                "count": mapping["unmapped_count"],
                "message": "存在未关联到产品中心的来源 SKU。",
                "href": mapping["open_href"],
            })
        if mapping["conflict_count"]:
            blockers.append({
                "code": "SKU_MAPPING_CONFLICTS",
                "count": mapping["conflict_count"],
                "message": "SKU 映射不是一一对应关系。",
                "href": mapping["open_href"],
            })
        return {
            "ready": not blockers,
            "data_version_uuid": version["version_uuid"],
            "data_version_sha256": version["canonical_sha256"],
            "quality": quality,
            "sku_mapping": mapping,
            "blocking_issues": blockers,
        }

    async def _capability_artifact_deliver(self, session, *, context, step, outputs) -> dict[str, Any]:
        skill_id = context["selected_skill_id"]
        if skill_id == "market_entry_assessment":
            source = outputs["report.verify"]
            artifact = {
                "artifact_type": "decision_report",
                "title": source["summary"]["title"],
                "summary": source["summary"]["executive_summary"],
                "resource_type": "analysis_report",
                "resource_uuid": source["report_uuid"],
                "resource_version": 1,
                "content": source,
                "sha256": _sha256(source),
                "open_href": source["open_href"],
            }
        elif skill_id == "weekly_sales_review":
            source = outputs["sales_review.compose"]
            artifact = {
                "artifact_type": "sales_review",
                "title": source["title"],
                "summary": (
                    f"统计销量 {source['summary']['sales_units']:.0f} 件；"
                    f"预测状态：{source['forecast_status']['status']}。"
                ),
                "resource_type": "data_query",
                "resource_uuid": source["query_uuid"],
                "resource_version": 1,
                "content": source,
                "sha256": _sha256(source),
                "open_href": "forecast?tab=history",
            }
        else:
            source = outputs["readiness.verify"]
            artifact = {
                "artifact_type": "data_readiness_report",
                "title": "数据就绪检查清单",
                "summary": (
                    "数据已满足训练前置条件。"
                    if source["ready"]
                    else f"发现 {len(source['blocking_issues'])} 类阻断问题。"
                ),
                "resource_type": "forecast_data_version",
                "resource_uuid": source["data_version_uuid"],
                "resource_version": 1,
                "content": source,
                "sha256": _sha256(source),
                "open_href": "forecast?tab=training",
            }
        artifact_uuid = await self.repository.create_artifact(
            session,
            tenant_id=int(context["tenant_id"]),
            goal_id=int(context["goal_id"]),
            run_id=int(context["id"]),
            step_id=int(step["id"]),
            artifact=artifact,
        )
        return {
            "artifact_uuid": artifact_uuid,
            "artifact_type": artifact["artifact_type"],
            "title": artifact["title"],
            "sha256": artifact["sha256"],
            "open_href": artifact["open_href"],
        }

    async def _finish_cancelled(self, session, context: dict[str, Any]) -> None:
        await self.repository.set_run_state(
            session,
            tenant_id=int(context["tenant_id"]),
            run_id=int(context["id"]),
            goal_id=int(context["goal_id"]),
            status="cancelled",
            progress_percent=float(context["progress_percent"]),
            current_step_id=context.get("current_step_id"),
        )
        await self.repository.append_event(
            session,
            tenant_id=int(context["tenant_id"]),
            goal_id=int(context["goal_id"]),
            run_id=int(context["id"]),
            event_type="run.cancelled",
            payload={},
        )

    @staticmethod
    def _run_projection(context: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_uuid": context["run_uuid"],
            "status": context["status"],
            "progress_percent": float(context.get("progress_percent") or 0),
            "tool_call_count": int(context.get("tool_call_count") or 0),
            "replan_count": int(context.get("replan_count") or 0),
            "failure_code": context.get("failure_code"),
            "failure_message": context.get("failure_message"),
        }
