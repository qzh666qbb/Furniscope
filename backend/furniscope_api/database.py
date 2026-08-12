"""Async SQLAlchemy engine and transaction-scoped session dependencies."""

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import ApiSettings


class Database:
    def __init__(self, settings: ApiSettings) -> None:
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            connect_args={
                "command_timeout": settings.database_command_timeout_seconds,
                "server_settings": {"search_path": "furniscope,public"},
            },
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            result = await connection.execute(
                text(
                    """
                    SELECT count(*) = 5
                      FROM unnest(ARRAY[
                        'tenants', 'users', 'auth_sessions',
                        'api_idempotency_records', 'analysis_tasks'
                      ]::text[]) AS required_table(name)
                     WHERE to_regclass('furniscope.' || required_table.name) IS NOT NULL
                    """
                )
            )
            if result.scalar_one() is not True:
                raise RuntimeError("PostgreSQL is reachable but FurniScope V3 schema is incomplete")

    async def close(self) -> None:
        await self.engine.dispose()
