from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import asyncpg
import pytest

import furniscope_api.worker as worker_module
from furniscope_api.config import ApiSettings
from furniscope_api.database import Database
from furniscope_api.job_queue import (
    DEAD_STREAM,
    GROUP,
    STREAM,
    JobLeaseLost,
    QueuedJob,
    RedisJobQueue,
    TenantQueueQuotaExceeded,
)
from furniscope_api.worker import JobHandlers


REDIS_URL = os.getenv("FURNISCOPE_TEST_REDIS_URL")
DATABASE_URL = os.getenv("FURNISCOPE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not REDIS_URL, reason="FURNISCOPE_TEST_REDIS_URL is not configured")


def settings() -> ApiSettings:
    return ApiSettings(
        app_env="test",
        database_url=(DATABASE_URL or "postgresql://test:test@127.0.0.1/furniscope_test").replace(
            "postgresql://", "postgresql+asyncpg://", 1,
        ),
        redis_url=REDIS_URL,
        job_queue_mode="redis",
        worker_block_ms=100,
        worker_lease_ms=10_000,
        worker_max_attempts=2,
    )


@pytest.mark.asyncio
async def test_queue_deduplicates_and_acknowledges_success() -> None:
    queue = RedisJobQueue.from_settings(settings())
    await queue.client.flushdb()
    await queue.ensure_group()
    await queue.enqueue("forecast", {"tenant_id": 1, "job_uuid": "x"}, job_id="forecast:x")
    await queue.enqueue("forecast", {"tenant_id": 1, "job_uuid": "x"}, job_id="forecast:x")
    assert await queue.client.xlen(STREAM) == 1
    handled = []

    async def handle(job):
        handled.append(job.job_id)

    async def unexpected(*_):
        raise AssertionError("hook must not run")

    assert await queue.consume_once("test-success", handle, unexpected, unexpected)
    assert handled == ["forecast:x"]
    assert (await queue.client.xpending(STREAM, "furniscope-workers"))["pending"] == 0
    assert await queue.tenant_metrics_snapshot() == [{
        "tenant_id": "1",
        "queued": 0,
        "running": 0,
        "deferred": 0,
        "completed": 1,
        "failed": 0,
    }]
    await queue.close()


@pytest.mark.asyncio
async def test_disabled_agent_runtime_cancels_legacy_job(monkeypatch) -> None:
    calls = {"commits": 0, "sql": [], "state": None, "event": None}

    class Session:
        async def execute(self, statement, parameters=None):
            calls["sql"].append((str(statement), parameters))

        async def commit(self):
            calls["commits"] += 1

    class SessionContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            return None

    class FakeDatabase:
        @staticmethod
        def session_factory():
            return SessionContext()

    class Repository:
        async def run_context(self, *_args, **_kwargs):
            return {
                "id": 19,
                "goal_id": 23,
                "plan_id": 29,
                "status": "queued",
                "progress_percent": 0,
                "current_step_id": None,
            }

        async def set_run_state(self, *_args, **kwargs):
            calls["state"] = kwargs

        async def append_event(self, *_args, **kwargs):
            calls["event"] = kwargs

    async def bind(_session, tenant_id):
        assert tenant_id == 7

    monkeypatch.setattr(worker_module, "AgentRepository", Repository)
    monkeypatch.setattr(worker_module, "bind_tenant_session", bind)
    handlers = JobHandlers(settings(), FakeDatabase())
    await handlers.handle(QueuedJob(
        message_id="legacy",
        job_id="agent-run:legacy",
        kind="agent_run",
        payload={"tenant_id": 7, "run_uuid": "00000000-0000-4000-8000-000000000019"},
        attempt=1,
        max_attempts=2,
    ))

    assert calls["state"]["status"] == "cancelled"
    assert calls["state"]["failure_code"] == "AGENT_RUNTIME_DISABLED"
    assert calls["event"]["event_type"] == "run.cancelled"
    assert calls["event"]["payload"]["reason"] == "runtime_disabled"
    assert any("UPDATE agent_plan_steps" in statement for statement, _ in calls["sql"])
    assert any("UPDATE agent_approvals" in statement for statement, _ in calls["sql"])
    assert calls["commits"] == 1


@pytest.mark.asyncio
async def test_tenant_queue_quota_is_atomic_and_released_after_terminal_success() -> None:
    cfg = settings().model_copy(update={"worker_tenant_queue_limit": 2})
    queue = RedisJobQueue.from_settings(cfg)
    await queue.client.flushdb()
    await queue.ensure_group()
    try:
        await queue.enqueue("forecast", {"tenant_id": 7}, job_id="quota:one")
        await queue.enqueue("forecast", {"tenant_id": 7}, job_id="quota:one")
        await queue.enqueue("forecast", {"tenant_id": 7}, job_id="quota:two")
        with pytest.raises(TenantQueueQuotaExceeded) as raised:
            await queue.enqueue("forecast", {"tenant_id": 7}, job_id="quota:three")
        assert raised.value.tenant_id == "7"
        assert await queue.client.xlen(STREAM) == 2

        async def handle(_job):
            return None

        async def unexpected(*_):
            raise AssertionError("hook must not run")

        assert await queue.consume_once("quota", handle, unexpected, unexpected)
        await queue.enqueue("forecast", {"tenant_id": 7}, job_id="quota:three")
        snapshot = (await queue.tenant_metrics_snapshot())[0]
        assert snapshot["queued"] == 2
        assert snapshot["completed"] == 1
    finally:
        await queue.close()


@pytest.mark.asyncio
async def test_tenant_slot_defers_busy_tenant_and_runs_next_tenant() -> None:
    cfg = settings().model_copy(update={
        "worker_tenant_max_in_flight": 1,
        "worker_tenant_defer_ms": 10,
    })
    queue = RedisJobQueue.from_settings(cfg)
    await queue.client.flushdb()
    await queue.ensure_group()
    await queue.enqueue("forecast", {"tenant_id": 1}, job_id="fair:tenant-1-running")
    await queue.enqueue("forecast", {"tenant_id": 1}, job_id="fair:tenant-1-waiting")
    await queue.enqueue("forecast", {"tenant_id": 2}, job_id="fair:tenant-2")
    started, release = asyncio.Event(), asyncio.Event()

    async def hold_first(job):
        assert job.job_id == "fair:tenant-1-running"
        started.set()
        await release.wait()

    async def unexpected(*_):
        raise AssertionError("hook must not run")

    first = asyncio.create_task(
        queue.consume_once("fair-first", hold_first, unexpected, unexpected),
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        handled = []

        async def collect(job):
            handled.append(job.job_id)

        assert await queue.consume_once("fair-second", collect, unexpected, unexpected)
        assert handled == []
        assert await queue.consume_once("fair-second", collect, unexpected, unexpected)
        assert handled == ["fair:tenant-2"]
        snapshots = {
            row["tenant_id"]: row for row in await queue.tenant_metrics_snapshot()
        }
        assert snapshots["1"]["running"] == 1
        assert snapshots["1"]["deferred"] == 1
        assert snapshots["2"]["completed"] == 1
    finally:
        release.set()
        await asyncio.wait_for(first, timeout=2)
        await queue.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not DATABASE_URL, reason="FURNISCOPE_TEST_DATABASE_URL is not configured")
async def test_worker_consumes_admin_safe_stop_event() -> None:
    suffix = uuid4().hex[:10]
    connection = await asyncpg.connect(DATABASE_URL)
    database = Database(settings())
    try:
        tenant_id = await connection.fetchval(
            """INSERT INTO furniscope.tenants(tenant_code,name,status)
               VALUES($1,'Worker control test','active') RETURNING id""",
            f"CTRL_{suffix.upper()}",
        )
        user_id = await connection.fetchval(
            """INSERT INTO furniscope.users(tenant_id,email,password_hash,name,role_code,status)
               VALUES($1,$2,'not-a-login-secret','Control tester','admin','active') RETURNING id""",
            tenant_id, f"control-{suffix}@example.invalid",
        )
        product_id = await connection.fetchval(
            """INSERT INTO furniscope.products(tenant_id,sku,name,created_by)
               VALUES($1,$2,'Control test product',$3) RETURNING id""",
            tenant_id, f"CTRL-{suffix}", user_id,
        )
        profile_id = await connection.fetchval(
            """INSERT INTO furniscope.product_profile_versions
               (tenant_id,product_id,version_no,schema_version,status,completeness_score,source_summary,
                confirmed_by,confirmed_at)
               VALUES($1,$2,1,'test-v1','confirmed',1,'{}',$3,now()) RETURNING id""",
            tenant_id, product_id, user_id,
        )
        dataset_id = await connection.fetchval(
            """INSERT INTO furniscope.market_datasets
               (tenant_id,name,platform,market_country,category_code,data_end_date,source_type,
                source_name,authorization_reference,status)
               VALUES($1,$2,'amazon','US','sofa',CURRENT_DATE,'public_authorized',
                'worker-test','test-only','ready') RETURNING id""",
            tenant_id, f"Control dataset {suffix}",
        )
        task = await connection.fetchrow(
            """INSERT INTO furniscope.analysis_tasks
               (tenant_id,job_name,product_id,product_profile_version_id,dataset_id,target_country,
                target_platform,analysis_currency,status,analysis_config,ontology_version,scoring_version,
                prompt_bundle_version,model_route_version,idempotency_key,created_by)
               VALUES($1,'Control task',$2,$3,$4,'US','amazon','USD','running','{}',
                'taxonomy-v1','score-v1','prompt-v1','route-v1',$5,$6)
               RETURNING id,task_uuid""",
            tenant_id, product_id, profile_id, dataset_id, f"control-task-{suffix}", user_id,
        )
        await connection.execute(
            """INSERT INTO furniscope.task_stage_runs
               (tenant_id,task_id,stage_code,idempotency_key,status,input_ref,started_at)
               VALUES($1,$2,'market_analytics',$3,'running','{}',now())""",
            tenant_id, task["id"], f"control-stage-{suffix}",
        )
        event_uuid = await connection.fetchval(
            """INSERT INTO furniscope.workflow_control_events
               (tenant_id,task_id,event_type,payload,idempotency_key,requested_by)
               VALUES($1,$2,'safe_stop','{}',$3,$4) RETURNING event_uuid""",
            tenant_id, task["id"], f"control-event-{suffix}", user_id,
        )

        job = QueuedJob(
            message_id="test", job_id=f"admin-control:{event_uuid}", kind="admin_control",
            payload={"event_uuid": str(event_uuid), "event_type": "safe_stop"},
            attempt=1, max_attempts=2,
        )
        await JobHandlers(settings(), database).handle(job)

        assert await connection.fetchval(
            "SELECT status FROM furniscope.analysis_tasks WHERE id=$1", task["id"],
        ) == "cancelled"
        assert await connection.fetchval(
            "SELECT status FROM furniscope.task_stage_runs WHERE task_id=$1", task["id"],
        ) == "cancelled"
        event = await connection.fetchrow(
            """SELECT status,delivery_attempts,consumed_at
                 FROM furniscope.workflow_control_events WHERE event_uuid=$1""",
            event_uuid,
        )
        assert event["status"] == "consumed"
        assert event["delivery_attempts"] == 1
        assert event["consumed_at"] is not None
    finally:
        await database.close()
        if 'tenant_id' in locals():
            await connection.execute(
                "DELETE FROM furniscope.analysis_workspace_messages WHERE tenant_id=$1", tenant_id,
            )
            await connection.execute(
                "UPDATE furniscope.analysis_workspaces SET last_analysis_task_id=NULL WHERE tenant_id=$1",
                tenant_id,
            )
            await connection.execute(
                "DELETE FROM furniscope.analysis_workspaces WHERE tenant_id=$1", tenant_id,
            )
            for table in (
                "analysis_tasks", "product_profile_versions", "products",
                "market_datasets", "users",
            ):
                await connection.execute(
                    f"DELETE FROM furniscope.{table} WHERE tenant_id=$1", tenant_id,
                )
            await connection.execute("DELETE FROM furniscope.tenants WHERE id=$1", tenant_id)
        await connection.close()


@pytest.mark.asyncio
async def test_queue_retries_then_dead_letters_without_losing_message() -> None:
    queue = RedisJobQueue.from_settings(settings())
    await queue.client.flushdb()
    await queue.ensure_group()
    await queue.enqueue("analysis", {"tenant_id": 1, "task_id": 2}, job_id="analysis:x")
    retries = []
    failures = []

    async def fail_handler(_job):
        raise RuntimeError("sensitive upstream detail")

    async def retry(job, exc):
        retries.append((job.attempt, type(exc).__name__))

    async def failed(job, exc):
        failures.append((job.attempt, type(exc).__name__))

    assert await queue.consume_once("test-retry", fail_handler, retry, failed)
    assert retries == [(1, "RuntimeError")]
    assert await queue.consume_once("test-retry", fail_handler, retry, failed)
    assert failures == [(2, "RuntimeError")]
    assert await queue.client.xlen(DEAD_STREAM) == 1
    dead = (await queue.client.xrange(DEAD_STREAM))[0][1]
    assert dead["error_type"] == "RuntimeError"
    assert "sensitive upstream detail" not in str(dead)
    await queue.close()


@pytest.mark.asyncio
async def test_queue_quarantines_malformed_messages() -> None:
    queue = RedisJobQueue.from_settings(settings())
    await queue.client.flushdb()
    await queue.ensure_group()
    await queue.client.xadd(
        STREAM, {"job_id": "broken", "kind": "analysis", "payload": "[not-an-object]"},
    )

    async def unexpected(*_):
        raise AssertionError("malformed jobs must not reach handlers")

    assert await queue.consume_once("test-malformed", unexpected, unexpected, unexpected)
    assert (await queue.client.xpending(STREAM, "furniscope-workers"))["pending"] == 0
    dead = (await queue.client.xrange(DEAD_STREAM))[0][1]
    assert dead["failure_code"] == "MALFORMED_JOB_MESSAGE"
    assert "payload" not in dead
    await queue.close()


@pytest.mark.asyncio
async def test_live_queue_heartbeat_prevents_reclaim_and_lost_owner_never_acks():
    # Accelerate time only for this integration test; production enforces >=10s.
    cfg = settings().model_copy(update={"worker_lease_ms": 300})
    queue = RedisJobQueue.from_settings(cfg)
    await queue.client.flushdb()
    await queue.ensure_group()
    await queue.enqueue("forecast_training", {"tenant_id": 1}, job_id="lease-test")
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def handle(_job):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    async def unexpected(*_):
        raise AssertionError("Lease loss must not invoke retry/fail hooks")

    work = asyncio.create_task(queue.consume_once("original", handle, unexpected, unexpected))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0.7)  # More than two leases, while handler is still alive.
        assert await queue._next("replacement") is None
        pending = await queue.client.xpending_range(STREAM, GROUP, "-", "+", 1)
        message_id = pending[0]["message_id"]
        await queue.client.xclaim(STREAM, GROUP, "replacement", 0, [message_id])
        with pytest.raises(JobLeaseLost):
            await asyncio.wait_for(work, timeout=2)
        assert cancelled.is_set()
        pending = await queue.client.xpending_range(STREAM, GROUP, "-", "+", 1)
        assert pending[0]["consumer"] == "replacement"
        assert await queue.client.xlen(DEAD_STREAM) == 0
    finally:
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        await queue.close()


@pytest.mark.asyncio
async def test_queue_timeout_waits_for_handler_cleanup_before_retry():
    cfg = settings().model_copy(update={"worker_job_timeout_seconds": 1})
    queue = RedisJobQueue.from_settings(cfg)
    await queue.client.flushdb()
    await queue.ensure_group()
    await queue.enqueue("forecast_training", {}, job_id="timeout-test")
    cleaned, retried = asyncio.Event(), asyncio.Event()

    async def handle(_job):
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0.05)
            cleaned.set()

    async def retry(_job, exc):
        assert isinstance(exc, TimeoutError)
        assert cleaned.is_set()
        retried.set()

    async def unexpected(*_):
        raise AssertionError("failure hook must not run")

    try:
        assert await queue.consume_once("timeout", handle, retry, unexpected)
        assert retried.is_set()
        assert (await queue.client.xpending(STREAM, GROUP))["pending"] == 0
        assert (await queue.client.xrevrange(STREAM, count=1))[0][1]["attempt"] == "2"
    finally:
        await queue.close()
