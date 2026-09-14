"""Web-process enqueue boundary."""

from typing import Any

from fastapi import FastAPI

from ..errors import BusinessError


async def enqueue_job(app: FastAPI, kind: str, payload: dict[str, Any], *, job_id: str) -> None:
    queue = getattr(app.state, "job_queue", None)
    if queue is None:
        raise RuntimeError("enqueue_job called without a configured Redis queue")
    try:
        await queue.enqueue(kind, payload, job_id=job_id)
    except Exception as exc:
        app.state.logger.exception("job_enqueue_failed", extra={"kind": kind, "job_id": job_id})
        raise BusinessError(
            "JOB_QUEUE_UNAVAILABLE",
            "任务已保存，但队列暂时不可用；Worker恢复后会自动补投",
            status_code=503,
        ) from exc
