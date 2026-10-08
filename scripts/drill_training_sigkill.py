"""Kill a real worker and recover its training job in isolated local services.

Requires PYTHONPATH=.:backend:tests and FURNISCOPE_TEST_DATABASE_URL.
Uses synthetic fixtures; never target a business database or shared Redis DB.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

from sqlalchemy import text

from furniscope_api.job_queue import RedisJobQueue, STREAM, GROUP
from test_training_recovery import setup_run
from test_enterprise_training import training_registry_deployment


async def drill(args):
    cfg, db, service, tenant, training_uuid = await setup_run(
        args.work_dir, redis_url=args.redis_url, job_queue_mode="redis",
        worker_lease_ms=10_000, worker_concurrency=1, worker_block_ms=200)
    queue = RedisJobQueue.from_settings(cfg)
    workers, logs, child = [], [], None
    events = []

    def record(event, **detail):
        row = {"event": event, "at": datetime.now(timezone.utc).isoformat(), **detail}
        events.append(row)
        print(json.dumps(row), flush=True)

    def start_worker(name):
        log = (args.work_dir / f"{name}.log").open("w")
        logs.append(log)
        environment = {**os.environ, "DATABASE_URL": cfg.database_url,
                       "REDIS_URL": args.redis_url, "JOB_QUEUE_MODE": "redis",
                       "APP_ENV": "test", "WORKER_LEASE_MS": "10000", "WORKER_BLOCK_MS": "200",
                       "WORKER_CONCURRENCY": "1", "DEMO_STORAGE_ROOT": cfg.demo_storage_root,
                       "FORECAST_ARTIFACT_ROOT": cfg.forecast_artifact_root}
        process = subprocess.Popen([sys.executable, "-m", "furniscope_api.worker"],
                                   env=environment, stdout=log, stderr=log)
        workers.append(process)
        return process

    async def state():
        async with db.session_factory() as session:
            return dict((await session.execute(text("""
                SELECT status,execution_attempts,execution_token::text,lease_expires_at,
                       artifact_model_id AS model_id,error_code FROM forecast_training_runs
                 WHERE tenant_id=:t AND training_uuid=CAST(:u AS uuid)
            """), {"t": tenant, "u": training_uuid})).mappings().one())

    empty = await queue.client.dbsize() == 0
    if not empty:
        await queue.close()
        await db.close()
        raise RuntimeError("演练需要空的独立Redis逻辑库；已拒绝清空现有数据")
    try:
        await queue.ensure_group()
        await queue.enqueue("forecast_training", {"tenant_id": tenant, "training_uuid": training_uuid})
        first = start_worker("worker-before-kill")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            processes = subprocess.check_output(["ps", "-axo", "pid=,ppid=,command="], text=True)
            for line in processes.splitlines():
                fields = line.strip().split(None, 2)
                if (len(fields) == 3 and fields[1] == str(first.pid)
                        and "furniscope_forecast.training_process" in fields[2]):
                    child = int(fields[0])
                    os.kill(child, signal.SIGSTOP)
                    break
            if child:
                break
            if first.poll() is not None:
                raise RuntimeError("首个worker提前退出，检查日志")
            await asyncio.sleep(0.02)
        assert child, "未捕获实际训练子进程"
        before = await state()
        assert before["status"] == "running" and before["execution_attempts"] == 1
        record("actual_child_paused", worker_pid=first.pid, child_pid=child,
               execution_token=before["execution_token"], lease_expires_at=str(before["lease_expires_at"]))
        first.kill()  # No Python shutdown handler runs.
        await asyncio.to_thread(first.wait, 5)
        record("worker_sigkill", returncode=first.returncode)
        assert first.returncode == -signal.SIGKILL
        # Real time expiry: no database lease edits and no mocked recovery hooks.
        while datetime.now(timezone.utc) <= before["lease_expires_at"]:
            await asyncio.sleep(0.25)
        second = start_worker("worker-after-restart")
        record("recovery_worker_started", pid=second.pid)
        deadline = time.monotonic() + 55
        while time.monotonic() < deadline:
            recovered = await state()
            if recovered["status"] in {"succeeded", "failed", "rejected"}:
                break
            assert second.poll() is None, "恢复worker提前退出"
            await asyncio.sleep(0.25)
        assert recovered["status"] == "succeeded", recovered
        assert recovered["execution_attempts"] == 2
        assert recovered["execution_token"] != before["execution_token"]
        model_id = recovered["model_id"]
        record("recovered_and_published", attempts=2, model_id=model_id,
               execution_token=recovered["execution_token"])
        # The orphan can finish files, but has no publishing parent.
        root = Path(cfg.forecast_artifact_root) / str(tenant)
        orphan = root / f".staging-{training_uuid}-{before['execution_token']}"
        os.kill(child, signal.SIGCONT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if (orphan / "evaluation.json").exists():
                try:
                    with (orphan / ".lock").open("a") as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    pass
            await asyncio.sleep(0.1)
        else:
            raise AssertionError("旧子进程未结束写入")
        assert (await state())["model_id"] == model_id
        # Accelerate only the cleanup age threshold after observing child exit.
        os.utime(orphan, (time.time() - 300, time.time() - 300))
        async with db.session_factory() as session:
            assert await service.lifecycle.reconcile_artifacts(session) >= 1
            deployment = await training_registry_deployment(session, tenant)
            assert deployment["model_id"] == model_id
        assert not orphan.exists()
        assert len([p for p in root.iterdir() if p.is_dir()]) == 1
        deadline = time.monotonic() + 5
        while (await queue.client.xpending(STREAM, GROUP))["pending"] and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        pending = await queue.client.xpending(STREAM, GROUP)
        assert pending["pending"] == 0, pending
        record("orphan_reconciled", published_model_retained=True, pending_messages=0)
        (args.work_dir / "summary.json").write_text(json.dumps({
            "protocol": "training-sigkill-drill-v1", "synthetic": True,
            "tenant_id": tenant, "training_uuid": training_uuid, "events": events,
            "passed": True, "lease_ms": 10000,
            "fault": "SIGSTOP child then SIGKILL worker; natural lease expiry; real restart",
            "cleanup_age_accelerated_after_child_completion": True,
        }, ensure_ascii=False, indent=2) + "\n")
    finally:
        for process in workers:
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)
        if child:
            try:
                os.kill(child, signal.SIGCONT)
                os.kill(child, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for handle in logs:
            handle.close()
        await queue.client.flushdb()  # This logical DB was verified empty before the drill.
        await queue.close()
        await db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    database = urlsplit(os.environ.get("FURNISCOPE_TEST_DATABASE_URL", ""))
    redis = urlsplit(args.redis_url)
    if (database.hostname not in {"localhost", "127.0.0.1"} or
            not database.path.startswith("/furniscope_enterprise_test_") or
            redis.hostname not in {"localhost", "127.0.0.1"} or redis.port != 6396 or
            redis.path not in {"/14", "/15"}):
        parser.error("仅支持本地furniscope_enterprise_test_*库和6396端口的Redis逻辑库14/15")
    args.work_dir.mkdir(parents=True, exist_ok=False)
    asyncio.run(drill(args))


if __name__ == "__main__":
    main()
