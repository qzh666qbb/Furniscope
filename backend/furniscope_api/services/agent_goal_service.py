"""Application service for AI employee goals and runtime controls."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import BusinessError
from ..repositories.agent_repository import AgentRepository
from ..schemas.agent_runtime import (
    AgentGoalDetail,
    AgentGoalSummary,
    AgentPlanResponse,
    AgentRunResponse,
    GoalCreateRequest,
    GoalDraftRequest,
)
from .agent_planner import compile_plan, match_skill, skill_definition


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


class AgentGoalService:
    def __init__(self) -> None:
        self.repository = AgentRepository()

    @staticmethod
    def _budget_value(value: Any, *, field: str, default: int) -> int:
        if value is None:
            return default
        if isinstance(value, bool):
            raise BusinessError(
                "AGENT_BUDGET_INVALID",
                f"{field} 必须是非负整数",
                status_code=422,
            )
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise BusinessError(
                "AGENT_BUDGET_INVALID",
                f"{field} 必须是非负整数",
                status_code=422,
            ) from exc
        if result < 0:
            raise BusinessError(
                "AGENT_BUDGET_INVALID",
                f"{field} 必须是非负整数",
                status_code=422,
            )
        return result

    @staticmethod
    def _autonomy_envelope(profile: dict[str, Any]) -> dict[str, Any]:
        budget = dict(profile.get("default_budget") or {})
        return {
            "level": profile.get("autonomy_level") or "L2",
            "allowed_effect_levels": ["R0", "R1", "R2"],
            "requires_confirmation": ["R3"],
            "denied_effect_levels": ["R4"],
            "max_tool_calls": AgentGoalService._budget_value(
                budget.get("max_tool_calls"),
                field="max_tool_calls",
                default=30,
            ),
            "max_replans": AgentGoalService._budget_value(
                budget.get("max_replans"),
                field="max_replans",
                default=1,
            ),
        }

    @classmethod
    def _merge_autonomy_envelope(
        cls,
        profile: dict[str, Any],
        requested: dict[str, Any] | None,
    ) -> dict[str, Any]:
        base = cls._autonomy_envelope(profile)
        requested = requested or {}
        return {
            **base,
            "max_tool_calls": min(
                base["max_tool_calls"],
                cls._budget_value(
                    requested.get("max_tool_calls"),
                    field="max_tool_calls",
                    default=base["max_tool_calls"],
                ),
            ),
            "max_replans": min(
                base["max_replans"],
                cls._budget_value(
                    requested.get("max_replans"),
                    field="max_replans",
                    default=base["max_replans"],
                ),
            ),
        }

    @staticmethod
    def _assert_plan_within_budget(
        *,
        tool_count: int,
        autonomy_envelope: dict[str, Any],
    ) -> None:
        max_tool_calls = AgentGoalService._budget_value(
            autonomy_envelope.get("max_tool_calls"),
            field="max_tool_calls",
            default=0,
        )
        if tool_count > max_tool_calls:
            raise BusinessError(
                "AGENT_TOOL_BUDGET_EXCEEDED",
                (
                    f"执行计划需要 {tool_count} 次工具调用，"
                    f"超过当前上限 {max_tool_calls}"
                ),
                status_code=422,
            )

    async def draft(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        body: GoalDraftRequest,
    ) -> dict[str, Any]:
        profile = await self.repository.ensure_catalog(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        skill = match_skill(body.objective, body.preferred_skill_id)
        constraints, resources, issues = await self.repository.resolve_resources(
            session,
            tenant_id=tenant_id,
            objective=body.objective,
            constraints=body.constraints,
            skill_id=skill["skill_id"],
        )
        if body.resource_refs:
            constraints["resource_refs"] = body.resource_refs
        return {
            "objective": body.objective,
            "selected_skill_id": f"{skill['skill_id']}@{skill['version']}",
            "skill_name": skill["display_name"],
            "expected_deliverables": list(skill["expected_deliverables"]),
            "constraints": constraints,
            "acceptance_criteria": list(skill["acceptance_criteria"]),
            "autonomy_envelope": self._autonomy_envelope(profile),
            "resolved_resources": resources,
            "validation_issues": issues,
            "estimated_steps": len(skill["steps"]),
            "return_href": body.return_href,
        }

    async def create(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        idempotency_key: str,
        body: GoalCreateRequest,
    ) -> dict[str, Any]:
        profile = await self.repository.ensure_catalog(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        skill = skill_definition(body.selected_skill_id)
        constraints, resources, issues = await self.repository.resolve_resources(
            session,
            tenant_id=tenant_id,
            objective=body.objective,
            constraints=body.constraints,
            skill_id=skill["skill_id"],
        )
        payload = body.model_dump(mode="json")
        payload["selected_skill_id"] = f"{skill['skill_id']}@{skill['version']}"
        payload["constraints"] = {
            **constraints,
            "resolved_resources": resources,
            "validation_issues": issues,
        }
        payload["expected_deliverables"] = (
            payload["expected_deliverables"] or list(skill["expected_deliverables"])
        )
        payload["acceptance_criteria"] = (
            payload["acceptance_criteria"] or list(skill["acceptance_criteria"])
        )
        payload["autonomy_envelope"] = self._merge_autonomy_envelope(
            profile,
            payload["autonomy_envelope"],
        )
        compiled = compile_plan(skill["skill_id"], payload["constraints"])
        self._assert_plan_within_budget(
            tool_count=int(compiled["risk_summary"]["tool_count"]),
            autonomy_envelope=payload["autonomy_envelope"],
        )
        request_hash = _canonical_sha256(payload)
        existing = await self.repository.find_goal_by_idempotency(
            session,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
        )
        if existing:
            if existing["request_hash"] != request_hash:
                raise BusinessError(
                    "AGENT_GOAL_IDEMPOTENCY_CONFLICT",
                    "同一幂等键已用于不同的目标内容",
                    status_code=409,
                )
            detail = await self.get_goal(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                goal_uuid=existing["goal_uuid"],
            )
            return detail
        goal_uuid = await self.repository.create_goal(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            profile_id=int(profile["id"]),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            payload=payload,
        )
        goal = await self.repository.goal_for_update(
            session,
            tenant_id=tenant_id,
            goal_uuid=goal_uuid,
        )
        await self.repository.create_plan(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            goal=goal,
            compiled=compiled,
        )
        await self.repository.append_event(
            session,
            tenant_id=tenant_id,
            goal_id=int(goal["id"]),
            run_id=None,
            event_type="goal.created",
            payload={
                "objective": body.objective,
                "skill_id": skill["skill_id"],
                "plan_sha256": compiled["plan_sha256"],
            },
        )
        await self.repository.append_message(
            session,
            tenant_id=tenant_id,
            goal_id=int(goal["id"]),
            role="user",
            content=body.objective,
            created_by=user_id,
        )
        await self.repository.append_message(
            session,
            tenant_id=tenant_id,
            goal_id=int(goal["id"]),
            role="employee",
            content=(
                f"已接收目标，采用“{skill['display_name']}”技能并生成"
                f" {len(skill['steps'])} 步执行计划。"
            ),
            created_by=None,
        )
        return await self.get_goal(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            goal_uuid=goal_uuid,
        )

    async def get_goal(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        goal_uuid: str,
    ) -> dict[str, Any]:
        data = await self.repository.goal_detail(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            goal_uuid=goal_uuid,
        )
        if data is None:
            raise BusinessError(
                "AGENT_GOAL_NOT_FOUND",
                "目标不存在或不可访问",
                status_code=404,
            )
        summary = AgentGoalSummary.model_validate({
            key: data.get(key)
            for key in AgentGoalSummary.model_fields
        })
        plan = (
            AgentPlanResponse.model_validate(data["plan"])
            if data.get("plan")
            else None
        )
        run = (
            AgentRunResponse.model_validate(data["run"])
            if data.get("run")
            else None
        )
        return AgentGoalDetail.model_validate({
            **summary.model_dump(),
            "expected_deliverables": data.get("expected_deliverables") or [],
            "constraints": data.get("constraints") or {},
            "acceptance_criteria": data.get("acceptance_criteria") or [],
            "autonomy_envelope": data.get("autonomy_envelope") or {},
            "deadline": data.get("deadline"),
            "plan": plan,
            "run": run,
            "artifacts": data.get("artifacts") or [],
            "approvals": data.get("approvals") or [],
            "timeline": data.get("timeline") or [],
            "messages": data.get("messages") or [],
        }).model_dump(mode="python")

    async def start(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        goal_uuid: str,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        await self.get_goal(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            goal_uuid=goal_uuid,
        )
        goal = await self.repository.goal_for_update(
            session,
            tenant_id=tenant_id,
            goal_uuid=goal_uuid,
        )
        if goal is None:
            raise BusinessError("AGENT_GOAL_NOT_FOUND", "目标不存在", status_code=404)
        if goal["current_run_id"]:
            detail = await self.get_goal(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                goal_uuid=goal_uuid,
            )
            return detail, None
        if goal["status"] != "plan_ready" or goal["current_plan_id"] is None:
            raise BusinessError(
                "AGENT_PLAN_NOT_READY",
                "目标计划尚未准备完成",
                status_code=409,
            )
        steps = await self.repository.plan_steps(
            session,
            tenant_id=tenant_id,
            plan_id=int(goal["current_plan_id"]),
        )
        self._assert_plan_within_budget(
            tool_count=len(steps),
            autonomy_envelope=dict(goal.get("autonomy_envelope") or {}),
        )
        run = await self.repository.create_run(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            goal=goal,
        )
        await self.repository.append_event(
            session,
            tenant_id=tenant_id,
            goal_id=int(goal["id"]),
            run_id=run["id"],
            event_type="run.queued",
            payload={"run_uuid": run["run_uuid"]},
        )
        detail = await self.get_goal(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            goal_uuid=goal_uuid,
        )
        return detail, {
            "tenant_id": tenant_id,
            "run_uuid": run["run_uuid"],
        }

    async def append_message(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        goal_uuid: str,
        content: str,
    ) -> list[dict[str, Any]]:
        detail = await self.get_goal(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            goal_uuid=goal_uuid,
        )
        goal = await self.repository.goal_for_update(
            session,
            tenant_id=tenant_id,
            goal_uuid=goal_uuid,
        )
        await self.repository.append_message(
            session,
            tenant_id=tenant_id,
            goal_id=int(goal["id"]),
            role="user",
            content=content,
            created_by=user_id,
        )
        await self.repository.append_goal_instruction(
            session,
            tenant_id=tenant_id,
            goal_id=int(goal["id"]),
            content=content,
        )
        acknowledgement = (
            "补充要求已加入当前目标约束。后续尚未执行的步骤会读取这项要求；"
            "已经完成的证据不会被覆盖。"
        )
        if detail["status"] in {"succeeded", "partial_succeeded", "failed", "cancelled"}:
            acknowledgement = "补充要求已记录。该目标已结束，请新建目标后再执行新的工作。"
        await self.repository.append_message(
            session,
            tenant_id=tenant_id,
            goal_id=int(goal["id"]),
            role="employee",
            content=acknowledgement,
            created_by=None,
        )
        refreshed = await self.get_goal(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            goal_uuid=goal_uuid,
        )
        return refreshed["messages"]

    async def control_run(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        run_uuid: str,
        action: str,
        reason: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        row = await self.repository.request_run_control(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            run_uuid=run_uuid,
            action=action,
            reason=reason,
        )
        if row is None:
            raise BusinessError(
                "AGENT_RUN_NOT_FOUND",
                "执行实例不存在或不可访问",
                status_code=404,
            )
        return {
            "run_uuid": run_uuid,
            "action": action,
            "accepted": True,
        }, (
            {"tenant_id": tenant_id, "run_uuid": run_uuid}
            if action in {"resume", "cancel"}
            else None
        )

    async def respond_approval(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        approval_uuid: str,
        selected_option: str,
        user_input: Any,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        approval = await self.repository.approval_for_update(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            approval_uuid=approval_uuid,
        )
        if approval is None:
            raise BusinessError(
                "AGENT_APPROVAL_NOT_FOUND",
                "待处理事项不存在或不可访问",
                status_code=404,
            )
        if approval["status"] != "pending":
            previous = dict(approval.get("response") or {})
            if previous.get("selected_option") != selected_option:
                raise BusinessError(
                    "AGENT_APPROVAL_ALREADY_RESPONDED",
                    "该事项已经处理，不能提交不同选择",
                    status_code=409,
                )
            return {
                "approval_uuid": approval_uuid,
                "status": approval["status"],
                "run_uuid": approval["run_uuid"],
            }, None
        valid_options = {
            str(item.get("value"))
            for item in (approval.get("options") or [])
            if isinstance(item, dict)
        }
        if selected_option not in valid_options:
            raise BusinessError(
                "AGENT_APPROVAL_OPTION_INVALID",
                "所选处理方式不在允许范围内",
                status_code=422,
            )
        await self.repository.respond_approval(
            session,
            tenant_id=tenant_id,
            approval=approval,
            user_id=user_id,
            selected_option=selected_option,
            user_input=user_input,
        )
        return {
            "approval_uuid": approval_uuid,
            "status": "rejected" if selected_option == "cancel" else "approved",
            "run_uuid": approval["run_uuid"],
        }, (
            {
                "tenant_id": tenant_id,
                "run_uuid": approval["run_uuid"],
            }
            if selected_option != "cancel"
            else None
        )
