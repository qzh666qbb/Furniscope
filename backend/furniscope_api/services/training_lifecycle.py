"""Durable execution fencing, cancellable training and orphan reconciliation."""

from __future__ import annotations

import asyncio
import fcntl
import json
from pathlib import Path
import re
import shutil
import sys
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import bind_tenant_session


class TrainingLeaseLost(RuntimeError):
    pass


class TrainingLifecycle:
    def __init__(self, settings):
        self.settings = settings

    async def heartbeat(self, bind, *, tenant_id, training_uuid, token):
        while True:
            await asyncio.sleep(self.settings.worker_lease_ms / 3000)
            async with AsyncSession(bind=bind) as session:
                await bind_tenant_session(session, tenant_id)
                renewed = await session.execute(text("""
                    UPDATE forecast_training_runs
                       SET heartbeat_at=clock_timestamp(),
                           lease_expires_at=clock_timestamp()+:seconds*interval '1 second'
                     WHERE tenant_id=:tenant AND training_uuid=CAST(:uuid AS uuid)
                       AND execution_token=CAST(:token AS uuid) AND status='running'
                       AND lease_expires_at>clock_timestamp()
                """), {"tenant": tenant_id, "uuid": training_uuid, "token": token,
                         "seconds": self.settings.worker_lease_ms / 1000})
                if not renewed.rowcount:
                    raise TrainingLeaseLost("训练执行租约已失效")
                await session.commit()

    async def fence(self, session, *, tenant_id, training_uuid, token):
        """Lock and validate the owner until its publish/reject transaction commits."""
        owner = await session.scalar(text("""
            SELECT id FROM forecast_training_runs
             WHERE tenant_id=:tenant AND training_uuid=CAST(:uuid AS uuid)
               AND execution_token=CAST(:token AS uuid) AND status='running'
               AND lease_expires_at>clock_timestamp() FOR UPDATE
        """), {"tenant": tenant_id, "uuid": training_uuid, "token": token})
        if owner is None:
            raise TrainingLeaseLost("训练执行租约已失效，新模型未发布")

    async def train(self, records, stage, lineage):
        (stage / "training-input.json").write_text(json.dumps(
            {"records": records, "lineage": lineage}, ensure_ascii=False, allow_nan=False))
        with (stage / "process.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "furniscope_forecast.training_process", str(stage),
                stdout=log, stderr=log)
            try:
                code = await process.wait()
                if code:
                    raise RuntimeError(f"训练进程退出（{code}），请检查服务端训练日志")
                result = json.loads((stage / "evaluation.json").read_text())
                (stage / "training-input.json").unlink()
                return result
            finally:
                if process.returncode is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except TimeoutError:
                        process.kill()
                        await process.wait()

    async def recover_expired(self, session):
        result = await session.execute(text("""
            UPDATE forecast_training_runs
               SET status=CASE WHEN execution_attempts<:maximum THEN 'queued' ELSE 'failed' END,
                   error_code='FORECAST_LEASE_EXPIRED',
                   error_message='训练进程中断，租约已过期',
                   completed_at=CASE WHEN execution_attempts<:maximum THEN NULL ELSE now() END,
                   lease_expires_at=NULL
             WHERE status='running'
               AND COALESCE(lease_expires_at,started_at+:seconds*interval '1 second',
                            created_at+:seconds*interval '1 second')<clock_timestamp()
        """), {"maximum": self.settings.worker_max_attempts,
                 "seconds": self.settings.worker_lease_ms / 1000})
        return result.rowcount

    async def reconcile_artifacts(self, session):
        """Remove only provably unreferenced, inactive, unlocked training attempts.

        File locks cover orphan children; database row locks cover the small
        rename-to-commit interval and concurrent claims. Unknown directories or
        unavailable databases are retained. Published/history artifacts survive.
        """
        root = Path(self.settings.forecast_artifact_root).resolve()
        if not root.exists():
            return 0
        uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        pattern = re.compile(rf"^(?:\.staging-)?({uuid_pattern})(?:-({uuid_pattern}))?$")
        removed = 0
        minimum_age = max(60, self.settings.worker_lease_ms / 1000)
        for tenant_root in root.iterdir():
            if not tenant_root.name.isdigit() or not tenant_root.is_dir() or tenant_root.is_symlink():
                continue
            for directory in tenant_root.iterdir():
                match = pattern.fullmatch(directory.name)
                if not match or not directory.is_dir() or directory.is_symlink():
                    continue
                if time.time() - directory.stat().st_mtime < minimum_age:
                    continue
                training_uuid, token = match.groups()
                row = (await session.execute(text("""
                    SELECT status,execution_token::text FROM forecast_training_runs
                     WHERE tenant_id=:tenant AND training_uuid=CAST(:uuid AS uuid)
                     FOR UPDATE SKIP LOCKED
                """), {"tenant": int(tenant_root.name), "uuid": training_uuid})).mappings().one_or_none()
                if not row:
                    await session.commit()
                    continue
                live = row["status"] == "running" and row["execution_token"] == token
                referenced = await session.scalar(text("""
                    SELECT EXISTS(SELECT FROM forecast_models WHERE owner_tenant_id=:tenant AND state_uri=:uri)
                """), {"tenant": int(tenant_root.name),
                         "uri": f"server-managed://tenant/{tenant_root.name}/{directory.name}"})
                if not live and not referenced:
                    try:
                        with (directory / ".lock").open("a") as lock:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            shutil.rmtree(directory)
                            removed += 1
                    except (FileNotFoundError, BlockingIOError):
                        pass
                await session.commit()
        return removed
