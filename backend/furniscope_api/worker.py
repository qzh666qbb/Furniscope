"""Independent Redis worker for long-running FurniScope jobs."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import signal
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from .config import ApiSettings, get_settings
from .database import Database
from .job_queue import QueuedJob, RedisJobQueue
from .logging import configure_logging
from .services.analysis_agent_adapter import AnalysisAgentAdapter
from .services.authorized_signals import AuthorizedSignalService
from .services.dataset_import_service import DatasetImportService
from .services.forecast_runtime import TenantForecastRuntimeRegistry
from .services.forecast_service import ForecastService
from .services.forecast_training_service import ForecastTrainingService
from .services.notifications import NotificationService
from .services.parse_service import ParseService


class JobHandlers:
    def __init__(self, settings: ApiSettings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.forecast_runtime = TenantForecastRuntimeRegistry(settings)

    async def handle(self, job: QueuedJob) -> None:
        if job.kind == "analysis":
            await AnalysisAgentAdapter(self.settings).run(**job.payload)
            return
        if job.kind == "confirmation_resume":
            await AnalysisAgentAdapter(self.settings).resume_next_confirmation()
            return
        if job.kind == "admin_control":
            await self._handle_admin_control(job)
            return
        async with self.database.session_factory() as session:
            if job.kind == "product_parse":
                await ParseService(self.settings).run_job(session, **job.payload)
            elif job.kind == "dataset_import":
                row = (await session.execute(text("""
                    SELECT a.storage_key,a.original_filename,a.mime_type FROM market_datasets d
                    JOIN file_assets a ON a.id=d.import_asset_id AND a.tenant_id=d.tenant_id
                    WHERE d.id=:dataset_id AND d.tenant_id=:tenant_id AND d.status='validating'
                """), job.payload)).mappings().one_or_none()
                if row is None:
                    return
                root = Path(self.settings.demo_storage_root).resolve()
                source = (root / row["storage_key"]).resolve()
                if root not in source.parents or not source.is_file():
                    raise RuntimeError("dataset import asset is unavailable")
                await DatasetImportService(self.settings).run_import(
                    session, content=source.read_bytes(), filename=row["original_filename"],
                    mime=row["mime_type"], **job.payload,
                )
            elif job.kind == "forecast":
                await ForecastService(self.settings, self.forecast_runtime).execute(session, **job.payload)
            elif job.kind == "forecast_training":
                await ForecastTrainingService(self.settings, self.forecast_runtime).execute(
                    session, **job.payload)
            elif job.kind == "authorized_signal_fetch":
                await AuthorizedSignalService(self.settings).fetch_source(session, **job.payload)
            elif job.kind == "policy_source_fetch":
                await AuthorizedSignalService(self.settings).fetch_policy_source(session, **job.payload)
            else:
                raise ValueError(f"unsupported job kind: {job.kind}")
            await session.commit()

    async def _handle_admin_control(self, job: QueuedJob) -> None:
        """Consume a validated admin recovery/stop event without arbitrary node jumps."""
        event_uuid = job.payload["event_uuid"]
        dispatch = None
        async with self.database.session_factory() as session:
            row = (await session.execute(text("""
                SELECT e.id,e.event_type,e.checkpoint_id,e.stage_code,e.status,e.enqueued_at,
                       e.task_id,e.tenant_id,t.task_uuid::text task_uuid,t.status task_status
                  FROM workflow_control_events e
                  JOIN analysis_tasks t ON t.id=e.task_id AND t.tenant_id=e.tenant_id
                 WHERE e.event_uuid=CAST(:event_uuid AS uuid)
                 FOR UPDATE OF e,t
            """), {"event_uuid": event_uuid})).mappings().one_or_none()
            if row is None or row["event_type"] not in {"auto_retry", "safe_stop"}:
                return
            if row["status"] == "consumed":
                return
            if row["status"] == "enqueued" and row["enqueued_at"] and row["enqueued_at"] > datetime.now(timezone.utc) - timedelta(minutes=5):
                return
            await session.execute(text("""UPDATE workflow_control_events
                SET status='enqueued',enqueued_at=now(),delivery_attempts=delivery_attempts+1,error_code=NULL
                WHERE id=:id"""), {"id": row["id"]})
            if row["event_type"] == "safe_stop":
                await session.execute(text("""UPDATE analysis_tasks SET status='cancelled',failure_code=NULL,
                    failure_message=NULL,updated_at=now() WHERE id=:task_id AND tenant_id=:tenant_id
                    AND status NOT IN ('succeeded','cancelled')"""), row)
                await session.execute(text("""UPDATE task_stage_runs SET status='cancelled',ended_at=now()
                    WHERE task_id=:task_id AND status IN ('queued','running','retry_scheduled')"""), row)
                await session.execute(text("""UPDATE workflow_control_events SET status='consumed',consumed_at=now()
                    WHERE id=:id"""), {"id": row["id"]})
            else:
                if row["task_status"] not in {"failed", "partial_succeeded", "running", "queued"}:
                    await session.execute(text("""UPDATE workflow_control_events SET status='failed',
                        error_code='WORKFLOW_NOT_RECOVERABLE' WHERE id=:id"""), {"id": row["id"]})
                else:
                    checkpoint = (await session.execute(text("""SELECT 1 FROM workflow_checkpoints
                        WHERE checkpoint_id=:checkpoint_id AND task_id=:task_id AND stage_code=:stage_code
                          AND status='active' AND is_safe_resume"""), row)).scalar_one_or_none()
                    pending = (await session.execute(text("""SELECT EXISTS(SELECT 1 FROM user_confirmations
                        WHERE task_id=:task_id AND status='pending')"""), row)).scalar_one()
                    if checkpoint is None or pending:
                        await session.execute(text("""UPDATE workflow_control_events SET status='failed',
                            error_code='WORKFLOW_CHECKPOINT_CONFLICT' WHERE id=:id"""), {"id": row["id"]})
                    else:
                        await session.execute(text("""UPDATE analysis_tasks SET status='queued',failure_code=NULL,
                            failure_message=NULL,updated_at=now() WHERE id=:task_id AND tenant_id=:tenant_id"""), row)
                        dispatch = {"tenant_id": row["tenant_id"], "task_id": row["task_id"], "task_uuid": row["task_uuid"]}
            await session.commit()
        if dispatch:
            try:
                await AnalysisAgentAdapter(self.settings).run(**dispatch)
            except Exception:
                async with self.database.session_factory() as session:
                    await session.execute(text("""UPDATE workflow_control_events SET status='failed',
                        error_code='WORKER_EXECUTION_FAILED' WHERE event_uuid=CAST(:event AS uuid)"""), {"event": event_uuid})
                    await session.commit()
                raise
            async with self.database.session_factory() as session:
                await session.execute(text("""UPDATE workflow_control_events SET status='consumed',consumed_at=now(),error_code=NULL
                    WHERE event_uuid=CAST(:event AS uuid) AND status='enqueued'"""), {"event": event_uuid})
                await session.commit()

    async def enqueue_due_authorized_signals(self, queue: RedisJobQueue) -> None:
        minute_bucket = int(datetime.now(timezone.utc).timestamp() // 60)
        enqueued: list[tuple[str, dict[str, int], str]] = []
        async with self.database.session_factory() as session:
            rows = (await session.execute(text("""
                SELECT id,tenant_id,'market' AS kind FROM authorized_collect_sources
                 WHERE enabled AND COALESCE(last_status,'')<>'queued'
                   AND (last_fetched_at IS NULL OR last_fetched_at <= CURRENT_TIMESTAMP - make_interval(mins => schedule_minutes))
                UNION ALL
                SELECT id,tenant_id,'policy' AS kind FROM policy_sources
                 WHERE enabled AND COALESCE(last_status,'')<>'queued'
                   AND (last_fetched_at IS NULL OR last_fetched_at <= CURRENT_TIMESTAMP - make_interval(mins => schedule_minutes))
                 LIMIT 100
            """))).mappings().all()
            for row in rows:
                table = "authorized_collect_sources" if row["kind"] == "market" else "policy_sources"
                claimed = await session.execute(text(f"""
                    UPDATE {table} SET last_status='queued',last_error=NULL,last_queued_at=CURRENT_TIMESTAMP
                     WHERE id=:id AND tenant_id=:tenant AND enabled AND COALESCE(last_status,'')<>'queued'
                """), {"id": row["id"], "tenant": row["tenant_id"]})
                if claimed.rowcount:
                    payload = {"tenant_id": int(row["tenant_id"]), "source_id": int(row["id"])}
                    job_id = f"{row['kind']}-signal:{row['tenant_id']}:{row['id']}:{minute_bucket}"
                    enqueued.append(("authorized_signal_fetch" if row["kind"] == "market" else "policy_source_fetch",
                                     payload, job_id))
            await session.commit()
        for kind, payload, job_id in enqueued:
            try:
                await queue.enqueue(kind, payload, job_id=job_id)
            except Exception:
                table = "authorized_collect_sources" if kind == "authorized_signal_fetch" else "policy_sources"
                async with self.database.session_factory() as session:
                    await session.execute(text(f"""
                        UPDATE {table} SET last_status='failed',last_error='enqueue_failed'
                         WHERE id=:id AND tenant_id=:tenant AND last_status='queued'
                    """), {"id": payload["source_id"], "tenant": payload["tenant_id"]})
                    await session.commit()
                raise

    async def deliver_pending_notifications(self) -> None:
        async with self.database.session_factory() as session:
            await NotificationService(self.settings).deliver_pending(session, limit=20)
            await session.commit()

    async def retry(self, job: QueuedJob, exc: Exception) -> None:
        async with self.database.session_factory() as session:
            p = job.payload
            if job.kind == "analysis":
                await session.execute(text("""
                    UPDATE analysis_tasks SET status='queued',failure_code=NULL,failure_message=NULL
                    WHERE id=:task_id AND tenant_id=:tenant_id AND status='failed'
                """), p)
            elif job.kind == "forecast":
                await session.execute(text("""
                    UPDATE forecast_jobs SET status='queued',failure_code=NULL,failure_message=NULL
                    WHERE job_uuid=CAST(:job_uuid AS uuid) AND tenant_id=:tenant_id AND status='failed'
                """), p)
            elif job.kind == "forecast_training":
                await session.execute(text("""
                    UPDATE forecast_training_runs SET status='queued',error_code=NULL,error_message=NULL
                    WHERE training_uuid=CAST(:training_uuid AS uuid) AND tenant_id=:tenant_id
                      AND status='failed'
                """), p)
            await session.commit()

    async def fail(self, job: QueuedJob, exc: Exception) -> None:
        safe_message = f"{type(exc).__name__}: worker execution failed"[:1000]
        async with self.database.session_factory() as session:
            p = job.payload | {"message": safe_message}
            if job.kind == "dataset_import":
                await session.execute(text("""
                    UPDATE market_datasets SET status='rejected',limitations='["worker_execution_failed"]'::jsonb
                    WHERE id=:dataset_id AND tenant_id=:tenant_id AND status='validating'
                """), p)
            elif job.kind == "product_parse":
                await session.execute(text("""
                    UPDATE product_parse_jobs SET status='failed',failure_code='WORKER_EXECUTION_FAILED',
                      failure_message=:message,retryable=true,completed_at=now()
                    WHERE parse_job_id=CAST(:parse_job_id AS uuid) AND tenant_id=:tenant_id
                      AND status NOT IN ('succeeded','partial_succeeded')
                """), p)
            elif job.kind == "forecast_training":
                await session.execute(text("""
                    UPDATE forecast_training_runs SET status='failed',
                      error_code='WORKER_EXECUTION_FAILED',error_message=:message,completed_at=now()
                    WHERE training_uuid=CAST(:training_uuid AS uuid) AND tenant_id=:tenant_id
                      AND status<>'succeeded'
                """), p)
            await session.commit()

    async def recover(self, queue: RedisJobQueue) -> None:
        async with self.database.session_factory() as session:
            await session.execute(text("""
                UPDATE authorized_collect_sources SET last_status='failed',last_error='worker_recovered_stale_queue'
                 WHERE last_status='queued' AND last_queued_at < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
            """))
            await session.execute(text("""
                UPDATE policy_sources SET last_status='failed',last_error='worker_recovered_stale_queue'
                 WHERE last_status='queued' AND last_queued_at < CURRENT_TIMESTAMP - INTERVAL '10 minutes'
            """))
            analyses = (await session.execute(text("""
                SELECT id task_id,tenant_id,task_uuid::text FROM analysis_tasks WHERE status='queued'
            """))).mappings().all()
            parses = (await session.execute(text("""
                SELECT tenant_id,parse_job_id::text FROM product_parse_jobs WHERE status='queued'
            """))).mappings().all()
            datasets = (await session.execute(text("""
                SELECT tenant_id,id dataset_id FROM market_datasets WHERE status='validating'
            """))).mappings().all()
            forecasts = (await session.execute(text("""
                SELECT tenant_id,job_uuid::text FROM forecast_jobs WHERE status='queued'
            """))).mappings().all()
            trainings = (await session.execute(text("""
                SELECT tenant_id,training_uuid::text FROM forecast_training_runs WHERE status='queued'
            """))).mappings().all()
            controls = (await session.execute(text("""
                SELECT event_uuid::text,event_type FROM workflow_control_events
                 WHERE event_type IN ('auto_retry','safe_stop')
                   AND (status='pending' OR (status='enqueued' AND enqueued_at<now()-interval '5 minutes'))
            """))).mappings().all()
        for row in analyses:
            payload = dict(row); await queue.enqueue("analysis", payload, job_id=f"analysis:{payload['task_uuid']}")
        for row in parses:
            payload = dict(row); await queue.enqueue("product_parse", payload, job_id=f"parse:{payload['parse_job_id']}")
        for row in datasets:
            payload = dict(row); await queue.enqueue("dataset_import", payload, job_id=f"dataset:{payload['dataset_id']}")
        for row in forecasts:
            payload = dict(row); await queue.enqueue("forecast", payload, job_id=f"forecast:{payload['job_uuid']}")
        for row in trainings:
            payload = dict(row); await queue.enqueue("forecast_training", payload,
                                                     job_id=f"forecast-training:{payload['training_uuid']}")
        for row in controls:
            payload = dict(row); await queue.enqueue("admin_control", payload,
                                                     job_id=f"admin-control:{payload['event_uuid']}")


async def run_worker() -> None:
    settings = get_settings()
    if settings.job_queue_mode != "redis":
        raise RuntimeError("Worker requires JOB_QUEUE_MODE=redis")
    logger = configure_logging(settings.log_level)
    database = Database(settings)
    queue = RedisJobQueue.from_settings(settings)
    handlers = JobHandlers(settings, database)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await queue.ensure_group()
    from .services.schema_bootstrap import bootstrap_runtime_schema
    async with database.session_factory() as session:
        await bootstrap_runtime_schema(session)
        await session.commit()
    await handlers.recover(queue)
    await handlers.enqueue_due_authorized_signals(queue)
    await handlers.deliver_pending_notifications()

    async def consume(index: int) -> None:
        consumer = f"{socket.gethostname()}-{index}"
        while not stop.is_set():
            try:
                await queue.consume_once(consumer, handlers.handle, handlers.retry, handlers.fail)
            except Exception:
                logger.exception("worker_consume_failed", extra={"consumer": consumer})
                await asyncio.sleep(1)

    async def maintain_periodically() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=30)
            except TimeoutError:
                try:
                    await handlers.recover(queue)
                except Exception:
                    logger.exception("worker_recovery_failed")
                try:
                    await handlers.enqueue_due_authorized_signals(queue)
                except Exception:
                    logger.exception("worker_signal_scheduler_failed")
                try:
                    await handlers.deliver_pending_notifications()
                except Exception:
                    logger.exception("worker_notification_delivery_failed")

    tasks = [asyncio.create_task(consume(i)) for i in range(settings.worker_concurrency)]
    tasks.append(asyncio.create_task(maintain_periodically()))
    await stop.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await queue.close()
    await database.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
