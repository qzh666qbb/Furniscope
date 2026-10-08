"""Async SQLAlchemy engine and transaction-scoped session dependencies."""

from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from .config import ApiSettings


class TenantPlacementUnavailable(RuntimeError):
    def __init__(
        self,
        tenant_id: int,
        *,
        expected_cell: str,
        actual_cell: str | None,
        status: str | None,
    ) -> None:
        super().__init__(
            f"tenant {tenant_id} placement unavailable: "
            f"expected={expected_cell}, actual={actual_cell}, status={status}"
        )
        self.tenant_id = tenant_id
        self.expected_cell = expected_cell
        self.actual_cell = actual_cell
        self.status = status


def _assert_tenant_placement(connection, tenant_id: int, cell_code: str) -> None:
    placement = connection.execute(
        text(
            """
            SELECT cell_code,status
              FROM furniscope.tenant_placements
             WHERE tenant_id=:tenant
            """
        ),
        {"tenant": tenant_id},
    ).mappings().one_or_none()
    if (
        placement is None
        or placement["cell_code"] != cell_code
        or placement["status"] != "active"
    ):
        raise TenantPlacementUnavailable(
            tenant_id,
            expected_cell=cell_code,
            actual_cell=placement["cell_code"] if placement else None,
            status=placement["status"] if placement else None,
        )


@event.listens_for(Session, "after_begin")
def _restore_tenant_transaction(session, _transaction, connection):
    """SET LOCAL expires at commit; session identity survives until session close."""
    tenant_id = session.info.get("furniscope_tenant_id")
    if tenant_id is not None:
        _assert_tenant_placement(
            connection,
            tenant_id,
            session.info.get("furniscope_cell_code", "cell-local"),
        )
        connection.execute(text("SET LOCAL ROLE furniscope_tenant"))
        connection.execute(text("SET LOCAL search_path TO furniscope,public"))
        connection.execute(text("SELECT set_config('furniscope.tenant_id',:tenant,true)"),
                           {"tenant": str(tenant_id)})


async def bind_tenant_session(session: AsyncSession, tenant_id: int) -> None:
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        raise ValueError("A verified positive tenant id is required")
    previous = session.info.get("furniscope_tenant_id")
    if previous is not None and previous != tenant_id:
        raise ValueError("A session cannot switch tenant identity")
    cell_code = session.info.get("furniscope_cell_code", "cell-local")
    placement = (
        await session.execute(
            text(
                """
                SELECT cell_code,status
                  FROM furniscope.tenant_placements
                 WHERE tenant_id=:tenant
                """
            ),
            {"tenant": tenant_id},
        )
    ).mappings().one_or_none()
    if (
        placement is None
        or placement["cell_code"] != cell_code
        or placement["status"] != "active"
    ):
        raise TenantPlacementUnavailable(
            tenant_id,
            expected_cell=cell_code,
            actual_cell=placement["cell_code"] if placement else None,
            status=placement["status"] if placement else None,
        )
    session.info["furniscope_tenant_id"] = tenant_id
    # Authentication may already have opened a transaction. The listener covers
    # future transactions; these statements also restrict the current one.
    await session.execute(text("SET LOCAL ROLE furniscope_tenant"))
    await session.execute(text("SET LOCAL search_path TO furniscope,public"))
    await session.execute(text("SELECT set_config('furniscope.tenant_id',:tenant,true)"),
                          {"tenant": str(tenant_id)})


def tenant_pool_setup(tenant_id: int, cell_code: str = "cell-local"):
    """asyncpg acquisition hook: reset on release, rebind on EVERY acquisition."""
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
        raise ValueError("A verified positive tenant id is required")

    async def setup(connection):
        # asyncpg's default reset does not guarantee RESET ROLE. Verify routing
        # as the trusted session identity before binding the next tenant.
        await connection.execute("RESET ROLE")
        placement = await connection.fetchrow(
            """
            SELECT cell_code,status
              FROM furniscope.tenant_placements
             WHERE tenant_id=$1
            """,
            tenant_id,
        )
        if (
            placement is None
            or placement["cell_code"] != cell_code
            or placement["status"] != "active"
        ):
            raise TenantPlacementUnavailable(
                tenant_id,
                expected_cell=cell_code,
                actual_cell=placement["cell_code"] if placement else None,
                status=placement["status"] if placement else None,
            )
        await connection.execute("SET ROLE furniscope_tenant")
        await connection.execute("SET search_path TO furniscope,public")
        await connection.execute("SELECT set_config('furniscope.tenant_id',$1,false)", str(tenant_id))
    return setup


class Database:
    def __init__(self, settings: ApiSettings, *, database_url: str | None = None) -> None:
        self.engine: AsyncEngine = create_async_engine(
            database_url or settings.database_url,
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            connect_args={
                "command_timeout": settings.database_command_timeout_seconds,
                "server_settings": {
                    "search_path": "furniscope,public",
                    "furniscope.cell_code": settings.deployment_cell_code,
                },
            },
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            expire_on_commit=False,
            info={"furniscope_cell_code": settings.deployment_cell_code},
        )

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
