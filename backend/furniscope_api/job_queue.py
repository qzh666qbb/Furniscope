"""Redis Streams job queue with consumer leases, retries and a dead-letter stream."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from .config import ApiSettings

STREAM = "furniscope:jobs"
DEAD_STREAM = "furniscope:jobs:dead"
GROUP = "furniscope-workers"


@dataclass(frozen=True, slots=True)
class QueuedJob:
    message_id: str
    job_id: str
    kind: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int


class RedisJobQueue:
    def __init__(self, client: Redis, settings: ApiSettings) -> None:
        self.client = client
        self.settings = settings

    @classmethod
    def from_settings(cls, settings: ApiSettings) -> "RedisJobQueue":
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is not configured")
        # XREADGROUP blocks for worker_block_ms. Keep the socket timeout above
        # that interval so an empty queue is returned normally instead of
        # raising redis.exceptions.TimeoutError on every poll.
        socket_timeout = max(10.0, settings.worker_block_ms / 1000 + 5.0)
        return cls(
            Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=socket_timeout,
            ),
            settings,
        )

    async def ensure_group(self) -> None:
        try:
            await self.client.xgroup_create(STREAM, GROUP, id="0-0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def ping(self) -> None:
        await self.client.ping()

    async def close(self) -> None:
        await self.client.aclose()

    async def archive_dead_letters(self, archive_path: Path) -> int:
        target = Path(archive_path)
        entries = await self.client.xrange(DEAD_STREAM)
        payload = [{"id": message_id, "fields": fields} for message_id, fields in entries]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if entries:
            await self.client.delete(DEAD_STREAM)
        return len(payload)

    async def enqueue(self, kind: str, payload: dict[str, Any], *, job_id: str | None = None,
                      attempt: int = 1, deduplicate: bool = True) -> str:
        durable_id = job_id or f"{kind}:{uuid4()}"
        dedupe_key = f"furniscope:job-dedupe:{durable_id}"
        claimed = False
        if deduplicate:
            claimed = bool(await self.client.set(dedupe_key, "1", ex=604800, nx=True))
            if not claimed:
                return durable_id
        fields = {
            "job_id": durable_id, "kind": kind,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "attempt": str(attempt), "max_attempts": str(self.settings.worker_max_attempts),
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self.client.xadd(STREAM, fields)
        except Exception:
            if claimed:
                await self.client.delete(dedupe_key)
            raise
        return durable_id

    @staticmethod
    def _decode(message_id: str, fields: dict[str, str]) -> QueuedJob:
        payload = json.loads(fields["payload"])
        if not isinstance(payload, dict):
            raise ValueError("job payload must be an object")
        return QueuedJob(
            message_id=str(message_id), job_id=fields["job_id"], kind=fields["kind"],
            payload=payload, attempt=int(fields.get("attempt", "1")),
            max_attempts=int(fields.get("max_attempts", "1")),
        )

    async def _next(self, consumer: str) -> tuple[str, dict[str, str]] | None:
        claimed = await self.client.xautoclaim(
            STREAM, GROUP, consumer, min_idle_time=self.settings.worker_lease_ms,
            start_id="0-0", count=1,
        )
        claimed_messages = claimed[1] if len(claimed) > 1 else []
        if claimed_messages:
            return claimed_messages[0]
        batches = await self.client.xreadgroup(
            GROUP, consumer, {STREAM: ">"}, count=1, block=self.settings.worker_block_ms,
        )
        if not batches:
            return None
        return batches[0][1][0]

    async def consume_once(self, consumer: str,
                           handler: Callable[[QueuedJob], Awaitable[None]],
                           retry_hook: Callable[[QueuedJob, Exception], Awaitable[None]],
                           failure_hook: Callable[[QueuedJob, Exception], Awaitable[None]]) -> bool:
        raw = await self._next(consumer)
        if raw is None:
            return False
        message_id, fields = raw
        try:
            job = self._decode(message_id, fields)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            await self.client.xadd(DEAD_STREAM, {
                "job_id": str(fields.get("job_id", "malformed"))[:200],
                "kind": str(fields.get("kind", "unknown"))[:100],
                "attempt": str(fields.get("attempt", "0"))[:20],
                "error_type": type(exc).__name__,
                "failure_code": "MALFORMED_JOB_MESSAGE",
                "failed_at": datetime.now(timezone.utc).isoformat(),
            })
            await self.client.xack(STREAM, GROUP, message_id)
            return True
        try:
            await asyncio.wait_for(
                handler(job), timeout=self.settings.worker_job_timeout_seconds,
            )
        except Exception as exc:
            if job.attempt < job.max_attempts:
                await retry_hook(job, exc)
                await self.enqueue(job.kind, job.payload, job_id=job.job_id,
                                   attempt=job.attempt + 1, deduplicate=False)
            else:
                await failure_hook(job, exc)
                await self.client.xadd(DEAD_STREAM, {
                    "job_id": job.job_id, "kind": job.kind,
                    "payload": json.dumps(job.payload, ensure_ascii=False),
                    "attempt": str(job.attempt), "error_type": type(exc).__name__,
                    "failure_code": "JOB_ATTEMPTS_EXHAUSTED",
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                })
            await self.client.xack(STREAM, GROUP, job.message_id)
            return True
        await self.client.xack(STREAM, GROUP, job.message_id)
        return True
