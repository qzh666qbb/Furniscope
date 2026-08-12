"""Run a local orchestration-only smoke demo: python -m backend.furniscope_agent.demo"""

from __future__ import annotations

import asyncio

from langgraph.checkpoint.memory import InMemorySaver

from .demo_support import DeterministicDemoToolbox, InMemoryWorkflowRepository, demo_initial_state
from .graph import FurniScopeAgentEngine, build_graph


async def main() -> None:
    repository = InMemoryWorkflowRepository()
    graph = build_graph(repository, DeterministicDemoToolbox(), checkpointer=InMemorySaver())
    engine = FurniScopeAgentEngine(graph, repository)
    result = await engine.run(demo_initial_state())
    print({
        "status": result["status"],
        "external_stage": result["external_stage"],
        "progress_percent": result["progress_percent"],
        "report_uuid": result["report_ref"]["report_uuid"],
        "stage_run_count": len(repository.stage_runs),
    })


if __name__ == "__main__":
    asyncio.run(main())
