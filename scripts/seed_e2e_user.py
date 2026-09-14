"""Seed the minimum tenant/user/product state for an opt-in full-stack browser test.

Credentials and the database URL are environment-only. The script is idempotent so
CI and local smoke runs can use the same stable product selector.
"""

from __future__ import annotations

import asyncio
import json
import os

import asyncpg

from furniscope_api.security.password import PasswordService


async def main() -> None:
    dsn = os.environ["FURNISCOPE_E2E_DATABASE_URL"]
    email = os.environ["FURNISCOPE_E2E_EMAIL"].strip().lower()
    password = os.environ["FURNISCOPE_E2E_PASSWORD"]
    connection = await asyncpg.connect(dsn)
    try:
        tenant_id = await connection.fetchval(
            """INSERT INTO furniscope.tenants(tenant_code,name,status)
               VALUES('E2E_BROWSER','Browser E2E tenant','active')
               ON CONFLICT(tenant_code) DO UPDATE SET status='active'
               RETURNING id""",
        )
        user_id = await connection.fetchval(
            """INSERT INTO furniscope.users
               (tenant_id,email,password_hash,name,role_code,status)
               VALUES($1,$2,$3,'Browser E2E user','user','active')
               ON CONFLICT(email) DO UPDATE SET tenant_id=excluded.tenant_id,
                 password_hash=excluded.password_hash,status='active'
               RETURNING id""",
            tenant_id, email, PasswordService().hash(password),
        )
        product_id = await connection.fetchval(
            """INSERT INTO furniscope.products
               (tenant_id,sku,name,category_code,analysis_status,created_by)
               VALUES($1,'E2E-SOFA','Browser E2E sofa','sofa','ready',$2)
               ON CONFLICT(tenant_id,sku) DO UPDATE SET analysis_status='ready'
               RETURNING id""",
            tenant_id, user_id,
        )
        profile_id = await connection.fetchval(
            """SELECT id FROM furniscope.product_profile_versions
               WHERE tenant_id=$1 AND product_id=$2 AND version_no=1""",
            tenant_id, product_id,
        )
        if profile_id is None:
            profile_id = await connection.fetchval(
                """INSERT INTO furniscope.product_profile_versions
                   (tenant_id,product_id,version_no,schema_version,status,completeness_score,
                    source_summary,confirmed_by,confirmed_at)
                   VALUES($1,$2,1,'browser-e2e-v1','confirmed',1,$3::jsonb,$4,now())
                   RETURNING id""",
                tenant_id, product_id,
                json.dumps({"source": "browser_e2e_seed", "data_class": "test_fixture"}),
                user_id,
            )
        for code, value in (
            ("material", "oak wood"), ("style", "modern"),
            ("function", "three-seat sofa"),
        ):
            await connection.execute(
                """INSERT INTO furniscope.product_attributes
                   (tenant_id,profile_version_id,attribute_code,value,source_type,confidence,
                    confirmation_status)
                   VALUES($1,$2,$3,to_jsonb($4::text),'confirmed_structured',.98,'confirmed')
                   ON CONFLICT(profile_version_id,attribute_code) DO UPDATE SET value=excluded.value""",
                tenant_id, profile_id, code, value,
            )
        await connection.execute(
            """UPDATE furniscope.products SET current_profile_version_id=$1,analysis_status='ready'
               WHERE id=$2 AND tenant_id=$3""",
            profile_id, product_id, tenant_id,
        )
        print(f"seeded tenant={tenant_id} user={user_id} product={product_id}")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
