"""Adapter between FastAPI task dispatch and the existing LangGraph engine."""

from __future__ import annotations

import asyncpg
import json
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from backend.furniscope_agent.graph import ConfirmationResumeWorker, FurniScopeAgentEngine, build_graph
from backend.furniscope_agent.repository import PostgresWorkflowRepository
from backend.furniscope_agent.synthetic_toolbox import (
    SyntheticFurnitureToolbox, TokenPlanDemoToolbox,
)
from backend.furniscope_agent.external_toolbox import ExternalFurnitureToolbox

from ..config import ApiSettings
from .model_router_client import ServiceModelRouterClient


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class AnalysisAgentAdapter:
    """Development/test in-process dispatcher; production uses an external worker."""

    demo_only = True

    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    def _toolbox(self, pool: asyncpg.Pool, model_client: ServiceModelRouterClient | None = None):
        if self.settings.analysis_worker_mode == "external":
            if self.settings.analysis_tool_mode != "external":
                raise RuntimeError("External worker requires ANALYSIS_TOOL_MODE=external")
            return ExternalFurnitureToolbox(pool, model_client=model_client)
        if self.settings.analysis_worker_mode == "token_plan_demo":
            routes = self.settings.chat_provider_routes()
            if not routes:
                raise RuntimeError("Token Plan API key is not configured")
            primary = routes[0]
            fallback = routes[1] if len(routes) > 1 else None
            return TokenPlanDemoToolbox(
                pool,
                base_url=primary["base_url"],
                api_key=primary["api_key"],
                chat_path=primary["chat_path"],
                model_id=primary["model_id"],
                timeout_seconds=self.settings.aliyun_model_router_timeout_seconds,
                max_retries=self.settings.aliyun_model_router_max_retries,
                fallback_base_url=None if fallback is None else fallback["base_url"],
                fallback_api_key=None if fallback is None else fallback["api_key"],
                fallback_chat_path=None if fallback is None else fallback["chat_path"],
                fallback_model_id=None if fallback is None else fallback["model_id"],
            )
        return SyntheticFurnitureToolbox(pool)

    async def run(self, *, tenant_id: int, task_id: int, task_uuid: str) -> dict:
        if self.settings.analysis_worker_mode not in {"demo_only", "token_plan_demo", "external"}:
            raise RuntimeError("In-process synthetic analysis worker is unavailable")
        if self.settings.app_env == "production" and self.settings.analysis_worker_mode != "external":
            raise RuntimeError("Synthetic analysis workers are unavailable in production")
        pool = await asyncpg.create_pool(_asyncpg_dsn(self.settings.database_url), min_size=1, max_size=5)
        model_client = (
            ServiceModelRouterClient(self.settings)
            if self.settings.analysis_worker_mode == "external"
            and self.settings.has_model_router_key()
            else None
        )
        try:
            row = await pool.fetchrow(
                """SELECT product_id,product_profile_version_id,dataset_id,target_country,
                          target_platform,analysis_currency,analysis_config,status,
                          enterprise_profile_snapshot
                     FROM furniscope.analysis_tasks
                    WHERE id=$1 AND tenant_id=$2 AND task_uuid=$3::uuid""",
                task_id, tenant_id, task_uuid,
            )
            if row is None or row["status"] not in {"queued", "running", "partial_succeeded"}:
                raise RuntimeError("Queued tenant-scoped analysis task is unavailable")
            analysis_config = row["analysis_config"]
            if isinstance(analysis_config, str):
                analysis_config = json.loads(analysis_config)
            if not isinstance(analysis_config, dict):
                raise RuntimeError("Analysis task configuration is not a JSON object")
            repository = PostgresWorkflowRepository(pool)
            async with AsyncPostgresSaver.from_conn_string(self.settings.langgraph_database_url) as checkpointer:
                await checkpointer.setup()
                toolbox = self._toolbox(pool, model_client)
                graph = build_graph(repository, toolbox, checkpointer=checkpointer)
                state = {
                    "task_id": task_id, "task_uuid": task_uuid, "tenant_id": tenant_id,
                    "status": row["status"], "external_stage": "understanding_product",
                    "internal_stage": "task_initializing", "progress_percent": 0,
                    "product_id": row["product_id"],
                    "product_profile_version": row["product_profile_version_id"],
                    "dataset_id": row["dataset_id"],
                    "target_market": {"country": row["target_country"],
                                      "platform": row["target_platform"],
                                      "currency": row["analysis_currency"]},
                    "analysis_config": analysis_config, "quality_flags": [],
                    "enterprise_profile_snapshot": (
                        json.loads(row["enterprise_profile_snapshot"])
                        if isinstance(row["enterprise_profile_snapshot"], str)
                        else (row["enterprise_profile_snapshot"] or {})
                    ),
                    "stage_results": {}, "partial_failures": [], "cancel_requested": False,
                    "review_batch_results": [], "strategy_results": [], "analytics_results": [],
                }
                return await FurniScopeAgentEngine(graph, repository).run(state)
        except Exception as exc:
            await pool.execute(
                """UPDATE furniscope.analysis_tasks
                      SET status='failed',failure_code=COALESCE(failure_code,'WORKER_EXECUTION_FAILED'),
                          failure_message=COALESCE(failure_message,$3),updated_at=now()
                    WHERE id=$1 AND tenant_id=$2 AND status NOT IN ('succeeded','cancelled')""",
                task_id, tenant_id, f"{type(exc).__name__}: analysis worker failed"[:1000],
            )
            raise
        finally:
            if model_client is not None:
                await model_client.close()
            await pool.close()

    async def accept_confirmation(self, *, confirmation_id: str, selected_option: str,
                                  user_input, user_id: int) -> dict:
        pool = await asyncpg.create_pool(
            _asyncpg_dsn(self.settings.database_url), min_size=1, max_size=2,
        )
        try:
            accepted = await PostgresWorkflowRepository(pool).accept_confirmation(
                confirmation_id, selected_option, user_input, user_id,
            )
            return {
                "confirmation_id": confirmation_id,
                "event_uuid": accepted.event_uuid,
                "task_uuid": accepted.task_uuid,
                "status": "queued",
                "already_accepted": accepted.already_accepted,
            }
        finally:
            await pool.close()

    async def resume_next_confirmation(self) -> dict | None:
        if self.settings.analysis_worker_mode not in {"demo_only", "token_plan_demo", "external"}:
            return None
        pool = await asyncpg.create_pool(
            _asyncpg_dsn(self.settings.database_url), min_size=1, max_size=5,
        )
        model_client = (
            ServiceModelRouterClient(self.settings)
            if self.settings.analysis_worker_mode == "external"
            and self.settings.has_model_router_key()
            else None
        )
        try:
            repository = PostgresWorkflowRepository(pool)
            async with AsyncPostgresSaver.from_conn_string(
                self.settings.langgraph_database_url
            ) as checkpointer:
                graph = build_graph(
                    repository, self._toolbox(pool, model_client), checkpointer=checkpointer,
                )
                engine = FurniScopeAgentEngine(graph, repository)
                return await ConfirmationResumeWorker(engine).process_next()
        finally:
            if model_client is not None:
                await model_client.close()
            await pool.close()
