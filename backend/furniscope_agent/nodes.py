"""I00-I19 node implementations for the FurniScope supervisor."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from langgraph.types import interrupt

from .contracts import AgentToolbox, CapabilityResult, WorkflowRepository
from .errors import AgentError, FatalBusinessError
from .state import EXTERNAL_STAGE_BY_INTERNAL, PROGRESS_BY_STAGE, FurniScopeGraphState


@dataclass(frozen=True, slots=True)
class NodeSpec:
    code: str
    stage: str
    capability: str
    max_attempts: int = 3
    fallback_capability: str | None = None


SPECS = {
    "I00": NodeSpec("I00", "task_initializing", "create_and_freeze", 3),
    "I01": NodeSpec("I01", "preflight_check", "preflight", 3),
    "I02": NodeSpec("I02", "context_loading", "load_product_context", 3),
    "I03": NodeSpec("I03", "data_quality", "data_quality", 3),
    "I04": NodeSpec("I04", "competitor_filtering", "competitor_hard_filter", 3),
    "I05": NodeSpec("I05", "competitor_embedding", "competitor_embedding", 3, "competitor_rule_recall"),
    "I06": NodeSpec("I06", "competitor_reranking", "competitor_rerank", 3, "competitor_rule_rerank"),
    "I08": NodeSpec("I08", "review_preprocessing", "review_batch_plan", 3),
    "I09": NodeSpec("I09", "review_extracting", "review_extract_batch", 3),
    "I10": NodeSpec("I10", "review_extracting", "review_extract_reduce", 1),
    "I11": NodeSpec("I11", "need_clustering", "need_clustering", 3, "taxonomy_rule_cluster"),
    "I12": NodeSpec("I12", "market_analytics", "analytics_fork", 1),
    "I12A": NodeSpec("I12A", "market_analytics", "price_competition", 3),
    "I12B": NodeSpec("I12B", "market_analytics", "trend", 3, "trend_skip"),
    "I13": NodeSpec("I13", "market_analytics", "analytics_join", 1),
    "I14": NodeSpec("I14", "opportunity_scoring", "opportunity_scoring", 3),
    "I15": NodeSpec("I15", "strategy_generating", "strategy_generate", 3, "strategy_validation_only"),
    "I17": NodeSpec("I17", "evidence_auditing", "evidence_audit", 3),
    "I18": NodeSpec("I18", "report_generating", "report_compose", 3, "deterministic_report"),
    "I19": NodeSpec("I19", "persisting", "final_persist", 3),
}


class WorkflowNodes:
    def __init__(self, repository: WorkflowRepository, tools: AgentToolbox) -> None:
        self.repository = repository
        self.tools = tools

    async def _run(
        self,
        state: FurniScopeGraphState,
        spec: NodeSpec,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if state.get("cancel_requested"):
            await self.repository.update_task(state, status="cancelled")
            return {"status": "cancelled", "internal_stage": spec.stage}

        external = EXTERNAL_STAGE_BY_INTERNAL[spec.stage]
        progress = max(state.get("progress_percent", 0), PROGRESS_BY_STAGE[spec.stage])
        input_ref = {
            "node": spec.code,
            "product_id": state.get("product_id"),
            "dataset_id": state.get("dataset_id"),
            "version_bundle": state.get("version_bundle", {}),
            "payload_ref": payload or {},
        }
        handle = await self.repository.start_stage(state, spec.stage, input_ref)
        run_id = handle.run_id
        await self.repository.update_task(
            state, status="running", external_stage=external,
            internal_stage=spec.stage, progress_percent=progress,
        )

        result: CapabilityResult | None = None
        reused = False
        if handle.status in {"succeeded", "partial_succeeded", "skipped"} and handle.output_ref:
            envelope = handle.output_ref
            result = CapabilityResult(
                output_ref=envelope.get("result_ref", {}),
                state_update=envelope.get("state_update", {}),
                quality_flags=envelope.get("quality_flags", []),
                partial_failures=envelope.get("partial_failures", []),
                confirmation=envelope.get("confirmation"),
                skipped=handle.status == "skipped",
            )
            reused = True
        last_error: Exception | None = None
        for attempt in range(spec.max_attempts if result is None else 0):
            try:
                result = await self.tools.execute(spec.capability, state, payload)
                break
            except AgentError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= spec.max_attempts:
                    break
                await asyncio.sleep(min(2 ** attempt, 4))
            except Exception as exc:
                last_error = exc
                break

        if result is None and spec.fallback_capability:
            try:
                result = await self.tools.execute(spec.fallback_capability, state, payload)
                result.quality_flags.append({
                    "code": f"{spec.code}_DEGRADED",
                    "message": f"{spec.capability} degraded to {spec.fallback_capability}",
                })
            except Exception as exc:
                last_error = exc

        if result is None:
            error = last_error or FatalBusinessError("Unknown node failure")
            code = getattr(error, "code", "UNHANDLED_NODE_ERROR")
            retryable = bool(getattr(error, "retryable", False))
            await self.repository.fail_stage(run_id, code, str(error), retryable)
            await self.repository.update_task(
                state, status="failed", failure_code=code,
                failure_message=str(error)[:1000],
            )
            raise error

        stage_status = "skipped" if result.skipped else (
            "partial_succeeded" if result.partial_failures else "succeeded"
        )
        result_envelope = {
            "result_ref": result.output_ref,
            "state_update": result.state_update,
            "quality_flags": result.quality_flags,
            "partial_failures": result.partial_failures,
            "confirmation": result.confirmation,
        }
        if not reused:
            await self.repository.finish_stage(run_id, stage_status, result_envelope)
        if result.partial_failures and not reused:
            await self.repository.save_partial_failures(state, run_id, result.partial_failures)
        checkpoint_state: FurniScopeGraphState = {
            **state,
            **result.state_update,
            "internal_stage": spec.stage,
            "external_stage": external,
            "progress_percent": progress,
        }
        await self.repository.save_business_checkpoint(checkpoint_state, spec.stage, True)

        stage_results = dict(state.get("stage_results", {}))
        stage_results[spec.code] = result.output_ref
        update: dict[str, Any] = {
            **result.state_update,
            "status": "partial_succeeded" if result.partial_failures else "running",
            "internal_stage": spec.stage,
            "external_stage": external,
            "progress_percent": progress,
            "stage_results": stage_results,
            "route_to_confirmation": result.confirmation is not None,
            "confirmation_request": result.confirmation,
        }
        if result.quality_flags:
            update["quality_flags"] = result.quality_flags
        if result.partial_failures:
            update["partial_failures"] = result.partial_failures
        return update

    async def i00(self, state: FurniScopeGraphState) -> dict[str, Any]:
        task = await self.repository.create_or_load_task(state)
        version_bundle = {
            "ontology_version": task["ontology_version"],
            "scoring_version": task["scoring_version"],
            "prompt_bundle_version": task["prompt_bundle_version"],
            "model_route_version": task["model_route_version"],
        }
        seeded = {**state, "version_bundle": version_bundle}
        update = await self._run(seeded, SPECS["I00"])
        update["version_bundle"] = version_bundle
        return update

    async def i01(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I01"])
    async def i02(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I02"])
    async def i03(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I03"])
    async def i04(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I04"])
    async def i05(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I05"])
    async def i06(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I06"])
    async def i08(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I08"])
    async def i10(self, state: FurniScopeGraphState) -> dict[str, Any]:
        return await self._run(state, SPECS["I10"], {"batch_results": state.get("review_batch_results", [])})
    async def i11(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I11"])
    async def i12(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I12"])
    async def i13(self, state: FurniScopeGraphState) -> dict[str, Any]:
        return await self._run(state, SPECS["I13"], {"analytics_results": state.get("analytics_results", [])})
    async def i14(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I14"])
    async def i17(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I17"])
    async def i18(self, state: FurniScopeGraphState) -> dict[str, Any]: return await self._run(state, SPECS["I18"])

    async def review_batch(self, state: FurniScopeGraphState) -> dict[str, Any]:
        payload = state["review_batch"]
        update = await self._run(state, SPECS["I09"], payload)
        result_ref = update["stage_results"]["I09"]
        return {"review_batch_results": [{"batch": payload, "result_ref": result_ref}]}

    async def price_analytics(self, state: FurniScopeGraphState) -> dict[str, Any]:
        update = await self._run(state, SPECS["I12A"])
        return {"analytics_results": [{"branch": "price_competition", "result_ref": update["stage_results"]["I12A"]}]}

    async def trend_analytics(self, state: FurniScopeGraphState) -> dict[str, Any]:
        update = await self._run(state, SPECS["I12B"])
        return {"analytics_results": [{"branch": "trend", "result_ref": update["stage_results"]["I12B"]}]}

    async def strategy_unit(self, state: FurniScopeGraphState) -> dict[str, Any]:
        payload = state["strategy_unit"]
        update = await self._run(state, SPECS["I15"], payload)
        return {"strategy_results": [{"unit": payload, "result_ref": update["stage_results"]["I15"]}]}

    async def i15_reduce(self, state: FurniScopeGraphState) -> dict[str, Any]:
        result = await self.tools.execute(
            "strategy_reduce", state, {"strategy_results": state.get("strategy_results", [])}
        )
        return {
            **result.state_update,
            "route_to_confirmation": result.confirmation is not None,
            "confirmation_request": result.confirmation,
            "quality_flags": result.quality_flags,
            "partial_failures": result.partial_failures,
        }

    async def confirmation_gate(self, state: FurniScopeGraphState) -> dict[str, Any]:
        confirmation = state.get("confirmation_request")
        if not confirmation:
            return {"route_to_confirmation": False}
        # One database transaction publishes every user-visible waiting artifact.
        # The LangGraph interrupt follows only after that transaction commits.
        await self.repository.begin_confirmation_wait(state, confirmation)
        resume = interrupt(confirmation)
        if resume.get("confirmation_id") != confirmation["confirmation_id"]:
            raise ValueError("Resumed confirmation_id does not match interrupted state")
        if resume.get("checkpoint_stage") != confirmation["checkpoint_stage"]:
            raise ValueError("Resumed checkpoint_stage does not match interrupted state")
        return {
            "status": "running",
            "user_confirmation": None,
            "confirmation_request": None,
            "route_to_confirmation": False,
            "stage_results": {
                **state.get("stage_results", {}),
                "user_confirmation": {"resume": resume},
            },
        }

    async def target_complete(self, state: FurniScopeGraphState) -> dict[str, Any]:
        """Finish an intentionally scoped Plane run without fabricating a report."""
        await self.repository.update_task(
            state,
            status="partial_succeeded",
            external_stage=state["external_stage"],
            internal_stage=state["internal_stage"],
            progress_percent=state["progress_percent"],
        )
        return {"status": "partial_succeeded"}

    async def i19(self, state: FurniScopeGraphState) -> dict[str, Any]:
        update = await self._run(state, SPECS["I19"])
        report_ref = update.get("report_ref") or state.get("report_ref", {})
        persisted = await self.repository.final_persist(state, report_ref)
        return {
            **update,
            "status": "succeeded", "external_stage": "completed",
            "internal_stage": "completed", "progress_percent": 100,
            "report_ref": persisted,
        }
