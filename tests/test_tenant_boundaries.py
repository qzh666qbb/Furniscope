"""Real PostgreSQL checks under a non-owner role, including pooled transactions."""

import os
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from furniscope_api.config import ApiSettings
from furniscope_api.database import (
    Database,
    TenantPlacementUnavailable,
    bind_tenant_session,
    tenant_pool_setup,
)
from furniscope_api.services.tenant_security import GOVERNANCE_ROLES, verify_tenant_security

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not os.getenv("FURNISCOPE_TEST_DATABASE_URL"), reason="needs isolated PostgreSQL"),
]


async def make_tenants(database):
    identities = []
    async with database.session_factory() as session:
        for _ in range(2):
            tenant = await session.scalar(text("""
                INSERT INTO tenants(tenant_code,name,status) VALUES(:code,'隔离测试','active') RETURNING id
            """), {"code": f"RLS_{uuid4().hex[:16].upper()}"})
            user = await session.scalar(text("""
                INSERT INTO users(tenant_id,email,password_hash,name,role_code,status)
                VALUES(:t,:email,'not-a-login','测试','user','active') RETURNING id
            """), {"t": tenant, "email": f"{uuid4().hex}@example.invalid"})
            product = await session.scalar(text("""
                INSERT INTO products(tenant_id,sku,name,category_code,created_by)
                VALUES(:t,'SAME-SKU','隔离沙发','sofa',:u) RETURNING id
            """), {"t": tenant, "u": user})
            identities.append((tenant, user, product))
        await session.commit()
    return identities


def database():
    dsn = os.environ["FURNISCOPE_TEST_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    return Database(ApiSettings(database_url=dsn, app_env="test",
                                database_pool_size=1, database_max_overflow=0))


async def test_role_read_write_compound_fk_and_pool_reset():
    db = database()
    (a, au, ap), (b, bu, bp) = await make_tenants(db)
    try:
        # No identity: restricted role fails closed.
        async with db.session_factory() as session:
            await session.execute(text("SET LOCAL ROLE furniscope_tenant"))
            assert await session.scalar(text("SELECT count(*) FROM products")) == 0
            await session.rollback()
        for tenant, expected in [(a, ap), (b, bp), (a, ap)]:
            async with db.session_factory() as session:
                # Open transaction before binding, exactly as auth verification does.
                await session.scalar(text("SELECT 1"))
                await bind_tenant_session(session, tenant)
                for _ in range(2):
                    assert await session.scalar(text("SELECT current_user")) == "furniscope_tenant"
                    ids = (await session.execute(text("SELECT id FROM products"))).scalars().all()
                    assert ids == [expected]  # Intentionally no tenant filter.
                    await session.commit()
                assert (await session.execute(text("UPDATE products SET name='leak' WHERE id=:id"),
                                              {"id": bp if tenant == a else ap})).rowcount == 0
                with pytest.raises(DBAPIError, match="row-level security"):
                    await session.execute(text("""
                        INSERT INTO products(tenant_id,sku,name,category_code)
                        VALUES(:t,'EVIL','bad','sofa')
                    """), {"t": b if tenant == a else a})
                await session.rollback()
                with pytest.raises(DBAPIError, match="foreign key"):
                    await session.execute(text("""
                        INSERT INTO product_profile_versions(tenant_id,product_id,version_no,status,
                          source_summary,schema_version)
                        VALUES(:t,:p,1,'draft','{}','test')
                    """), {"t": tenant, "p": bp if tenant == a else ap})
                await session.rollback()
                with pytest.raises(ValueError, match="switch"):
                    await bind_tenant_session(session, b if tenant == a else a)
        async with db.session_factory() as admin:
            assert await admin.scalar(text("SELECT current_user")) != "furniscope_tenant"
            assert not await admin.scalar(text("SELECT current_setting('furniscope.tenant_id',true)"))
            # Compound FK also protects trusted platform writes.
            with pytest.raises(DBAPIError, match="foreign key"):
                await admin.execute(text("""
                    INSERT INTO product_profile_versions(tenant_id,product_id,version_no,status,
                      source_summary,schema_version) VALUES(:t,:p,1,'draft','{}','test')
                """), {"t": a, "p": bp})
    finally:
        await db.close()


