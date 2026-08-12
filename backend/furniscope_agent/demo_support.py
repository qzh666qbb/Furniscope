"""Deterministic adapters for local orchestration smoke tests.

They prove graph execution only. They are not furniture analysis algorithms and
must not be used as production insight output.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .contracts import CapabilityResult, ConfirmationAcceptance, ResumeOutboxEvent, StageRunHandle
from .state import FurniScopeGraphState, PartialFailure, UserConfirmation


class InMemoryWorkflowRepository:
    def __init__(self) -> None:
        self.stage_runs: list[dict[str, Any]] = []
        self.task_updates: list[dict[str, Any]] = []
        self.confirmations: dict[str, dict[str, Any]] = {}
        self.control_events: list[dict[str, Any]] = []

    async def create_or_load_task(self, state: FurniScopeGraphState) -> dict[str, Any]:
        versions = state["version_bundle"]
        return {
            "ontology_version": versions["ontology_version"],
            "scoring_version": versions["scoring_version"],
            "prompt_bundle_version": versions["prompt_bundle_version"],
            "model_route_version": versions["model_route_version"],
        }

    async def start_stage(self, state: FurniScopeGraphState, stage_code: str, input_ref: dict[str, Any]) -> StageRunHandle:
        self.stage_runs.append({"stage_code": stage_code, "status": "running", "input_ref": input_ref})
        return StageRunHandle(len(self.stage_runs), "running")

    async def finish_stage(self, run_id: int, status: str, output_ref: dict[str, Any]) -> None:
        self.stage_runs[run_id - 1].update(status=status, output_ref=output_ref)

    async def fail_stage(self, run_id: int, code: str, message: str, retryable: bool) -> None:
        self.stage_runs[run_id - 1].update(status="failed", error_code=code, retryable=retryable)

    async def update_task(self, state: FurniScopeGraphState, **changes: Any) -> None:
        self.task_updates.append(changes)

    async def save_partial_failures(self, state: FurniScopeGraphState, run_id: int, failures: list[PartialFailure]) -> None:
        return None

    async def save_business_checkpoint(self, state: FurniScopeGraphState, stage_code: str, safe: bool) -> str:
        return f"checkpoint:{state['task_uuid']}:{stage_code}"

    async def begin_confirmation_wait(self, state: FurniScopeGraphState, confirmation: UserConfirmation) -> str:
        existing = self.confirmations.get(confirmation["confirmation_id"])
        if existing:
            return existing["checkpoint_id"]
        checkpoint_id = f"checkpoint:{state['task_uuid']}:{confirmation['checkpoint_stage']}"
        self.confirmations[confirmation["confirmation_id"]] = {
            **confirmation, "checkpoint_id": checkpoint_id, "status": "pending",
            "task_id": state["task_id"], "task_uuid": state["task_uuid"], "tenant_id": state["tenant_id"],
        }
        self.stage_runs.append({"stage_code": "user_confirmation", "status": "waiting_human"})
        self.task_updates.append({"status": "waiting_human", "internal_stage": "user_confirmation", "checkpoint_stage": confirmation["checkpoint_stage"]})
        return checkpoint_id

    async def accept_confirmation(self, confirmation_id: str, selected_option: str, user_input: Any, user_id: int) -> ConfirmationAcceptance:
        row = self.confirmations[confirmation_id]
        if row["status"] == "responded" and row["selected_option"] != selected_option:
            raise ValueError("Idempotency conflict")
        payload = {"confirmation_id": confirmation_id, "selected_option": selected_option, "user_input": user_input, "checkpoint_stage": row["checkpoint_stage"]}
        if row["status"] == "responded":
            event = next(item for item in self.control_events if item["payload"]["confirmation_id"] == confirmation_id)
            return ConfirmationAcceptance(event["event_uuid"], row["task_id"], row["task_uuid"], row["tenant_id"], row["checkpoint_id"], payload, True)
        row.update(status="responded", selected_option=selected_option, responded_by=user_id)
        next(item for item in reversed(self.stage_runs) if item["stage_code"] == "user_confirmation")["status"] = "succeeded"
        event_uuid = str(uuid4())
        self.control_events.append({"event_uuid": event_uuid, "status": "pending", "payload": payload, **{key: row[key] for key in ("task_id", "task_uuid", "tenant_id", "checkpoint_id")}})
        return ConfirmationAcceptance(event_uuid, row["task_id"], row["task_uuid"], row["tenant_id"], row["checkpoint_id"], payload)

    async def claim_resume_event(self) -> ResumeOutboxEvent | None:
        event = next((item for item in self.control_events if item["status"] == "pending"), None)
        if event is None:
            return None
        event["status"] = "enqueued"
        return ResumeOutboxEvent(event["event_uuid"], event["task_id"], event["task_uuid"], event["tenant_id"], event["checkpoint_id"], event["payload"])

    async def consume_resume_event(self, event: ResumeOutboxEvent) -> None:
        next(item for item in self.control_events if item["event_uuid"] == event.event_uuid)["status"] = "consumed"

    async def fail_resume_event(self, event_uuid: str, error_code: str) -> None:
        event = next(item for item in self.control_events if item["event_uuid"] == event_uuid)
        event.update(status="failed", error_code=error_code)

    async def final_persist(self, state: FurniScopeGraphState, report_ref: dict[str, Any]) -> dict[str, Any]:
        if "report_uuid" not in report_ref:
            raise ValueError("report_uuid required")
        return report_ref


class DeterministicDemoToolbox:
    """Returns references needed to exercise every I00-I19 route."""

    async def execute(
        self,
        capability: str,
        state: FurniScopeGraphState,
        payload: dict[str, Any] | None = None,
    ) -> CapabilityResult:
        ref = {"capability": capability, "demo_only": True}
        updates: dict[str, Any] = {}
        if capability == "load_product_context":
            updates["product_context_ref"] = {"product_id": state["product_id"], "demo_only": True}
        elif capability == "data_quality":
            updates.update(valid_listing_ids=[101, 102], valid_review_ids=[201, 202], trend_eligible=False)
        elif capability == "competitor_rerank":
            updates["competitor_set_version"] = 1
        elif capability == "review_batch_plan":
            updates["review_batches"] = [{"batch_id": "b1", "review_ids": [201, 202]}]
        elif capability == "opportunity_scoring":
            updates["strategy_units"] = [{"opportunity_id": 301}]
        elif capability in {"report_compose", "deterministic_report"}:
            updates["report_ref"] = {"report_uuid": str(uuid4()), "demo_only": True}
        return CapabilityResult(output_ref=ref, state_update=updates)


def demo_initial_state() -> FurniScopeGraphState:
    return {
        "task_id": 1,
        "task_uuid": str(uuid4()),
        "tenant_id": 1,
        "status": "queued",
        "external_stage": "understanding_product",
        "internal_stage": "task_initializing",
        "progress_percent": 0,
        "product_id": 1,
        "product_profile_version": 1,
        "dataset_id": 1,
        "target_market": {"country": "US", "platform": "amazon", "currency": "USD"},
        "version_bundle": {
            "ontology_version": "configured-version",
            "scoring_version": "configured-version",
            "prompt_bundle_version": "configured-version",
            "model_route_version": "configured-version",
        },
        "analysis_config": {},
        "quality_flags": [],
        "stage_results": {},
        "partial_failures": [],
        "cancel_requested": False,
        "review_batch_results": [],
        "strategy_results": [],
        "analytics_results": [],
    }
