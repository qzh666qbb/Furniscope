"""Enqueue HeFeng scoped analysis runs that stop at score and plan."""

from __future__ import annotations

import asyncio
import sys
from uuid import uuid4

from furniscope_api.config import get_settings
from furniscope_api.database import Database
from furniscope_api.job_queue import RedisJobQueue
from furniscope_api.services.analysis_task_service import AnalysisTaskService

TENANT_ID = 1
USER_ID = 1
PRODUCT_ID = 76
PROFILE_VERSION_ID = 76
DATASET_ID = 2


async def _enqueue(node: str) -> dict:
    settings = get_settings()
    database = Database(settings)
    queue = RedisJobQueue.from_settings(settings)
    service = AnalysisTaskService(settings)
    payload = {
        "job_name": f"R08 scoped {node} HF-A0396-1",
        "job_type": "product_market_fit",
        "product_id": PRODUCT_ID,
        "product_profile_version_id": PROFILE_VERSION_ID,
        "dataset_id": DATASET_ID,
        "target_country": "US",
        "target_platform": "amazon",
        "analysis_currency": "USD",
        "analysis_config": {
            "source": "node_workflow_canvas",
            "data_class": "authorized_market_data",
            "target_node": node,
            "trace_evidence": True,
            "include_forecast": False,
        },
    }

    def envelope(data):
        return data

    try:
        async with database.session_factory() as session:
            _, created = await service.create(
                session,
                tenant_id=TENANT_ID,
                user_id=USER_ID,
                idempotency_key=f"r08-{node}-{uuid4()}",
                payload=payload,
                response_envelope=envelope,
                request_id=f"r08-{node}-{uuid4()}",
            )
            task_uuid = str(created["task_uuid"])
            _, _, dispatch = await service.start(
                session,
                tenant_id=TENANT_ID,
                user_id=USER_ID,
                task_uuid=task_uuid,
                idempotency_key=f"r08-start-{node}-{uuid4()}",
                response_envelope=envelope,
            )
            await session.commit()
        if dispatch is None:
            raise RuntimeError(f"{node} start returned no dispatch")
        await queue.enqueue("analysis", dispatch, job_id=f"analysis:{dispatch['task_uuid']}")
        return dispatch
    finally:
        await queue.close()
        await database.close()


async def main() -> None:
    nodes = sys.argv[1:] or ["score", "plan"]
    for node in nodes:
        if node not in {"score", "plan"}:
            raise SystemExit(f"unsupported node: {node}")
        dispatch = await _enqueue(node)
        print(f"{node} queued task_id={dispatch['task_id']} task_uuid={dispatch['task_uuid']}")


if __name__ == "__main__":
    asyncio.run(main())
