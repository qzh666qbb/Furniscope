"""Create an isolated enterprise tenant and login account."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def provision_enterprise_account(
    session: AsyncSession,
    *,
    tenant_code: str,
    enterprise_name: str,
    email: str,
    contact_name: str,
    password_hash: str,
    entitlements: list[str],
) -> dict[str, Any]:
    tenant = (await session.execute(text("""INSERT INTO tenants
        (tenant_code,name,status,entitlements,data_class)
        VALUES(:code,:name,'active',CAST(:entitlements AS jsonb),'business')
        RETURNING id,tenant_code,name,status,entitlements,created_at,updated_at"""), {
        "code": tenant_code, "name": enterprise_name,
        "entitlements": json.dumps(entitlements),
    })).mappings().one()
    user = (await session.execute(text("""INSERT INTO users
        (tenant_id,email,password_hash,name,role_code,status)
        VALUES(:tenant,:email,:password,:name,'user','active')
        RETURNING id,name,email,status,created_at"""), {
        "tenant": tenant["id"], "email": email,
        "password": password_hash, "name": contact_name,
    })).mappings().one()
    if "sales_forecast" in entitlements:
        base_model = (await session.execute(text("""SELECT id FROM forecast_models
            WHERE model_scope='shared_base' AND owner_tenant_id IS NULL AND status='active'
            ORDER BY created_at DESC,id DESC LIMIT 1"""))).scalar_one_or_none()
        if base_model is not None:
            await session.execute(text("""INSERT INTO forecast_model_deployments
                (tenant_id,model_id,scenario_code,status,route_policy,deployed_by)
                VALUES(:tenant,:model,'sales_forecast','active','{"source":"enterprise_onboarding"}'::jsonb,NULL)"""),
                {"tenant": tenant["id"], "model": base_model})
    return {
        "tenant_id": tenant["id"],
        "tenant_code": tenant["tenant_code"],
        "enterprise_name": tenant["name"],
        "tenant_status": tenant["status"],
        "entitlements": tenant["entitlements"],
        "user_id": user["id"],
        "contact_name": user["name"],
        "email": user["email"],
        "user_status": user["status"],
        "created_at": tenant["created_at"],
        "updated_at": tenant["updated_at"],
    }
