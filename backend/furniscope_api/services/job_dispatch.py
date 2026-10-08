"""Web-process enqueue boundary."""

from typing import Any

from fastapi import FastAPI

from ..errors import BusinessError
from ..job_queue import TenantQueueQuotaExceeded


async def enqueue_job(app: FastAPI, kind: str, payload: dict[str, Any], *, job_id: str) -> None:
    queue = getattr(app.state, "job_queue", None)
    if queue is None:
        raise RuntimeError("enqueue_job called without a configured Redis queue")
    try:
        await queue.enqueue(kind, payload, job_id=job_id)
    except TenantQueueQuotaExceeded as exc:
        raise BusinessError(
            "TENANT_JOB_QUEUE_LIMIT_EXCEEDED",
            "任务已保存，当前企业排队任务已达上限；容量释放后系统会自动补投",
            status_code=429,
            details=[{"tenant_id": exc.tenant_id, "queue_limit": exc.limit}],
        ) from exc
    except Exception as exc:
        app.state.logger.exception("job_enqueue_failed", extra={"kind": kind, "job_id": job_id})
        raise BusinessError(
            "JOB_QUEUE_UNAVAILABLE",
            "任务已保存，但队列暂时不可用；Worker恢复后会自动补投",
            status_code=503,
        ) from exc
