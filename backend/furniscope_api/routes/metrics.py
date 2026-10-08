"""Prometheus scrape endpoint with API, task, model, and database-pool metrics."""

from __future__ import annotations

from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.responses import Response

from ..dependencies import AdminDatabaseSession

router = APIRouter(tags=["Infrastructure"])


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, session: AdminDatabaseSession) -> Response:
    task_rows = (await session.execute(text("""
        SELECT status,count(*) count,
               COALESCE(avg(EXTRACT(epoch FROM (updated_at-created_at)))
                        FILTER (WHERE status IN ('succeeded','partial_succeeded','failed','cancelled')),0) avg_seconds
          FROM furniscope.analysis_tasks GROUP BY status
    """))).mappings().all()
    model_rows = (await session.execute(text("""
        SELECT provider,status,count(*) count FROM furniscope.ai_model_runs GROUP BY provider,status
    """))).mappings().all()
    forecast_rows = (await session.execute(text("""
        SELECT status,count(*) count FROM furniscope.forecast_jobs GROUP BY status
    """))).mappings().all()
    data_query_row = (await session.execute(text("""
        SELECT count(*)::bigint AS count,
               COALESCE(avg(duration_ms),0)::float8 AS average_duration_ms
          FROM furniscope.data_query_executions
    """))).mappings().one()
    knowledge_rows = (await session.execute(text("""
        SELECT status,count(*)::bigint AS count
          FROM furniscope.knowledge_index_jobs GROUP BY status
    """))).mappings().all()
    tenant_queue_rows = []
    queue = request.app.state.job_queue
    queue_available = 0
    if queue is not None:
        try:
            tenant_queue_rows = await queue.tenant_metrics_snapshot()
            queue_available = 1
        except Exception:
            request.app.state.logger.exception("tenant_queue_metrics_failed")
    pool = request.app.state.database.engine.pool
    lines = [generate_latest().decode("utf-8").rstrip(),
             "# HELP furniscope_analysis_tasks Analysis task count by current status.",
             "# TYPE furniscope_analysis_tasks gauge"]
    for row in task_rows:
        status = _label(row["status"])
        lines.append(f'furniscope_analysis_tasks{{status="{status}"}} {int(row["count"])}')
        lines.append(f'furniscope_analysis_task_average_duration_seconds{{status="{status}"}} {float(row["avg_seconds"])}')
    lines += ["# HELP furniscope_model_runs_total Persisted model calls by provider and status.",
              "# TYPE furniscope_model_runs_total gauge"]
    for row in model_rows:
        lines.append(
            f'furniscope_model_runs_total{{provider="{_label(row["provider"])}",status="{_label(row["status"])}"}} {int(row["count"])}'
        )
    lines += ["# HELP furniscope_forecast_jobs Forecast job count by current status.",
              "# TYPE furniscope_forecast_jobs gauge"]
    for row in forecast_rows:
        lines.append(f'furniscope_forecast_jobs{{status="{_label(row["status"])}"}} {int(row["count"])}')
    lines += [
        "# HELP furniscope_data_query_executions Persisted controlled data queries.",
        "# TYPE furniscope_data_query_executions gauge",
        f'furniscope_data_query_executions {int(data_query_row["count"])}',
        "# HELP furniscope_data_query_average_duration_ms Average persisted query latency.",
        "# TYPE furniscope_data_query_average_duration_ms gauge",
        f'furniscope_data_query_average_duration_ms {float(data_query_row["average_duration_ms"])}',
        "# HELP furniscope_knowledge_index_jobs Knowledge index jobs by status.",
        "# TYPE furniscope_knowledge_index_jobs gauge",
    ]
    for row in knowledge_rows:
        lines.append(
            f'furniscope_knowledge_index_jobs{{status="{_label(row["status"])}"}} '
            f'{int(row["count"])}'
        )
    lines += [
        "# HELP furniscope_job_queue_available Whether Redis tenant queue metrics are available.",
        "# TYPE furniscope_job_queue_available gauge",
        f"furniscope_job_queue_available {queue_available}",
        "# HELP furniscope_tenant_jobs Outstanding and running jobs by tenant.",
        "# TYPE furniscope_tenant_jobs gauge",
    ]
    for row in tenant_queue_rows:
        tenant_id = _label(row["tenant_id"])
        lines.append(
            f'furniscope_tenant_jobs{{tenant_id="{tenant_id}",state="queued"}} '
            f'{int(row["queued"])}'
        )
        lines.append(
            f'furniscope_tenant_jobs{{tenant_id="{tenant_id}",state="running"}} '
            f'{int(row["running"])}'
        )
    lines += [
        "# HELP furniscope_tenant_job_events_total Tenant queue scheduling outcomes.",
        "# TYPE furniscope_tenant_job_events_total counter",
    ]
    for row in tenant_queue_rows:
        tenant_id = _label(row["tenant_id"])
        for event in ("deferred", "completed", "failed"):
            lines.append(
                f'furniscope_tenant_job_events_total{{tenant_id="{tenant_id}",'
                f'event="{event}"}} {int(row[event])}'
            )
    lines += [
        "# HELP furniscope_db_pool_connections SQLAlchemy database pool connections.",
        "# TYPE furniscope_db_pool_connections gauge",
        f'furniscope_db_pool_connections{{state="checked_in"}} {pool.checkedin()}',
        f'furniscope_db_pool_connections{{state="checked_out"}} {pool.checkedout()}',
        f'furniscope_db_pool_connections{{state="overflow"}} {pool.overflow()}',
    ]
    return Response("\n".join(lines) + "\n", media_type=CONTENT_TYPE_LATEST)
