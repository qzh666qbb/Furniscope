"""Tenant enterprise profile and manufacturing capability persistence."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class EnterpriseRepository:
    """Reads and writes the tenant-scoped enterprise capability profile.

    The agent's enterprise-fit scoring reads ``manufacturing_capabilities``
    directly, so rows written here are picked up by every new analysis task.
    """

    async def get_profile(self, session: AsyncSession, *, tenant_id: int) -> dict[str, Any] | None:
        row = await session.execute(text("""
            SELECT business_model,primary_categories,export_markets,sales_channels,profile_version,
                   annual_capacity_note,constraints,profile_completeness::float8,
                   confirmed_by,confirmed_at,updated_at
              FROM enterprise_profiles WHERE tenant_id=:tenant_id
        """), {"tenant_id": tenant_id})
        record = row.mappings().one_or_none()
        return dict(record) if record is not None else None

    async def list_capabilities(self, session: AsyncSession, *, tenant_id: int) -> list[dict[str, Any]]:
        rows = await session.execute(text("""
            SELECT capability_type,capability_code,capability_name,availability,
                   min_value::float8,max_value::float8,unit,notes,source_type,
                   confidence::float8,updated_at
              FROM manufacturing_capabilities
             WHERE tenant_id=:tenant_id
             ORDER BY capability_type,capability_code
        """), {"tenant_id": tenant_id})
        return [dict(row) for row in rows.mappings().all()]

    async def save_profile(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                           profile: dict[str, Any], capabilities: list[dict[str, Any]]) -> None:
        """Upsert the profile and reconcile capability rows in one transaction."""
        completeness = self._completeness(profile, capabilities)
        await session.execute(text("""
            INSERT INTO enterprise_profiles
                   (tenant_id,business_model,primary_categories,export_markets,sales_channels,
                    annual_capacity_note,constraints,profile_completeness,confirmed_by,confirmed_at)
            VALUES (:tenant_id,CAST(:business_model AS jsonb),CAST(:primary_categories AS jsonb),
                    CAST(:export_markets AS jsonb),CAST(:sales_channels AS jsonb),
                    :annual_capacity_note,CAST(:constraints AS jsonb),:profile_completeness,
                    :confirmed_by,CURRENT_TIMESTAMP)
            ON CONFLICT (tenant_id) DO UPDATE SET
                    business_model=EXCLUDED.business_model,
                    primary_categories=EXCLUDED.primary_categories,
                    export_markets=EXCLUDED.export_markets,
                    sales_channels=EXCLUDED.sales_channels,
                    annual_capacity_note=EXCLUDED.annual_capacity_note,
                    constraints=EXCLUDED.constraints,
                    profile_completeness=EXCLUDED.profile_completeness,
                    profile_version=enterprise_profiles.profile_version+1,
                    confirmed_by=EXCLUDED.confirmed_by,
                    confirmed_at=EXCLUDED.confirmed_at,
                    updated_at=CURRENT_TIMESTAMP
        """), {
            "tenant_id": tenant_id,
            "business_model": json.dumps(profile["business_model"], ensure_ascii=False),
            "primary_categories": json.dumps(profile["primary_categories"], ensure_ascii=False),
            "export_markets": json.dumps(profile["export_markets"], ensure_ascii=False),
            "sales_channels": json.dumps(profile["sales_channels"], ensure_ascii=False),
            "annual_capacity_note": profile.get("annual_capacity_note") or None,
            "constraints": json.dumps(profile["constraints"], ensure_ascii=False),
            "profile_completeness": completeness,
            "confirmed_by": user_id,
        })

        keep_keys = [f"{item['capability_type']}/{item['capability_code']}" for item in capabilities]
        await session.execute(text("""
            DELETE FROM manufacturing_capabilities
             WHERE tenant_id=:tenant_id
               AND capability_type || '/' || capability_code <> ALL(:keep_keys)
        """), {"tenant_id": tenant_id, "keep_keys": keep_keys or [""]})

        for item in capabilities:
            await session.execute(text("""
                INSERT INTO manufacturing_capabilities
                       (tenant_id,capability_type,capability_code,capability_name,availability,
                        min_value,max_value,unit,source_type,confidence,notes,created_by)
                VALUES (:tenant_id,:capability_type,:capability_code,:capability_name,:availability,
                        :min_value,:max_value,:unit,'confirmed_user',1,:notes,:created_by)
                ON CONFLICT (tenant_id,capability_type,capability_code) DO UPDATE SET
                        capability_name=EXCLUDED.capability_name,
                        availability=EXCLUDED.availability,
                        min_value=EXCLUDED.min_value,
                        max_value=EXCLUDED.max_value,
                        unit=EXCLUDED.unit,
                        notes=EXCLUDED.notes,
                        source_type='confirmed_user',
                        confidence=1,
                        updated_at=CURRENT_TIMESTAMP
            """), {
                "tenant_id": tenant_id,
                "capability_type": item["capability_type"],
                "capability_code": item["capability_code"],
                "capability_name": item["capability_name"],
                "availability": item["availability"],
                "min_value": item.get("min_value"),
                "max_value": item.get("max_value"),
                "unit": item.get("unit"),
                "notes": item.get("notes"),
                "created_by": user_id,
            })

    @staticmethod
    def _completeness(profile: dict[str, Any], capabilities: list[dict[str, Any]]) -> float:
        """Rule-based completeness across the eight profile dimensions (0-1)."""
        available = {item["capability_type"] for item in capabilities if item["availability"] == "yes"}
        has_cost_range = any(
            c.get("constraint_type") == "unit_cost" for c in profile["constraints"]
        )
        checks = [
            bool(profile["business_model"]),
            bool(profile["primary_categories"]),
            bool(profile["export_markets"]),
            bool((profile.get("annual_capacity_note") or "").strip()),
            has_cost_range,
            "category" in available,
            bool(available & {"material", "process"}),
            bool(available & {"certification", "customization", "delivery"}),
        ]
        return round(sum(checks) / len(checks), 4)