async def test_private_model_visibility_and_deployment_owner_guard():
    db = database()
    (a, _, _), (b, _, _) = await make_tenants(db)
    try:
        async with db.session_factory() as admin:
            model = await admin.scalar(text("""
                INSERT INTO forecast_models(model_code,version,owner_tenant_id,model_scope,
                    state_uri,state_checksum) VALUES(:code,'test',:t,'tenant_private','test://state',:sha)
                RETURNING id
            """), {"code": uuid4().hex, "t": a, "sha": "1" * 64})
            await admin.commit()
            with pytest.raises(DBAPIError, match="does not belong"):
                await admin.execute(text("""
                    INSERT INTO forecast_model_deployments(tenant_id,model_id) VALUES(:t,:m)
                """), {"t": b, "m": model})
            await admin.rollback()
            with pytest.raises(DBAPIError, match="ownership is immutable"):
                await admin.execute(text("UPDATE forecast_models SET owner_tenant_id=:t WHERE id=:m"),
                                    {"t": b, "m": model})
            await admin.rollback()
        async with db.session_factory() as session:
            await bind_tenant_session(session, b)
            assert await session.scalar(text("SELECT count(*) FROM forecast_models WHERE id=:m"),
                                        {"m": model}) == 0
            with pytest.raises(DBAPIError, match="does not belong"):
                await session.execute(text("""
                    INSERT INTO forecast_model_deployments(tenant_id,model_id) VALUES(:t,:m)
                """), {"t": b, "m": model})
    finally:
        await db.close()


async def test_asyncpg_reacquisition_restricts_each_tenant():
    db = database()
    (a, _, ap), (b, _, bp) = await make_tenants(db)
    await db.close()
    for tenant, expected in [(a, ap), (b, bp)]:
        pool = await asyncpg.create_pool(os.environ["FURNISCOPE_TEST_DATABASE_URL"],
                                        min_size=1, max_size=1, setup=tenant_pool_setup(tenant))
        try:
            for _ in range(3):
                async with pool.acquire() as connection:
                    async with connection.transaction():
                        assert await connection.fetchval("SELECT current_user") == "furniscope_tenant"
                        assert await connection.fetchval("SELECT count(*) FROM furniscope.products") == 1
                        assert await connection.fetchval("SELECT id FROM furniscope.products") == expected
        finally:
            await pool.close()


async def test_force_rls_roles_and_policy_manifest_are_valid():
    db = database()
    try:
        async with db.session_factory() as session:
            await verify_tenant_security(session, require_runtime_separation=False)
            role_count = await session.scalar(text("""
                SELECT count(*) FROM pg_roles
                 WHERE rolname=ANY(CAST(:roles AS text[]))
                   AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcanlogin
            """), {"roles": sorted(GOVERNANCE_ROLES)})
            assert role_count == len(GOVERNANCE_ROLES)
            unforced = (await session.execute(text("""
                SELECT c.relname
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='furniscope' AND c.relkind IN ('r','p')
                   AND NOT c.relispartition
                   AND (
                     EXISTS(
                       SELECT FROM pg_attribute a
                        WHERE a.attrelid=c.oid AND a.attname='tenant_id'
                          AND NOT a.attisdropped
                     )
                     OR c.relname IN ('tenants','cluster_members','forecast_models')
                   )
                   AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity)
            """))).scalars().all()
            assert unforced == []
    finally:
        await db.close()


async def test_cell_mismatch_freeze_and_write_fence_fail_closed():
    db = database()
    (tenant, _, product), _ = await make_tenants(db)
    other_cell = f"cell-{uuid4().hex[:12]}"
    migration_uuid = uuid4()
    try:
        async with db.session_factory() as admin:
            await admin.execute(text("""
                INSERT INTO deployment_cells(
                  cell_code,display_name,region_code,cell_kind,status,is_default
                ) VALUES(:cell,'test dedicated cell','test-region','dedicated','active',false)
            """), {"cell": other_cell})
            await admin.execute(text("""
                UPDATE tenant_placements
                   SET cell_code=:cell,status='active',migration_uuid=NULL
                 WHERE tenant_id=:tenant
            """), {"cell": other_cell, "tenant": tenant})
            await admin.commit()

        async with db.session_factory() as session:
            with pytest.raises(TenantPlacementUnavailable) as mismatch:
                await bind_tenant_session(session, tenant)
            assert mismatch.value.actual_cell == other_cell
            assert mismatch.value.status == "active"
            await session.rollback()

        async with db.session_factory() as admin:
            await admin.execute(text("""
                UPDATE tenant_placements
                   SET cell_code='cell-local',status='frozen',
                       migration_uuid=:migration
                 WHERE tenant_id=:tenant
            """), {"migration": migration_uuid, "tenant": tenant})
            await admin.commit()

        async with db.session_factory() as session:
            with pytest.raises(TenantPlacementUnavailable) as frozen:
                await bind_tenant_session(session, tenant)
            assert frozen.value.actual_cell == "cell-local"
            assert frozen.value.status == "frozen"
            await session.rollback()

        async with db.session_factory() as admin:
            with pytest.raises(DBAPIError, match="writes are fenced"):
                await admin.execute(
                    text("UPDATE products SET name='blocked' WHERE id=:product"),
                    {"product": product},
                )
            await admin.rollback()
    finally:
        async with db.session_factory() as admin:
            await admin.execute(text("""
                UPDATE tenant_placements
                   SET cell_code='cell-local',status='active',migration_uuid=NULL
                 WHERE tenant_id=:tenant
            """), {"tenant": tenant})
            await admin.commit()
        await db.close()
