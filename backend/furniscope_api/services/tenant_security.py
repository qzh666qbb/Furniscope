"""Fail-closed verification of PostgreSQL tenant-isolation invariants."""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


GOVERNANCE_ROLES = {
    "furniscope_tenant",
    "furniscope_schema_owner",
    "furniscope_migrator",
    "furniscope_authenticator",
    "furniscope_runtime",
    "furniscope_platform_admin",
    "furniscope_scheduler",
    "furniscope_audit_writer",
    "furniscope_backup",
    "furniscope_monitor",
}
SPECIAL_TABLES = {"tenants", "cluster_members", "forecast_models"}


def _canonical_expression(value: str | None) -> str:
    return re.sub(r'[\s()"]+', "", value or "").lower().replace("furniscope.", "")


def _tenant_expression(value: str | None, *, key: str = "tenant_id") -> bool:
    expression = _canonical_expression(value)
    key_pattern = re.escape(key.lower())
    boundary = r"(^|[^a-z0-9_]){}($|[^a-z0-9_])"
    return bool(
        re.search(boundary.format(f"{key_pattern}=current_tenant_id"), expression)
        or re.search(boundary.format(f"current_tenant_id={key_pattern}"), expression)
    )


async def verify_tenant_security(
    session: AsyncSession,
    *,
    require_runtime_separation: bool,
) -> None:
    issues: list[str] = []
    roles = (
        await session.execute(
            text(
                """
                SELECT rolname,rolsuper,rolbypassrls,rolcanlogin
                  FROM pg_roles
                 WHERE rolname=ANY(CAST(:roles AS text[]))
                """
            ),
            {"roles": sorted(GOVERNANCE_ROLES)},
        )
    ).mappings().all()
    role_map = {row["rolname"]: row for row in roles}
    missing_roles = sorted(GOVERNANCE_ROLES - set(role_map))
    if missing_roles:
        issues.append("missing roles: " + ",".join(missing_roles))
    for role_name, row in role_map.items():
        if row["rolsuper"] or row["rolbypassrls"] or row["rolcanlogin"]:
            issues.append(f"unsafe governance role: {role_name}")

    tables = (
        await session.execute(
            text(
                """
                SELECT c.oid,c.relname,c.relrowsecurity,c.relforcerowsecurity,
                       owner.rolname AS owner_name,
                       EXISTS(
                         SELECT FROM pg_attribute a
                          WHERE a.attrelid=c.oid AND a.attname='tenant_id'
                            AND NOT a.attisdropped
                       ) AS has_tenant_id
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                  JOIN pg_roles owner ON owner.oid=c.relowner
                 WHERE n.nspname='furniscope' AND c.relkind IN ('r','p')
                   AND NOT c.relispartition
                   AND (
                     EXISTS(
                       SELECT FROM pg_attribute a
                        WHERE a.attrelid=c.oid AND a.attname='tenant_id'
                          AND NOT a.attisdropped
                     )
                     OR c.relname=ANY(CAST(:special AS text[]))
                   )
                 ORDER BY c.relname
                """
            ),
            {"special": sorted(SPECIAL_TABLES)},
        )
    ).mappings().all()
    table_map = {row["relname"]: row for row in tables}
    missing_special = sorted(SPECIAL_TABLES - set(table_map))
    if missing_special:
        issues.append("missing special tables: " + ",".join(missing_special))
    for table_name, row in table_map.items():
        if not row["relrowsecurity"]:
            issues.append(f"RLS disabled: {table_name}")
        if not row["relforcerowsecurity"]:
            issues.append(f"FORCE RLS disabled: {table_name}")

    policies = (
        await session.execute(
            text(
                """
                SELECT c.relname,p.polname,
                       ARRAY(
                         SELECT r.rolname
                           FROM pg_roles r
                          WHERE r.oid=ANY(p.polroles)
                          ORDER BY r.rolname
                       ) AS role_names,
                       pg_get_expr(p.polqual,p.polrelid) AS using_expression,
                       pg_get_expr(p.polwithcheck,p.polrelid) AS check_expression
                  FROM pg_policy p
                  JOIN pg_class c ON c.oid=p.polrelid
                  JOIN pg_namespace n ON n.oid=c.relnamespace
                 WHERE n.nspname='furniscope'
                   AND c.relname=ANY(CAST(:tables AS text[]))
                """
            ),
            {"tables": sorted(table_map)},
        )
    ).mappings().all()
    policy_map: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in policies:
        policy_map[row["relname"]][row["polname"]] = dict(row)

    for table_name, row in table_map.items():
        table_policies = policy_map.get(table_name, {})
        admin_policy = table_policies.get("platform_admin_scope")
        if admin_policy is None or "furniscope_platform_admin" not in admin_policy["role_names"]:
            issues.append(f"invalid platform admin policy: {table_name}")

        if table_name == "forecast_models":
            read_policy = table_policies.get("tenant_model_read")
            write_policy = table_policies.get("tenant_model_write")
            if read_policy is None or write_policy is None:
                issues.append("invalid tenant model policies: forecast_models")
            continue

        tenant_policy = table_policies.get("tenant_scope")
        if tenant_policy is None or "furniscope_tenant" not in tenant_policy["role_names"]:
            issues.append(f"invalid tenant policy role: {table_name}")
            continue
        if table_name == "cluster_members":
            using = _canonical_expression(tenant_policy["using_expression"])
            check = _canonical_expression(tenant_policy["check_expression"])
            if "current_tenant_id" not in using or "current_tenant_id" not in check:
                issues.append("invalid tenant policy expression: cluster_members")
            continue
        key = "id" if table_name == "tenants" else "tenant_id"
        if not _tenant_expression(tenant_policy["using_expression"], key=key):
            issues.append(f"invalid tenant USING expression: {table_name}")
        if table_name != "tenants" and not _tenant_expression(
            tenant_policy["check_expression"], key=key
        ):
            issues.append(f"invalid tenant WITH CHECK expression: {table_name}")

    if require_runtime_separation:
        runtime = (
            await session.execute(
                text(
                    """
                    SELECT current_user AS role_name,r.rolsuper,r.rolbypassrls,
                           EXISTS(
                             SELECT FROM pg_class c
                             JOIN pg_namespace n ON n.oid=c.relnamespace
                              WHERE n.nspname='furniscope' AND c.relowner=r.oid
                           ) AS owns_schema_objects
                      FROM pg_roles r
                     WHERE r.rolname=current_user
                    """
                )
            )
        ).mappings().one()
        if runtime["rolsuper"] or runtime["rolbypassrls"] or runtime["owns_schema_objects"]:
            issues.append(f"unsafe runtime login role: {runtime['role_name']}")

    if issues:
        raise RuntimeError("Tenant security verification failed: " + "; ".join(sorted(set(issues))))
