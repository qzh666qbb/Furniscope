"""FastAPI application factory."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from .config import ApiSettings, get_settings
from .database import Database
from .errors import install_exception_handlers
from .logging import configure_logging
from .middleware import RequestContextMiddleware
from .routes.health import router as health_router
from .routes.auth import router as auth_router
from .routes.products import router as products_router
from .routes.datasets import router as datasets_router
from .routes.parse_jobs import router as parse_jobs_router
from .routes.analysis_tasks import router as analysis_tasks_router


def create_app(settings: ApiSettings | None = None, database: Database | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    runtime_database = database or Database(runtime_settings)
    logger = configure_logging(runtime_settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("application_started")
        try:
            yield
        finally:
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
    app.state.logger = logger
    app.add_middleware(RequestContextMiddleware)
    install_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(products_router)
    app.include_router(datasets_router)
    app.include_router(parse_jobs_router)
    app.include_router(analysis_tasks_router)
    return app


app = create_app()
