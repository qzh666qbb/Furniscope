"""Create two NEW local test databases; verify fresh install and additive upgrade."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text

from furniscope_api.config import ApiSettings
from furniscope_api.database import Database
from furniscope_api.services.schema_bootstrap import bootstrap_runtime_schema

ROOT = Path(__file__).resolve().parents[1]
ORDER = ["v3_15_enterprise_data", "v3_18_customer_memory", "v3_16_tenant_boundaries",
         "v3_17_opportunity_policy", "v3_19_training_leases", "v3_20_forecast_routing",
         "v3_21_import_templates", "v3_22_opportunity_outcomes",
         "v3_23_context_lifecycle", "v3_24_memory_agent_governance",
         "v3_25_database_security_baseline",
         "v3_26_competitor_watch_product_binding",
         "v3_27_audit_rbac",
         "v3_28_tenant_deletion_lifecycle",
         "v3_29_tenant_cell_routing",
         "v3_30_tenant_query_optimization",
         "v3_31_controlled_data_query",
         "v3_32_product_catalog_imports", "v3_33_tenant_data_class",
         "v3_34_sku_fact_identity", "v3_35_agent_runtime",
         "v3_36_market_intelligence_governance",
         "v3_37_operational_outcomes"]


async def inspect(url):
    db = Database(ApiSettings(_env_file=None, app_env="test",
        database_url=url.replace("postgresql://", "postgresql+asyncpg://")))
    try:
        async with db.session_factory() as session:
            await bootstrap_runtime_schema(session)
            await session.commit()
            versions = (await session.execute(text("SELECT version FROM schema_migrations ORDER BY version"))).scalars().all()
            rows = (await session.execute(text("""
                SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='furniscope' AND c.relkind IN ('r','p')
                   AND NOT c.relispartition AND c.relrowsecurity
            """))).scalars().all()
            sentinels = (await session.execute(text("""
                SELECT tenant_code,name FROM tenants WHERE tenant_code='MIGRATION_SENTINEL'
            """))).mappings().all()
            probe_tenant = (await session.execute(text("""
                INSERT INTO tenants(tenant_code,name,status)
                VALUES('MIGRATION_RBAC_PROBE','migration rbac probe','active')
                RETURNING id
            """))).scalar_one()
            probe_user = (await session.execute(text("""
                INSERT INTO users(tenant_id,email,password_hash,name,role_code,status)
                VALUES(:tenant,'migration-rbac-probe@example.invalid','not-a-login-hash',
                       'migration probe','user','active')
                RETURNING id
            """), {"tenant": probe_tenant})).scalar_one()
            role_probe = (await session.execute(text("""
                SELECT r.role_code,count(p.permission_code)::int AS permission_count
                  FROM user_role_assignments a
                  JOIN tenant_roles r ON r.id=a.role_id AND r.tenant_id=a.tenant_id
                  LEFT JOIN role_permissions p
                    ON p.role_id=r.id AND p.tenant_id=r.tenant_id
                 WHERE a.tenant_id=:tenant AND a.user_id=:user
                 GROUP BY r.role_code
            """), {"tenant": probe_tenant, "user": probe_user})).mappings().one()
            audit_probe = (await session.execute(text("""
                INSERT INTO audit_logs(
                  tenant_id,actor_user_id,action_code,resource_type,resource_id,request_id
                ) VALUES(:tenant,:user,'migration.probe','tenant',:tenant,'migration-probe')
                RETURNING chain_sequence,previous_hash,event_hash
            """), {"tenant": probe_tenant, "user": probe_user})).mappings().one()
            cell_probe = (await session.execute(text("""
                SELECT p.cell_code,p.status,c.region_code
                  FROM tenant_placements p
                  JOIN deployment_cells c ON c.cell_code=p.cell_code
                 WHERE p.tenant_id=:tenant
            """), {"tenant": probe_tenant})).mappings().one()
            assert set(ORDER) <= set(versions), versions
            assert role_probe["role_code"] == "tenant_owner"
            assert role_probe["permission_count"] >= 1
            assert audit_probe["chain_sequence"] == 1
            assert len(audit_probe["previous_hash"]) == len(audit_probe["event_hash"]) == 64
            assert dict(cell_probe) == {
                "cell_code": "cell-local", "status": "active", "region_code": "local",
            }
            partition_probe = (await session.execute(text("""
                SELECT c.relkind::text AS relkind,
                       (SELECT count(*) FROM pg_inherits i WHERE i.inhparent=c.oid)::int
                         AS partitions,
                       c.relrowsecurity,c.relforcerowsecurity
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='furniscope'
                   AND c.relname='knowledge_chunk_embeddings'
            """))).mappings().one()
            assert dict(partition_probe) == {
                "relkind": "p",
                "partitions": 16,
                "relrowsecurity": True,
                "relforcerowsecurity": True,
            }
            query_tables = (await session.execute(text("""
                SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='furniscope'
                   AND c.relname=ANY(ARRAY[
                     'data_fact_projections','sales_facts_daily',
                     'inventory_facts_daily','data_query_executions'
                   ])
                 ORDER BY c.relname
            """))).mappings().all()
            assert len(query_tables) == 4
            assert all(
                row["relrowsecurity"] and row["relforcerowsecurity"]
                for row in query_tables
            )
            assert int(await session.scalar(text("""
                SELECT count(*) FROM data_metric_catalog WHERE is_active
            """)) or 0) == 10
            product_catalog_tables = (await session.execute(text("""
                SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='furniscope'
                   AND c.relname=ANY(ARRAY[
                     'product_groups','product_group_members',
                     'product_import_jobs','product_import_rows'
                   ])
                 ORDER BY c.relname
            """))).mappings().all()
            assert len(product_catalog_tables) == 4
            assert all(
                row["relrowsecurity"] and row["relforcerowsecurity"]
                for row in product_catalog_tables
            )
            sku_compare = (await session.execute(text("""
                SELECT c.is_generated,
                       to_regclass('furniscope.uk_products_tenant_sku_compare')
                         IS NOT NULL AS unique_index_exists
                  FROM information_schema.columns c
                 WHERE c.table_schema='furniscope'
                   AND c.table_name='products'
                   AND c.column_name='sku_compare_key'
            """))).mappings().one()
            assert dict(sku_compare) == {
                "is_generated": "ALWAYS", "unique_index_exists": True,
            }
            sku_rename_function = (await session.execute(text("""
                SELECT p.prosecdef,owner.rolname AS owner_name,
                       has_function_privilege(
                         'furniscope_tenant',p.oid,'EXECUTE'
                       ) AS tenant_execute,
                       NOT EXISTS(
                         SELECT 1
                           FROM aclexplode(
                             COALESCE(p.proacl,acldefault('f',p.proowner))
                           ) acl
                          WHERE acl.grantee=0
                            AND acl.privilege_type='EXECUTE'
                       ) AS public_execute_revoked
                  FROM pg_proc p
                  JOIN pg_namespace n ON n.oid=p.pronamespace
                  JOIN pg_roles owner ON owner.oid=p.proowner
                 WHERE n.nspname='furniscope'
                   AND p.oid='furniscope.rename_tenant_sku_facts(bigint,text,text)'::regprocedure
            """))).mappings().one()
            assert dict(sku_rename_function) == {
                "prosecdef": True,
                "owner_name": "furniscope_platform_admin",
                "tenant_execute": True,
                "public_execute_revoked": True,
            }
            intelligence_tables = (await session.execute(text("""
                SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='furniscope'
                   AND c.relname=ANY(ARRAY[
                     'market_intelligence_batches',
                     'market_intelligence_lineage'
                   ])
                 ORDER BY c.relname
            """))).mappings().all()
            assert len(intelligence_tables) == 2
            assert all(
                row["relrowsecurity"] and row["relforcerowsecurity"]
                for row in intelligence_tables
            )
            await session.rollback()
            return {"migrations": versions, "rls_tables": sorted(rows),
                    "startup_isolation_check": "passed", "rbac_trigger_check": "passed",
                    "audit_chain_trigger_check": "passed",
                    "cell_placement_trigger_check": "passed",
                    "partitioned_embedding_index_check": "passed",
                    "controlled_data_query_check": "passed",
                    "product_catalog_import_check": "passed",
                    "sku_fact_identity_check": "passed",
                    "market_intelligence_governance_check": "passed",
                    "sentinels": [dict(r) for r in sentinels]}
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", required=True, help="local postgresql://.../postgres")
    parser.add_argument("--psql", default="psql")
    parser.add_argument("--suffix", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = urlsplit(args.admin_url)
    if (url.scheme != "postgresql" or url.hostname not in {"127.0.0.1", "localhost"}
            or url.path != "/postgres" or not re.fullmatch(r"[a-z0-9_]{1,24}", args.suffix)):
        parser.error("须使用本地postgres管理库及小写字母数字后缀")
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"protocol": "migration-acceptance-v1", "databases": {},
              "migration_sha256": {name: hashlib.sha256(
                  (ROOT / "migrations" / f"{name}.sql").read_bytes()).hexdigest() for name in ORDER}}

    def sql(target, *, content=None, file=None, logfile):
        command = [args.psql, "-X", "-v", "ON_ERROR_STOP=1", target]
        if file:
            command += ["-f", str(file)]
        with logfile.open("a") as log:
            subprocess.run(command, input=content, text=True, stdout=log, stderr=log, check=True, cwd=ROOT)

    for mode in ("fresh", "upgrade"):
        name = f"furniscope_enterprise_test_{args.suffix}_{mode}"
        target = urlunsplit(url._replace(path=f"/{name}"))
        logfile = args.output / f"migration-{mode}.log"
        # CREATE DATABASE fails rather than resetting any existing database.
        sql(args.admin_url, content=f'CREATE DATABASE "{name}";', logfile=logfile)
        if mode == "fresh":
            sql(target, file=ROOT / "furniscope_postgresql_v3.sql", logfile=logfile)
        else:
            base = (ROOT / "furniscope_postgresql_v3.sql").read_text()
            base = "\n".join(line for line in base.splitlines() if not any(
                f"{version}.sql" in line for version in ORDER))
            sql(target, content=base, logfile=logfile)
            sql(target, content="""
                INSERT INTO furniscope.tenants(tenant_code,name,status)
                VALUES('MIGRATION_SENTINEL','preserve existing tenant','active');
            """, logfile=logfile)
            for version in ORDER:
                sql(target, file=ROOT / "migrations" / f"{version}.sql", logfile=logfile)
        first = asyncio.run(inspect(target))
        for version in ORDER:
            sql(target, file=ROOT / "migrations" / f"{version}.sql", logfile=logfile)
        repeated = asyncio.run(inspect(target))
        assert first == repeated, "重复迁移改变了版本、RLS或存量哨兵"
        if mode == "upgrade":
            assert first["sentinels"] == [{"tenant_code": "MIGRATION_SENTINEL",
                                          "name": "preserve existing tenant"}]
        result["databases"][mode] = {"name": name, **first, "repeat": "passed"}
        print(f"{mode}: install, startup RLS, repeat migration passed", flush=True)
    (args.output / "migration-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
