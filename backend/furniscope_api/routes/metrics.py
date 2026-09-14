"""Prometheus scrape endpoint with API, task, model, and database-pool metrics."""

from __future__ import annotations

from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.responses import Response

from ..dependencies import DatabaseSession

router = APIRouter(tags=["Infrastructure"])


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, session: DatabaseSession) -> Response:
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
        "# HELP furniscope_db_pool_connections SQLAlchemy database pool connections.",
        "# TYPE furniscope_db_pool_connections gauge",
        f'furniscope_db_pool_connections{{state="checked_in"}} {pool.checkedin()}',
        f'furniscope_db_pool_connections{{state="checked_out"}} {pool.checkedout()}',
        f'furniscope_db_pool_connections{{state="overflow"}} {pool.overflow()}',
    ]
    return Response("\n".join(lines) + "\n", media_type=CONTENT_TYPE_LATEST)
