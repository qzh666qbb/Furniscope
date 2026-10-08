"""Failure injection for training ownership, process cancellation and recovery."""

import asyncio
import fcntl
import os
from pathlib import Path
import time
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import text

from furniscope_api.config import ApiSettings
from furniscope_api.database import Database, bind_tenant_session
from furniscope_api.services.forecast_runtime import TenantForecastRuntimeRegistry
from furniscope_api.services.forecast_training_service import ForecastTrainingService
from furniscope_api.services.training_lifecycle import TrainingLifecycle, TrainingLeaseLost
from furniscope_api.worker import JobHandlers
from test_enterprise_training import rules, sales_records, training_registry_deployment

DATABASE_URL = os.getenv("FURNISCOPE_TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DATABASE_URL, reason="needs isolated PostgreSQL")


async def setup_run(tmp_path, **settings):
    cfg = ApiSettings(database_url=DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://"),
                      app_env="test", demo_storage_root=str(tmp_path / "raw"),
                      forecast_artifact_root=str(tmp_path / "models"), **settings)
    db = Database(cfg)
    service = ForecastTrainingService(cfg, TenantForecastRuntimeRegistry(cfg))
    async with db.session_factory() as session:
        tenant = await session.scalar(text("""
            INSERT INTO tenants(tenant_code,name,status) VALUES(:code,'恢复演练','active') RETURNING id
        """), {"code": f"REC_{uuid4().hex[:16].upper()}"})
        user = await session.scalar(text("""
            INSERT INTO users(tenant_id,email,password_hash,name,role_code,status)
            VALUES(:tenant,:email,'not-a-login','恢复演练','admin','active') RETURNING id
        """), {"tenant": tenant, "email": f"{uuid4().hex}@example.invalid"})
        await session.execute(text("""
            INSERT INTO products(tenant_id,created_by,sku,name,category_code)
            VALUES(:tenant,:user,'SAME-SKU','测试商品','sofa')
        """), {"tenant": tenant, "user": user})
        item = await service.data.upload(session, tenant_id=tenant, user_id=user, filename="sales.csv",
            content=pd.DataFrame(sales_records()).to_csv(index=False).encode())
        preview = await service.data.preflight(session, tenant_id=tenant,
                                               version_uuid=item["version_uuid"], rules=rules())
        await service.data.confirm(session, tenant_id=tenant, version_uuid=item["version_uuid"],
                                   preview_sha256=preview["preview_sha256"])
        _, run = await service.create(session, tenant_id=tenant, user_id=user,
            version_uuid=item["version_uuid"], mode="initial", allow_history_overwrite=False,
            idempotency_key=uuid4().hex)
        await session.commit()
    return cfg, db, service, tenant, run["training_uuid"]


@pytest.mark.asyncio
async def test_cancellation_terminates_the_actual_training_process(tmp_path):
    cfg = ApiSettings(app_env="test")
    stage = tmp_path / ".staging-process"
    stage.mkdir()
    # A deterministic long-running child exposes its PID before doing any work.
    (stage / "forecast.py").write_text("""
import os, time
def train_artifacts(records, stage, lineage):
    (stage / 'child.pid').write_text(str(os.getpid()))
    time.sleep(30)
    (stage / 'late-write').write_text('should never happen')
    return {'passed': False}
""")
    work = asyncio.create_task(TrainingLifecycle(cfg).train([], stage, {}))
    async def started():
        while not (stage / "child.pid").exists():
            await asyncio.sleep(0.02)
    try:
        await asyncio.wait_for(started(), timeout=10)
        pid = int((stage / "child.pid").read_text())
    finally:
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not (stage / "late-write").exists()


@needs_db
@pytest.mark.asyncio
async def test_expired_execution_cannot_publish_after_a_successful_recovery(tmp_path, monkeypatch):
    cfg, db, service, tenant, run = await setup_run(tmp_path)
    old_ready, release_old = asyncio.Event(), asyncio.Event()
    actual_train = service.lifecycle.train
    calls = 0

    async def paused_train(*args):
        nonlocal calls
        calls += 1
        number = calls
        result = await actual_train(*args)
        if number == 1:
            old_ready.set()
            await release_old.wait()
        return result

    async def frozen_heartbeat(*_args, **_kwargs):
        await asyncio.Future()  # Simulate a worker suspended past its DB lease.

    monkeypatch.setattr(service.lifecycle, "train", paused_train)
    monkeypatch.setattr(service.lifecycle, "heartbeat", frozen_heartbeat)
    old = None
    try:
        async with db.session_factory() as first, db.session_factory() as recovery:
            await bind_tenant_session(first, tenant)
            old = asyncio.create_task(service.execute(first, tenant_id=tenant, training_uuid=run))
            await asyncio.wait_for(old_ready.wait(), timeout=30)
            assert await service.lifecycle.recover_expired(recovery) == 0
            await recovery.execute(text("""
                UPDATE forecast_training_runs SET lease_expires_at=now()-interval '1 second'
                 WHERE tenant_id=:t AND training_uuid=CAST(:u AS uuid)
            """), {"t": tenant, "u": run})
            assert await service.lifecycle.recover_expired(recovery) == 1
            await recovery.commit()
            await service.execute(recovery, tenant_id=tenant, training_uuid=run)
            deployment = await training_registry_deployment(recovery, tenant)
            assert deployment
            release_old.set()
            with pytest.raises(TrainingLeaseLost):
                await old
            result = await service.get(recovery, tenant_id=tenant, training_uuid=run)
            assert result["status"] == "succeeded" and result["execution_attempts"] == 2
            assert (await training_registry_deployment(recovery, tenant))["model_id"] == deployment["model_id"]
            assert not list((Path(cfg.forecast_artifact_root) / str(tenant)).glob(".staging-*"))
    finally:
        if old and not old.done():
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)
        await db.close()


