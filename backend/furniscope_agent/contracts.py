"""Ports between orchestration and business/tool implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .state import FurniScopeGraphState, PartialFailure, UserConfirmation


@dataclass(slots=True)
class CapabilityResult:
    output_ref: dict[str, Any] = field(default_factory=dict)
    state_update: dict[str, Any] = field(default_factory=dict)
    quality_flags: list[dict[str, Any]] = field(default_factory=list)
    partial_failures: list[PartialFailure] = field(default_factory=list)
    confirmation: UserConfirmation | None = None
    skipped: bool = False


@dataclass(slots=True)
class StageRunHandle:
    run_id: int
    status: str
    output_ref: dict[str, Any] | None = None


@dataclass(slots=True)
class ConfirmationAcceptance:
    """Server-derived resume envelope written atomically to the outbox."""

    event_uuid: str
    task_id: int
    task_uuid: str
    tenant_id: int
    checkpoint_id: str
    payload: dict[str, Any]
    already_accepted: bool = False


@dataclass(slots=True)
class ResumeOutboxEvent:
    event_uuid: str
    task_id: int
    task_uuid: str
    tenant_id: int
    checkpoint_id: str
    payload: dict[str, Any]


class AgentToolbox(Protocol):
    """Furniture algorithms live behind this interface; graph semantics stay stable."""

    async def execute(
        self,
        capability: str,
        state: FurniScopeGraphState,
        payload: dict[str, Any] | None = None,
    ) -> CapabilityResult: ...


class WorkflowRepository(Protocol):
    async def create_or_load_task(self, state: FurniScopeGraphState) -> dict[str, Any]: ...
    async def start_stage(self, state: FurniScopeGraphState, stage_code: str, input_ref: dict[str, Any]) -> StageRunHandle: ...
    async def finish_stage(self, run_id: int, status: str, output_ref: dict[str, Any]) -> None: ...
    async def fail_stage(self, run_id: int, code: str, message: str, retryable: bool) -> None: ...
    async def update_task(self, state: FurniScopeGraphState, **changes: Any) -> None: ...
    async def save_partial_failures(self, state: FurniScopeGraphState, run_id: int, failures: list[PartialFailure]) -> None: ...
    async def save_business_checkpoint(self, state: FurniScopeGraphState, stage_code: str, safe: bool) -> str: ...
    async def begin_confirmation_wait(self, state: FurniScopeGraphState, confirmation: UserConfirmation) -> str: ...
    async def accept_confirmation(self, confirmation_id: str, selected_option: str, user_input: Any, user_id: int) -> ConfirmationAcceptance: ...
    async def claim_resume_event(self) -> ResumeOutboxEvent | None: ...
    async def consume_resume_event(self, event: ResumeOutboxEvent) -> None: ...
    async def fail_resume_event(self, event_uuid: str, error_code: str) -> None: ...
    async def final_persist(self, state: FurniScopeGraphState, report_ref: dict[str, Any]) -> dict[str, Any]: ...
