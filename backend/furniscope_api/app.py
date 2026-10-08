"""FastAPI application factory."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import ApiSettings, get_settings
from .database import Database
from .errors import install_exception_handlers
from .logging import configure_logging
from .middleware import RequestContextMiddleware
from .routes.health import router as health_router
from .routes.auth import router as auth_router
from .routes.products import router as products_router
from .routes.product_imports import router as product_imports_router
from .routes.datasets import router as datasets_router
from .routes.parse_jobs import router as parse_jobs_router
from .routes.analysis_tasks import router as analysis_tasks_router
from .routes.workspaces import router as workspaces_router
from .routes.context_lifecycle import router as context_lifecycle_router
from .routes.knowledge_bases import router as knowledge_bases_router
from .routes.data_queries import router as data_queries_router
from .routes.agent_runtime import router as agent_runtime_router
from .routes.forecasts import router as forecasts_router
from .routes.forecast_training import router as forecast_training_router
from .routes.reports import router as reports_router
from .routes.confirmations import router as confirmations_router
from .routes.insights import router as insights_router
from .routes.market_intelligence import router as market_intelligence_router
from .routes.enterprise import router as enterprise_router
from .routes.tracking import router as tracking_router
from .routes.signals import router as signals_router
from .routes.notifications import router as notifications_router
from .routes.internal_model import router as internal_model_router
from .routes.internal_forecast import router as internal_forecast_router
from .routes.admin import router as admin_router
from .routes.metrics import router as metrics_router
from .services.forecast_runtime import TenantForecastRuntimeRegistry
from .job_queue import RedisJobQueue


def create_app(settings: ApiSettings | None = None, database: Database | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    runtime_database = database or Database(runtime_settings)
    runtime_admin_database = runtime_database
    if (
        database is None
        and runtime_settings.admin_database_url
        and runtime_settings.admin_database_url != runtime_settings.database_url
    ):
        runtime_admin_database = Database(
            runtime_settings,
            database_url=runtime_settings.admin_database_url,
        )
    logger = configure_logging(runtime_settings.log_level)
    job_queue = (RedisJobQueue.from_settings(runtime_settings)
                 if runtime_settings.job_queue_mode == "redis" else None)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("application_started")
        try:
            session_factory = getattr(runtime_admin_database, "session_factory", None)
            if session_factory is not None:
                from .services.schema_bootstrap import bootstrap_runtime_schema
                async with session_factory() as session:
                    await bootstrap_runtime_schema(session, runtime_settings)
                    await session.commit()
            if job_queue is not None:
                await job_queue.ensure_group()
            yield
        finally:
            if job_queue is not None:
                await job_queue.close()
            if runtime_admin_database is not runtime_database:
                await runtime_admin_database.close()
            await runtime_database.close()
            logger.info("application_stopped")

    app = FastAPI(
        title=runtime_settings.app_name,
        version="0.1.0",
        debug=runtime_settings.app_debug,
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.database = runtime_database
    app.state.admin_database = runtime_admin_database
    app.state.logger = logger
    app.state.forecast_runtime = TenantForecastRuntimeRegistry(runtime_settings)
    app.state.job_queue = job_queue
    app.add_middleware(RequestContextMiddleware)
    if runtime_settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=runtime_settings.cors_allowed_origins,
            allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
        )
    install_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(metrics_router)
    app.include_router(auth_router)
    app.include_router(products_router)
    app.include_router(product_imports_router)
    app.include_router(datasets_router)
    app.include_router(parse_jobs_router)
    app.include_router(analysis_tasks_router)
    app.include_router(workspaces_router)
    app.include_router(context_lifecycle_router)
    app.include_router(knowledge_bases_router)
    app.include_router(data_queries_router)
    app.include_router(agent_runtime_router)
    app.include_router(forecasts_router)
    app.include_router(forecast_training_router)
    app.include_router(reports_router)
    app.include_router(confirmations_router)
    app.include_router(insights_router)
    app.include_router(market_intelligence_router)
    app.include_router(enterprise_router)
    app.include_router(tracking_router)
    app.include_router(signals_router)
    app.include_router(notifications_router)
    app.include_router(internal_model_router)
    app.include_router(internal_forecast_router)
    app.include_router(admin_router)
    return app


app = create_app()