@needs_db
@pytest.mark.asyncio
async def test_timeout_and_restart_attempt_limit_release_tenant_slot(tmp_path, monkeypatch):
    cfg, db, service, tenant, run = await setup_run(
        tmp_path, worker_job_timeout_seconds=1, worker_max_attempts=2)

    async def suspended(*_args, **_kwargs):
        await asyncio.Future()
    monkeypatch.setattr(service.lifecycle, "train", suspended)
    try:
        async with db.session_factory() as session:
            with pytest.raises(TimeoutError):
                await service.execute(session, tenant_id=tenant, training_uuid=run)
            row = await service.get(session, tenant_id=tenant, training_uuid=run)
            assert row["status"] == "failed" and row["execution_attempts"] == 1
            assert not list((Path(cfg.forecast_artifact_root) / str(tenant)).glob(".staging-*"))
            # Simulate the final worker dying without a Python exception handler.
            await session.execute(text("""
                UPDATE forecast_training_runs SET status='running',execution_attempts=2,
                    lease_expires_at=now()-interval '1 second'
                 WHERE tenant_id=:t AND training_uuid=CAST(:u AS uuid)
            """), {"t": tenant, "u": run})
            assert await service.lifecycle.recover_expired(session) == 1
            await session.commit()
            row = await service.get(session, tenant_id=tenant, training_uuid=run)
            assert row["status"] == "failed" and row["error_code"] == "FORECAST_LEASE_EXPIRED"
            assert not await session.scalar(text("""
                SELECT EXISTS(SELECT FROM forecast_training_runs
                               WHERE tenant_id=:t AND status IN ('running','queued'))
            """), {"t": tenant})
    finally:
        await db.close()


@needs_db
@pytest.mark.asyncio
async def test_reconciler_retains_live_children_and_referenced_artifacts(tmp_path):
    cfg, db, service, tenant, run = await setup_run(tmp_path)
    try:
        async with db.session_factory() as session:
            await service.execute(session, tenant_id=tenant, training_uuid=run)
            deployment = await training_registry_deployment(session, tenant)
            root = Path(cfg.forecast_artifact_root) / str(tenant)
            published = root / deployment["state_uri"].split("/")[-1]
            stale = root / f".staging-{run}-{uuid4()}"
            abandoned_final = root / f"{run}-{uuid4()}"
            locked = root / f".staging-{run}-{uuid4()}"
            for directory in (stale, abandoned_final, locked):
                directory.mkdir()
            with (locked / ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for directory in (stale, abandoned_final, locked, published):
                    os.utime(directory, (time.time()-300, time.time()-300))
                assert await service.lifecycle.reconcile_artifacts(session) == 2
                assert locked.exists() and published.exists()
            os.utime(locked, (time.time()-300, time.time()-300))
            assert await service.lifecycle.reconcile_artifacts(session) == 1
            assert published.exists()
    finally:
        await db.close()


@needs_db
@pytest.mark.asyncio
async def test_database_heartbeat_and_shutdown_are_recoverable(tmp_path, monkeypatch):
    cfg, db, service, tenant, run = await setup_run(tmp_path)
    cfg.worker_lease_ms = 300  # Accelerated lease for an actual DB renewal test.
    started = asyncio.Event()

    async def suspended(*_args, **_kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(service.lifecycle, "train", suspended)
    work = None
    try:
        async with db.session_factory() as first, db.session_factory() as monitor:
            await bind_tenant_session(first, tenant)
            work = asyncio.create_task(service.execute(first, tenant_id=tenant, training_uuid=run))
            await asyncio.wait_for(started.wait(), timeout=5)
            await asyncio.sleep(0.8)
            row = await service.get(monitor, tenant_id=tenant, training_uuid=run)
            assert row["status"] == "running" and row["heartbeat_at"] > row["started_at"]
            assert await service.lifecycle.recover_expired(monitor) == 0
            await monitor.commit()
            work.cancel()
            with pytest.raises(asyncio.CancelledError):
                await work
            assert (await service.get(monitor, tenant_id=tenant, training_uuid=run))["status"] == "queued"

            class Queue:
                jobs = []
                async def enqueue(self, kind, payload, **options):
                    self.jobs.append((kind, payload, options))
            queue = Queue()
            await JobHandlers(cfg, db).recover(queue)
            jobs = [job for job in queue.jobs
                    if job[0] == "forecast_training" and job[1]["training_uuid"] == run]
            assert len(jobs) == 1 and jobs[0][2]["attempt"] == 2
            assert ":recover:" in jobs[0][2]["job_id"]
    finally:
        if work and not work.done():
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
        await db.close()
