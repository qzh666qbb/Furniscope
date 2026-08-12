from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver

from backend.furniscope_agent.demo_support import (
    DeterministicDemoToolbox,
    InMemoryWorkflowRepository,
    demo_initial_state,
)
from backend.furniscope_agent.graph import FurniScopeAgentEngine, build_graph
from backend.furniscope_agent.graph import ConfirmationResumeWorker
from backend.furniscope_agent.contracts import CapabilityResult


class ConfirmationDemoToolbox(DeterministicDemoToolbox):
    def __init__(self) -> None:
        self.confirmation_id = str(uuid4())

    async def execute(self, capability, state, payload=None):
        result = await super().execute(capability, state, payload)
        if capability == "data_quality":
            result.confirmation = {
                "confirmation_id": self.confirmation_id,
                "confirmation_type": "insufficient_data",
                "question": "合成样本量较少，是否按当前样本继续？",
                "recommended_option": "continue_with_limit",
                "options": [
                    {"code": "continue_with_limit", "label": "继续并标记限制"},
                    {"code": "stop", "label": "停止"},
                ],
                "evidence_refs": [{"type": "synthetic_fixture", "id": "SYN-001"}],
                "impact": {"confidence_cap": 0.7},
                "checkpoint_stage": "data_quality",
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            }
        return result


class AgentGraphTest(unittest.TestCase):
    def test_complete_workflow_reaches_documented_terminal_state(self) -> None:
        async def scenario() -> None:
            repository = InMemoryWorkflowRepository()
            graph = build_graph(repository, DeterministicDemoToolbox(), checkpointer=InMemorySaver())
            engine = FurniScopeAgentEngine(graph, repository)
            result = await engine.run(demo_initial_state())
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["external_stage"], "completed")
            self.assertEqual(result["internal_stage"], "completed")
            self.assertEqual(result["progress_percent"], 100)
            self.assertTrue(result["report_ref"]["report_uuid"])
            self.assertTrue(all(
                run["status"] in {"succeeded", "skipped", "partial_succeeded"}
                for run in repository.stage_runs
            ))

        asyncio.run(scenario())

    def test_confirmation_answer_is_outboxed_then_worker_resumes(self) -> None:
        async def scenario() -> None:
            repository = InMemoryWorkflowRepository()
            tools = ConfirmationDemoToolbox()
            graph = build_graph(repository, tools, checkpointer=InMemorySaver())
            engine = FurniScopeAgentEngine(graph, repository)
            interrupted = await engine.run(demo_initial_state())
            self.assertIn("__interrupt__", interrupted)
            confirmation = repository.confirmations[tools.confirmation_id]
            self.assertEqual(confirmation["status"], "pending")
            self.assertEqual(repository.stage_runs[-1]["status"], "waiting_human")

            accepted = await engine.submit_confirmation(
                confirmation_id=tools.confirmation_id,
                selected_option="continue_with_limit",
                user_id=1,
                user_input={"synthetic_demo": True},
            )
            self.assertFalse(accepted.already_accepted)
            self.assertEqual(repository.control_events[0]["status"], "pending")
            self.assertEqual(accepted.payload["checkpoint_stage"], "data_quality")

            result = await ConfirmationResumeWorker(engine).process_next()
            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(repository.control_events[0]["status"], "consumed")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
