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
TENANT_REGISTRY = "furniscope:tenant-queue-tenants"
TENANT_METRICS = "furniscope:tenant-job-events"


class JobLeaseLost(RuntimeError):
    """Another consumer owns delivery; this consumer must not retry or ACK it."""


class TenantQueueQuotaExceeded(RuntimeError):
    """A tenant has reached its configured queued-job limit."""

    def __init__(self, tenant_id: str, limit: int) -> None:
        super().__init__(f"tenant {tenant_id} queue limit {limit} exceeded")
        self.tenant_id = tenant_id
        self.limit = limit


@dataclass(frozen=True, slots=True)
class QueuedJob:
    message_id: str
    job_id: str
    kind: str
    payload: dict[str, Any]
    attempt: int
    max_attempts: int
    tenant_key: str = "system"
    backlog_counted: bool = False


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

    @staticmethod
    def _tenant_key(payload: dict[str, Any]) -> str:
        value = payload.get("tenant_id")
        if isinstance(value, bool):
            return "system"
        try:
            tenant_id = int(value)
        except (TypeError, ValueError):
            return "system"
        return str(tenant_id) if tenant_id > 0 else "system"

    def _message_fields(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        job_id: str,
        attempt: int,
        tenant_key: str,
    ) -> dict[str, str]:
        return {
            "job_id": job_id,
            "kind": kind,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "attempt": str(attempt),
            "max_attempts": str(self.settings.worker_max_attempts),
            "tenant_id": tenant_key,
            "backlog_counted": "1",
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _field_arguments(fields: dict[str, str]) -> list[str]:
        return [item for pair in fields.items() for item in pair]

    @staticmethod
    def _queued_key(tenant_key: str) -> str:
        return f"furniscope:tenant-queued:{tenant_key}"

    @staticmethod
    def _running_key(tenant_key: str) -> str:
        return f"furniscope:tenant-running:{tenant_key}"

    async def enqueue(self, kind: str, payload: dict[str, Any], *, job_id: str | None = None,
                      attempt: int = 1, deduplicate: bool = True,
                      _preserve_backlog: bool = False) -> str:
        durable_id = job_id or f"{kind}:{uuid4()}"
        dedupe_key = f"furniscope:job-dedupe:{durable_id}"
        tenant_key = self._tenant_key(payload)
        fields = self._message_fields(
            kind, payload, job_id=durable_id, attempt=attempt, tenant_key=tenant_key,
        )
        result = await self.client.eval("""
            if ARGV[1] == '1' and redis.call('EXISTS', KEYS[2]) == 1 then
                return 0
            end
            if ARGV[3] == '0' then
                local current = tonumber(redis.call('GET', KEYS[3]) or '0')
                if current >= tonumber(ARGV[4]) then return -1 end
            end
            redis.call('XADD', KEYS[1], '*', unpack(ARGV, 6))
            if ARGV[1] == '1' then
                redis.call('SET', KEYS[2], '1', 'EX', ARGV[2])
            end
            if ARGV[3] == '0' then redis.call('INCR', KEYS[3]) end
            redis.call('SADD', KEYS[4], ARGV[5])
            return 1
        """, 4, STREAM, dedupe_key, self._queued_key(tenant_key), TENANT_REGISTRY,
            "1" if deduplicate else "0", 604800, "1" if _preserve_backlog else "0",
            self.settings.worker_tenant_queue_limit, tenant_key,
            *self._field_arguments(fields))
        if int(result) == -1:
            raise TenantQueueQuotaExceeded(
                tenant_key, self.settings.worker_tenant_queue_limit,
            )
        return durable_id

    @staticmethod
    def _decode(message_id: str, fields: dict[str, str]) -> QueuedJob:
        payload = json.loads(fields["payload"])
        if not isinstance(payload, dict):
            raise ValueError("job payload must be an object")
        tenant_key = RedisJobQueue._tenant_key(payload)
        if fields.get("tenant_id", tenant_key) != tenant_key:
            raise ValueError("job tenant metadata does not match payload")
        return QueuedJob(
            message_id=str(message_id), job_id=fields["job_id"], kind=fields["kind"],
            payload=payload, attempt=int(fields.get("attempt", "1")),
            max_attempts=int(fields.get("max_attempts", "1")),
            tenant_key=tenant_key,
            backlog_counted=fields.get("backlog_counted") == "1",
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

    async def _acquire_tenant_slot(self, tenant_key: str, token: str) -> bool:
        acquired = await self.client.eval("""
            local now_parts = redis.call('TIME')
            local now = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)
            redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
            if not redis.call('ZSCORE', KEYS[1], ARGV[1])
               and redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[2]) then
                return 0
            end
            redis.call('ZADD', KEYS[1], now + tonumber(ARGV[3]), ARGV[1])
            redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[3]) * 2)
            return 1
        """, 1, self._running_key(tenant_key), token,
            self.settings.worker_tenant_max_in_flight, self.settings.worker_lease_ms)
        return bool(acquired)

    async def _release_tenant_slot(self, tenant_key: str, token: str) -> None:
        await self.client.zrem(self._running_key(tenant_key), token)

    async def _renew(
        self,
        consumer: str,
        message_id: str,
        tenant_key: str,
        slot_token: str,
    ) -> None:
        # Delivery ownership and tenant-slot renewal are one lease contract.
        owned = await self.client.eval("""
            local p = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1)
            if #p == 0 or p[1][2] ~= ARGV[2] then return 0 end
            if not redis.call('ZSCORE', KEYS[2], ARGV[4]) then return 0 end
            local now_parts = redis.call('TIME')
            local now = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)
            redis.call('ZADD', KEYS[2], now + tonumber(ARGV[5]), ARGV[4])
            redis.call('PEXPIRE', KEYS[2], tonumber(ARGV[5]) * 2)
            redis.call('XCLAIM', KEYS[1], ARGV[1], ARGV[2], 0, ARGV[3], 'JUSTID')
            return 1
        """, 2, STREAM, self._running_key(tenant_key),
            GROUP, consumer, message_id, slot_token, self.settings.worker_lease_ms)
        if not owned:
            raise JobLeaseLost("Queue delivery or tenant-slot lease lost")

    async def _run_with_lease(self, consumer, job, slot_token, handler):
        async def heartbeat():
            while True:
                await asyncio.sleep(self.settings.worker_lease_ms / 3000)
                try:
                    await self._renew(
                        consumer, job.message_id, job.tenant_key, slot_token,
                    )
                except Exception as exc:
                    raise JobLeaseLost("Queue lease renewal failed") from exc

        work, pulse = asyncio.create_task(handler(job)), asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait((work, pulse), return_when=asyncio.FIRST_COMPLETED)
            await (pulse if pulse in done else work)
        finally:
            work.cancel()
            pulse.cancel()
            await asyncio.gather(work, pulse, return_exceptions=True)

    async def _defer(
        self,
        consumer: str,
        message_id: str,
        fields: dict[str, str],
        tenant_key: str,
    ) -> None:
        deferred = dict(fields)
        deferred["defer_count"] = str(int(fields.get("defer_count", "0")) + 1)
        owned = await self.client.eval("""
            local p = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1)
            if #p == 0 or p[1][2] ~= ARGV[2] then return 0 end
            redis.call('XADD', KEYS[1], '*', unpack(ARGV, 5))
            redis.call('XACK', KEYS[1], ARGV[1], ARGV[3])
            redis.call('HINCRBY', KEYS[2], ARGV[4] .. ':deferred', 1)
            return 1
        """, 2, STREAM, TENANT_METRICS, GROUP, consumer, message_id, tenant_key,
            *self._field_arguments(deferred))
        if not owned:
            raise JobLeaseLost("Queue delivery lease lost while deferring")

    async def _retry_and_ack(
        self,
        consumer: str,
        job: QueuedJob,
    ) -> None:
        fields = self._message_fields(
            job.kind,
            job.payload,
            job_id=job.job_id,
            attempt=job.attempt + 1,
            tenant_key=job.tenant_key,
        )
        owned = await self.client.eval("""
            local p = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1)
            if #p == 0 or p[1][2] ~= ARGV[2] then return 0 end
            redis.call('XADD', KEYS[1], '*', unpack(ARGV, 4))
            redis.call('XACK', KEYS[1], ARGV[1], ARGV[3])
            return 1
        """, 1, STREAM, GROUP, consumer, job.message_id,
            *self._field_arguments(fields))
        if not owned:
            raise JobLeaseLost("Queue delivery lease lost while retrying")

    async def _finish(
        self,
        consumer: str,
        job: QueuedJob,
        event: str,
    ) -> None:
        owned = await self.client.eval("""
            local p = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1)
            if #p == 0 or p[1][2] ~= ARGV[2] then return 0 end
            redis.call('XACK', KEYS[1], ARGV[1], ARGV[3])
            if ARGV[5] == '1' then
                local current = tonumber(redis.call('GET', KEYS[2]) or '0')
                if current > 0 then redis.call('DECR', KEYS[2]) end
            end
            redis.call('HINCRBY', KEYS[3], ARGV[4] .. ':' .. ARGV[6], 1)
            return 1
        """, 3, STREAM, self._queued_key(job.tenant_key), TENANT_METRICS,
            GROUP, consumer, job.message_id, job.tenant_key,
            "1" if job.backlog_counted else "0", event)
        if not owned:
            raise JobLeaseLost("Queue delivery lease lost while completing")

    async def _dead_letter_and_finish(
        self,
        consumer: str,
        job: QueuedJob,
        fields: dict[str, str],
    ) -> None:
        owned = await self.client.eval("""
            local p = redis.call('XPENDING', KEYS[1], ARGV[1], ARGV[3], ARGV[3], 1)
            if #p == 0 or p[1][2] ~= ARGV[2] then return 0 end
            redis.call('XADD', KEYS[2], '*', unpack(ARGV, 6))
            redis.call('XACK', KEYS[1], ARGV[1], ARGV[3])
            if ARGV[5] == '1' then
                local current = tonumber(redis.call('GET', KEYS[3]) or '0')
                if current > 0 then redis.call('DECR', KEYS[3]) end
            end
            redis.call('HINCRBY', KEYS[4], ARGV[4] .. ':failed', 1)
            return 1
        """, 4, STREAM, DEAD_STREAM, self._queued_key(job.tenant_key), TENANT_METRICS,
            GROUP, consumer, job.message_id, job.tenant_key,
            "1" if job.backlog_counted else "0", *self._field_arguments(fields))
        if not owned:
            raise JobLeaseLost("Queue delivery lease lost while dead-lettering")

    async def tenant_metrics_snapshot(self) -> list[dict[str, int | str]]:
        tenants = sorted(await self.client.smembers(TENANT_REGISTRY))
        if not tenants:
            return []
        seconds, microseconds = await self.client.time()
        now_ms = int(seconds) * 1000 + int(microseconds) // 1000
        pipe = self.client.pipeline(transaction=False)
        for tenant_key in tenants:
            pipe.get(self._queued_key(tenant_key))
            pipe.zcount(self._running_key(tenant_key), now_ms + 1, "+inf")
            for event in ("deferred", "completed", "failed"):
                pipe.hget(TENANT_METRICS, f"{tenant_key}:{event}")
        values = await pipe.execute()
        snapshots = []
        for index, tenant_key in enumerate(tenants):
            offset = index * 5
            snapshots.append({
                "tenant_id": tenant_key,
                "queued": int(values[offset] or 0),
                "running": int(values[offset + 1] or 0),
                "deferred": int(values[offset + 2] or 0),
                "completed": int(values[offset + 3] or 0),
                "failed": int(values[offset + 4] or 0),
            })
        return snapshots

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
            raw_tenant = fields.get("tenant_id", "system")
            tenant_key = (
                raw_tenant
                if raw_tenant == "system" or raw_tenant.isdigit() and int(raw_tenant) > 0
                else "system"
            )
            malformed = QueuedJob(
                message_id=str(message_id),
                job_id=str(fields.get("job_id", "malformed"))[:200],
                kind=str(fields.get("kind", "unknown"))[:100],
                payload={},
                attempt=0,
                max_attempts=0,
                tenant_key=tenant_key,
                backlog_counted=fields.get("backlog_counted") == "1",
            )
            await self._dead_letter_and_finish(consumer, malformed, {
                "job_id": str(fields.get("job_id", "malformed"))[:200],
                "kind": str(fields.get("kind", "unknown"))[:100],
                "attempt": str(fields.get("attempt", "0"))[:20],
                "error_type": type(exc).__name__,
                "failure_code": "MALFORMED_JOB_MESSAGE",
                "failed_at": datetime.now(timezone.utc).isoformat(),
            })
            return True
        slot_token = f"{consumer}:{job.message_id}"
        if not await self._acquire_tenant_slot(job.tenant_key, slot_token):
            await self._defer(consumer, job.message_id, fields, job.tenant_key)
            await asyncio.sleep(self.settings.worker_tenant_defer_ms / 1000)
            return True
        try:
            try:
                await asyncio.wait_for(
                    self._run_with_lease(consumer, job, slot_token, handler),
                    timeout=self.settings.worker_job_timeout_seconds,
                )
            except JobLeaseLost:
                raise
            except Exception as exc:
                await self._renew(
                    consumer, job.message_id, job.tenant_key, slot_token,
                )
                if job.attempt < job.max_attempts:
                    await retry_hook(job, exc)
                    await self._retry_and_ack(consumer, job)
                else:
                    await failure_hook(job, exc)
                    await self._dead_letter_and_finish(consumer, job, {
                        "job_id": job.job_id,
                        "kind": job.kind,
                        "payload": json.dumps(job.payload, ensure_ascii=False),
                        "attempt": str(job.attempt),
                        "error_type": type(exc).__name__,
                        "failure_code": "JOB_ATTEMPTS_EXHAUSTED",
                        "failed_at": datetime.now(timezone.utc).isoformat(),
                    })
                return True
            await self._renew(
                consumer, job.message_id, job.tenant_key, slot_token,
            )
            await self._finish(consumer, job, "completed")
            return True
        finally:
            await self._release_tenant_slot(job.tenant_key, slot_token)
