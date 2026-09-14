"""LangGraph topology and engine facade."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send

from .contracts import AgentToolbox, ConfirmationAcceptance, WorkflowRepository
from .nodes import WorkflowNodes
from .state import FurniScopeGraphState


def _confirmation_route(state: FurniScopeGraphState, normal: str) -> str:
    return "confirmation_gate" if state.get("route_to_confirmation") else normal


def _boundary_route(state: FurniScopeGraphState, normal: str, target: str) -> str:
    if state.get("route_to_confirmation"):
        return "confirmation_gate"
    return "target_complete" if state.get("analysis_config", {}).get("target_node") == target else normal


def _after_confirmation(state: FurniScopeGraphState) -> str:
    confirmation = state.get("stage_results", {}).get("user_confirmation", {}).get("resume", {})
    stage = confirmation.get("checkpoint_stage")
    routes = {
        "context_loading": "i03_data_quality",
        "data_quality": "i04_hard_filter",
        "competitor_filtering": "i05_embedding",
        "competitor_reranking": "i08_review_plan",
        "review_extracting": "i11_clustering",
        "market_analytics": "i14_scoring",
        "opportunity_scoring": "strategy_dispatch",
        "strategy_generating": "i17_evidence",
    }
    if stage not in routes:
        raise ValueError(f"Unsupported confirmation checkpoint_stage: {stage}")
    completed_target = {
        "context_loading": "product",
        "market_analytics": "market",
        "opportunity_scoring": "score",
        "evidence_auditing": "plan",
    }.get(stage)
    if completed_target and state.get("analysis_config", {}).get("target_node") == completed_target:
        return "target_complete"
    return routes[stage]


def _review_sends(state: FurniScopeGraphState) -> list[Send] | str:
    batches = state.get("review_batches", [])
    if not batches:
        return "i10_review_reduce"
    return [Send("review_batch", {**state, "review_batch": batch}) for batch in batches]


def _strategy_sends(state: FurniScopeGraphState) -> list[Send] | str:
    units = state.get("strategy_units", [])
    if not units:
        return "i15_strategy_reduce"
    return [Send("strategy_unit", {**state, "strategy_unit": unit}) for unit in units]


def build_graph(
    repository: WorkflowRepository,
    tools: AgentToolbox,
    *,
    checkpointer: Any | None = None,
):
    nodes = WorkflowNodes(repository, tools)
    graph = StateGraph(FurniScopeGraphState)
    graph.add_node("i00_create_freeze", nodes.i00)
    graph.add_node("i01_preflight", nodes.i01)
    graph.add_node("i02_context", nodes.i02)
    graph.add_node("i03_data_quality", nodes.i03)
    graph.add_node("i04_hard_filter", nodes.i04)
    graph.add_node("i05_embedding", nodes.i05)
    graph.add_node("i06_rerank", nodes.i06)
    graph.add_node("confirmation_gate", nodes.confirmation_gate)
    graph.add_node("i08_review_plan", nodes.i08)
    graph.add_node("review_batch", nodes.review_batch)
    graph.add_node("i10_review_reduce", nodes.i10)
    graph.add_node("i11_clustering", nodes.i11)
    graph.add_node("i12_fork", nodes.i12)
    graph.add_node("i12a_price", nodes.price_analytics)
    graph.add_node("i12b_trend", nodes.trend_analytics)
    graph.add_node("i13_join", nodes.i13)
    graph.add_node("i14_scoring", nodes.i14)
    graph.add_node("strategy_dispatch", lambda state: {})
    graph.add_node("strategy_unit", nodes.strategy_unit)
    graph.add_node("i15_strategy_reduce", nodes.i15_reduce)
    graph.add_node("i17_evidence", nodes.i17)
    graph.add_node("i18_report", nodes.i18)
    graph.add_node("i19_persist", nodes.i19)
    graph.add_node("target_complete", nodes.target_complete)

    graph.add_edge(START, "i00_create_freeze")
    graph.add_edge("i00_create_freeze", "i01_preflight")
    graph.add_edge("i01_preflight", "i02_context")
    graph.add_conditional_edges("i02_context", lambda s: _boundary_route(s, "i03_data_quality", "product"))
    graph.add_conditional_edges("i03_data_quality", lambda s: _confirmation_route(s, "i04_hard_filter"))
    graph.add_conditional_edges("i04_hard_filter", lambda s: _confirmation_route(s, "i05_embedding"))
    graph.add_edge("i05_embedding", "i06_rerank")
    graph.add_conditional_edges("i06_rerank", lambda s: _confirmation_route(s, "i08_review_plan"))
    graph.add_conditional_edges("confirmation_gate", _after_confirmation)
    graph.add_conditional_edges("i08_review_plan", _review_sends)
    graph.add_edge("review_batch", "i10_review_reduce")
    graph.add_conditional_edges("i10_review_reduce", lambda s: _confirmation_route(s, "i11_clustering"))
    graph.add_edge("i11_clustering", "i12_fork")
    graph.add_edge("i12_fork", "i12a_price")
    graph.add_edge("i12_fork", "i12b_trend")
    graph.add_edge(["i12a_price", "i12b_trend"], "i13_join")
    graph.add_conditional_edges("i13_join", lambda s: _boundary_route(s, "i14_scoring", "market"))
    graph.add_conditional_edges("i14_scoring", lambda s: _boundary_route(s, "strategy_dispatch", "score"))
    graph.add_conditional_edges("strategy_dispatch", _strategy_sends)
    graph.add_edge("strategy_unit", "i15_strategy_reduce")
    graph.add_conditional_edges("i15_strategy_reduce", lambda s: _confirmation_route(s, "i17_evidence"))
    graph.add_conditional_edges("i17_evidence", lambda s: _boundary_route(s, "i18_report", "plan"))
    graph.add_edge("i18_report", "i19_persist")
    graph.add_edge("i19_persist", END)
    graph.add_edge("target_complete", END)
    return graph.compile(checkpointer=checkpointer)


class FurniScopeAgentEngine:
    def __init__(self, graph: Any, repository: WorkflowRepository) -> None:
        self.graph = graph
        self.repository = repository

    @staticmethod
    def graph_config(task_uuid: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": task_uuid}}

    async def run(self, state: FurniScopeGraphState) -> dict[str, Any]:
        return await self.graph.ainvoke(state, self.graph_config(state["task_uuid"]))

    async def submit_confirmation(
        self,
        *,
        confirmation_id: str,
        selected_option: str,
        user_id: int,
        user_input: Any = None,
    ) -> ConfirmationAcceptance:
        """Accept a user answer transactionally; a worker performs graph recovery."""
        return await self.repository.accept_confirmation(
            confirmation_id, selected_option, user_input, user_id
        )


class ConfirmationResumeWorker:
    """Transactional-outbox consumer for LangGraph Command(resume=...)."""

    def __init__(self, engine: FurniScopeAgentEngine) -> None:
        self.engine = engine

    async def process_next(self) -> dict[str, Any] | None:
        event = await self.engine.repository.claim_resume_event()
        if event is None:
            return None
        try:
            result = await self.engine.graph.ainvoke(
                Command(resume=event.payload),
                self.engine.graph_config(event.task_uuid),
            )
        except Exception as exc:
            await self.engine.repository.fail_resume_event(
                event.event_uuid, f"RESUME_{type(exc).__name__.upper()}"
            )
            raise
        await self.engine.repository.consume_resume_event(event)
        return result
