"""Apply runtime DDL once at process start, never on request handlers."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..config import ApiSettings
from ..repositories.workspace_repository import WorkspaceRepository
from .authorized_signals import AuthorizedSignalService
from .competitor_tracking import CompetitorTrackingService
from .notifications import NotificationService
from .password_reset_service import PasswordResetService
from .tenant_security import verify_tenant_security


async def bootstrap_runtime_schema(
    session: AsyncSession,
    settings: ApiSettings | None = None,
) -> None:
    if settings is not None and settings.database_allow_runtime_ddl:
        await WorkspaceRepository().ensure_schema(session)
        await CompetitorTrackingService().ensure_schema(session)
        await AuthorizedSignalService().ensure_schema(session)
        await NotificationService().ensure_schema(session)
        await PasswordResetService().ensure_schema(session)
    lifecycle_ready = await session.scalar(text("""
        SELECT to_regclass('furniscope.analysis_workspace_turns') IS NOT NULL
           AND to_regclass('furniscope.analysis_context_snapshots') IS NOT NULL
           AND to_regclass('furniscope.analysis_workspace_states') IS NOT NULL
           AND to_regclass('furniscope.knowledge_bases') IS NOT NULL
           AND to_regclass('furniscope.password_reset_requests') IS NOT NULL
           AND to_regclass('furniscope.tenant_legal_holds') IS NOT NULL
           AND to_regclass('furniscope.tenant_deletion_requests') IS NOT NULL
           AND to_regclass('furniscope.tenant_deletion_certificates') IS NOT NULL
           AND to_regclass('furniscope.deployment_cells') IS NOT NULL
           AND to_regclass('furniscope.tenant_placements') IS NOT NULL
           AND to_regclass('furniscope.tenant_migration_jobs') IS NOT NULL
           AND to_regclass('furniscope.knowledge_chunk_embeddings') IS NOT NULL
           AND to_regclass('furniscope.table_scaling_policies') IS NOT NULL
           AND to_regclass('furniscope.data_metric_catalog') IS NOT NULL
           AND to_regclass('furniscope.sales_facts_daily') IS NOT NULL
           AND to_regclass('furniscope.inventory_facts_daily') IS NOT NULL
           AND to_regclass('furniscope.data_query_executions') IS NOT NULL
           AND to_regclass('furniscope.product_groups') IS NOT NULL
           AND to_regclass('furniscope.product_group_members') IS NOT NULL
           AND to_regclass('furniscope.product_import_jobs') IS NOT NULL
           AND to_regclass('furniscope.product_import_rows') IS NOT NULL
           AND to_regclass('furniscope.agent_goals') IS NOT NULL
           AND to_regclass('furniscope.agent_runs') IS NOT NULL
           AND to_regclass('furniscope.agent_artifacts') IS NOT NULL
           AND to_regclass('furniscope.market_intelligence_batches') IS NOT NULL
           AND to_regclass('furniscope.market_intelligence_lineage') IS NOT NULL
           AND EXISTS(
             SELECT FROM information_schema.columns
              WHERE table_schema='furniscope' AND table_name='customer_memories'
                AND column_name='effective_at'
           )
           AND EXISTS(
             SELECT FROM information_schema.columns
              WHERE table_schema='furniscope' AND table_name='tenants'
                AND column_name='data_class'
           )
           AND EXISTS(
             SELECT FROM furniscope.schema_migrations
              WHERE version='v3_37_operational_outcomes'
           )
           AND to_regprocedure(
             'furniscope.rename_tenant_sku_facts(bigint,text,text)'
           ) IS NOT NULL
    """))
    if lifecycle_ready is not True:
        raise RuntimeError(
            "Database migration v3_37_operational_outcomes is required"
        )
    if settings is not None:
        cell_ready = await session.scalar(text("""
            SELECT EXISTS(
              SELECT FROM furniscope.deployment_cells
               WHERE cell_code=:cell AND region_code=:region AND status IN ('active','draining')
            )
        """), {"cell": settings.deployment_cell_code, "region": settings.deployment_region})
        if cell_ready is not True:
            raise RuntimeError(
                f"Deployment cell is not registered: {settings.deployment_cell_code}"
            )
    await verify_tenant_security(
        session,
        require_runtime_separation=bool(
            settings and settings.database_require_runtime_role_separation
        ),
    )
