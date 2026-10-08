"""Contracts for the tenant-scoped AI employee runtime."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AgentGoalStatus = Literal[
    "draft",
    "planning",
    "plan_ready",
    "queued",
    "running",
    "waiting_human",
    "verifying",
    "delivering",
    "succeeded",
    "partial_succeeded",
    "failed",
    "cancelled",
    "paused",
]


class GoalDraftRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=2000)
    preferred_skill_id: str | None = Field(default=None, max_length=100)
    constraints: dict[str, Any] = Field(default_factory=dict)
    resource_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    return_href: str | None = Field(default=None, max_length=1000)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GoalDraftResponse(BaseModel):
    objective: str
    selected_skill_id: str
    skill_name: str
    expected_deliverables: list[dict[str, Any]]
    constraints: dict[str, Any]
    acceptance_criteria: list[str]
    autonomy_envelope: dict[str, Any]
    resolved_resources: list[dict[str, Any]]
    validation_issues: list[dict[str, Any]]
    estimated_steps: int = Field(ge=1, le=12)
    return_href: str | None = None


class GoalCreateRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=2000)
    selected_skill_id: str = Field(min_length=3, max_length=100)
    expected_deliverables: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    constraints: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    autonomy_envelope: dict[str, Any] = Field(default_factory=dict)
    deadline: datetime | None = None
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    trigger_type: Literal["user_delegate", "business_page"] = "user_delegate"
    source_mode: Literal["employee", "copilot"] = "employee"
    return_href: str | None = Field(default=None, max_length=1000)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class GoalMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RunControlRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ApprovalRespondRequest(BaseModel):
    selected_option: str = Field(min_length=1, max_length=64)
    user_input: Any = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AgentGoalSummary(BaseModel):
    goal_uuid: UUID
    objective: str
    selected_skill_id: str
    skill_name: str | None = None
    status: AgentGoalStatus
    priority: str
    progress_percent: float = Field(ge=0, le=100)
    pending_approvals: int = Field(ge=0)
    artifact_count: int = Field(ge=0)
    current_run_uuid: UUID | None = None
    next_step_title: str | None = None
    return_href: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class AgentOverview(BaseModel):
    profile: dict[str, Any]
    counts: dict[str, int]
    commitments: list[AgentGoalSummary]
    approvals: list[dict[str, Any]]
    artifacts: list[dict[str, Any]]
    activities: list[dict[str, Any]]


class AgentPlanResponse(BaseModel):
    plan_uuid: UUID
    version: int
    skill_id: str
    skill_version: int
    plan_sha256: str
    risk_summary: dict[str, Any]
    status: str
    steps: list[dict[str, Any]]


class AgentRunResponse(BaseModel):
    run_uuid: UUID
    status: str
    progress_percent: float
    tool_call_count: int
    replan_count: int
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AgentGoalDetail(AgentGoalSummary):
    expected_deliverables: list[dict[str, Any]]
    constraints: dict[str, Any]
    acceptance_criteria: list[str]
    autonomy_envelope: dict[str, Any]
    deadline: datetime | None = None
    plan: AgentPlanResponse | None = None
    run: AgentRunResponse | None = None
    artifacts: list[dict[str, Any]]
    approvals: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    messages: list[dict[str, Any]]


class AgentSkillItem(BaseModel):
    skill_id: str
    version: int
    display_name: str
    description: str
    deliverable_label: str
    status: str = "active"
    step_count: int = Field(ge=1, le=12)


class AgentCapabilityItem(BaseModel):
    capability_id: str
    version: int
    display_name: str
    description: str
    provider: str
    effect_level: str
    permission_code: str | None = None
    status: str = "active"
