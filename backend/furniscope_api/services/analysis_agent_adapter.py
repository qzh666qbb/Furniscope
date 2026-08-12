"""Adapter between FastAPI task dispatch and the existing LangGraph engine."""

from __future__ import annotations

import asyncpg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from backend.furniscope_agent.graph import FurniScopeAgentEngine, build_graph
from backend.furniscope_agent.repository import PostgresWorkflowRepository
from backend.furniscope_agent.synthetic_toolbox import SyntheticFurnitureToolbox

from ..config import ApiSettings


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class AnalysisAgentAdapter:
    """Development/test in-process dispatcher; production uses an external worker."""

    demo_only = True

    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    async def run(self, *, tenant_id: int, task_id: int, task_uuid: str) -> dict:
        if self.settings.analysis_worker_mode != "demo_only" or self.settings.app_env == "production":
            raise RuntimeError("In-process synthetic analysis worker is unavailable")
        pool = await asyncpg.create_pool(_asyncpg_dsn(self.settings.database_url), min_size=1, max_size=5)
        try:
            row = await pool.fetchrow(
                """SELECT product_id,product_profile_version_id,dataset_id,target_country,
                          target_platform,analysis_currency,analysis_config,status
                     FROM furniscope.analysis_tasks
                    WHERE id=$1 AND tenant_id=$2 AND task_uuid=$3::uuid""",
                task_id, tenant_id, task_uuid,
            )
            if row is None or row["status"] not in {"queued", "running", "partial_succeeded"}:
                raise RuntimeError("Queued tenant-scoped analysis task is unavailable")
            repository = PostgresWorkflowRepository(pool)
            async with AsyncPostgresSaver.from_conn_string(self.settings.langgraph_database_url) as checkpointer:
                await checkpointer.setup()
                graph = build_graph(repository, SyntheticFurnitureToolbox(pool), checkpointer=checkpointer)
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
                    "analysis_config": row["analysis_config"], "quality_flags": [],
                    "stage_results": {}, "partial_failures": [], "cancel_requested": False,
                    "review_batch_results": [], "strategy_results": [], "analytics_results": [],
                }
                return await FurniScopeAgentEngine(graph, repository).run(state)
        except Exception as exc:
            await pool.execute(
                """UPDATE furniscope.analysis_tasks
                      SET status='failed',failure_code='WORKER_EXECUTION_FAILED',
                          failure_message=$3,updated_at=now()
                    WHERE id=$1 AND tenant_id=$2 AND status NOT IN ('succeeded','cancelled')""",
                task_id, tenant_id, f"{type(exc).__name__}: analysis worker failed"[:1000],
            )
            raise
        finally:
            await pool.close()
