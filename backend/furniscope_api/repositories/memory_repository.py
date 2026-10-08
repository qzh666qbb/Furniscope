"""Versioned, tenant-scoped customer memory persistence."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


DEFAULT_MEMORY_POLICY = {
    "auto_extract": False,
    "confirmation_required": True,
    "default_scope": "workspace",
    "retention_days": 365,
    "allowed_types": [
        "target_market",
        "budget",
        "unit_cost_limit",
        "preferred_channel",
        "customer_preference",
    ],
}


def _memory_row(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["value"] = item.pop("memory_value", {}) or {}
    item["confidence"] = float(item.get("confidence") or 0)
    dependencies = item.get("profile_dependencies")
    if not isinstance(dependencies, dict):
        item["profile_dependencies"] = {}
    return item


class MemoryRepository:
    async def list(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        offset: int,
        limit: int,
        status: str | None = None,
        scope: str | None = None,
        workspace_uuid: str | None = None,
        memory_type: str | None = None,
        active_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        params = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "offset": offset,
            "limit": limit,
            "status": status,
            "scope": scope,
            "workspace_uuid": workspace_uuid,
            "memory_type": memory_type,
            "active_only": active_only,
        }
        where = """
          m.tenant_id=:tenant_id
          AND (m.scope='tenant' OR m.user_id=:user_id)
          AND (CAST(:status AS text) IS NULL OR m.status=:status)
          AND (CAST(:scope AS text) IS NULL OR m.scope=:scope)
          AND (CAST(:memory_type AS text) IS NULL OR m.memory_key=:memory_type)
          AND (
            NOT :active_only OR (
              m.status='confirmed' AND m.invalidated_at IS NULL
              AND m.effective_at<=clock_timestamp()
              AND (m.expires_at IS NULL OR m.expires_at>clock_timestamp())
            )
          )
          AND (
            CAST(:workspace_uuid AS text) IS NULL OR m.scope<>'workspace'
            OR w.workspace_uuid=CAST(:workspace_uuid AS uuid)
          )
        """
        total = int(await session.scalar(text(f"""
            SELECT count(*) FROM customer_memories m
              LEFT JOIN analysis_workspaces w
                ON w.id=m.workspace_id AND w.tenant_id=m.tenant_id
             WHERE {where}
        """), params) or 0)
        rows = await session.execute(text(f"""
            SELECT m.memory_uuid::text,m.memory_type,m.memory_value,m.scope,m.status,
                   m.confidence::float8,w.workspace_uuid::text,m.source_message_uuid::text,
                   m.supersedes_memory_uuid::text,m.created_at,m.updated_at,
                   m.effective_at,m.expires_at,m.confirmed_by,m.confirmed_at,m.archived_at,
                   m.invalidated_at,m.invalidation_reason,m.sensitivity,m.profile_dependencies
              FROM customer_memories m
              LEFT JOIN analysis_workspaces w
                ON w.id=m.workspace_id AND w.tenant_id=m.tenant_id
             WHERE {where}
             ORDER BY m.updated_at DESC,m.id DESC
             OFFSET :offset LIMIT :limit
        """), params)
        return [_memory_row(row) for row in rows.mappings().all()], total

    async def get(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        memory_uuid: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        lock = " FOR UPDATE OF m" if for_update else ""
        row = await session.execute(text(f"""
            SELECT m.id,m.memory_uuid::text,m.memory_type,m.memory_value,m.scope,m.status,
                   m.confidence::float8,m.user_id,m.workspace_id,w.workspace_uuid::text,
                   m.source_message_uuid::text,m.supersedes_memory_uuid::text,
                   m.effective_at,m.expires_at,m.confirmed_by,m.created_at,m.updated_at,
                   m.confirmed_at,m.archived_at,m.invalidated_at,m.invalidation_reason,
                   m.sensitivity,m.profile_dependencies
              FROM customer_memories m
              LEFT JOIN analysis_workspaces w
                ON w.id=m.workspace_id AND w.tenant_id=m.tenant_id
             WHERE m.tenant_id=:tenant_id AND m.memory_uuid=CAST(:memory_uuid AS uuid)
               AND (m.scope='tenant' OR m.user_id=:user_id)
             {lock}
        """), {"tenant_id": tenant_id, "user_id": user_id, "memory_uuid": memory_uuid})
        record = row.mappings().one_or_none()
        return _memory_row(record) if record else None

    async def create_candidate(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_id: int | None,
        memory_type: str,
        value: dict[str, Any],
        confidence: float,
        source_message_uuid: str | None,
        scope: str,
        retention_days: int,
        sensitivity: str = "confidential",
        profile_dependencies: dict[str, Any] | None = None,
        expires_at: Any | None = None,
        expires_at_set: bool = False,
    ) -> dict[str, Any]:
        owner_user_id = None if scope == "tenant" else user_id
        scoped_workspace_id = workspace_id if scope == "workspace" else None
        if scope == "workspace" and scoped_workspace_id is None:
            raise ValueError("workspace scope requires workspace_id")
        existing = await session.execute(text("""
            SELECT m.id,m.memory_uuid::text,m.memory_type,m.memory_value,m.scope,m.status,
                   m.confidence::float8,w.workspace_uuid::text,m.source_message_uuid::text,
                   m.supersedes_memory_uuid::text,m.created_at,m.updated_at,
                   m.effective_at,m.expires_at,m.confirmed_by,m.confirmed_at,m.archived_at,
                   m.invalidated_at,m.invalidation_reason,m.sensitivity,m.profile_dependencies
              FROM customer_memories m
              LEFT JOIN analysis_workspaces w
                ON w.id=m.workspace_id AND w.tenant_id=m.tenant_id
             WHERE m.tenant_id=:tenant_id
               AND m.user_id IS NOT DISTINCT FROM :user_id
               AND m.workspace_id IS NOT DISTINCT FROM :workspace_id
               AND m.memory_key=:memory_type AND m.status='candidate'
               AND m.memory_value=CAST(:memory_value AS jsonb)
               AND m.source_message_uuid IS NOT DISTINCT FROM CAST(:source_message_uuid AS uuid)
             ORDER BY m.id DESC LIMIT 1
        """), {
            "tenant_id": tenant_id,
            "user_id": owner_user_id,
            "workspace_id": scoped_workspace_id,
            "memory_type": memory_type,
            "memory_value": json.dumps(value, ensure_ascii=False),
            "source_message_uuid": source_message_uuid,
        })
        record = existing.mappings().one_or_none()
        if record:
            return _memory_row(record)
        supersedes = await session.scalar(text("""
            SELECT memory_uuid::text FROM customer_memories
             WHERE tenant_id=:tenant_id
               AND user_id IS NOT DISTINCT FROM :user_id
               AND workspace_id IS NOT DISTINCT FROM :workspace_id
               AND scope=:scope AND memory_key=:memory_type AND status='confirmed'
             ORDER BY updated_at DESC,id DESC LIMIT 1
        """), {
            "tenant_id": tenant_id,
            "user_id": owner_user_id,
            "workspace_id": scoped_workspace_id,
            "scope": scope,
            "memory_type": memory_type,
        })
        result = await session.execute(text("""
            INSERT INTO customer_memories(
              tenant_id,user_id,workspace_id,memory_key,memory_value,scope,status,
              confidence,source_message_uuid,supersedes_memory_uuid,effective_at,expires_at,
              sensitivity,profile_dependencies
            )
            VALUES(
              :tenant_id,:user_id,:workspace_id,:memory_type,CAST(:memory_value AS jsonb),
              :scope,'candidate',:confidence,CAST(:source_message_uuid AS uuid),
              CAST(:supersedes_memory_uuid AS uuid),
              CURRENT_TIMESTAMP,CASE WHEN :expires_at_set THEN :expires_at
                ELSE CURRENT_TIMESTAMP+make_interval(days=>:retention_days) END,
              :sensitivity,CAST(:profile_dependencies AS jsonb)
            )
            RETURNING memory_uuid::text,memory_type,memory_value,scope,status,
                      confidence::float8,NULL::text AS workspace_uuid,
                      source_message_uuid::text,supersedes_memory_uuid::text,
                      effective_at,expires_at,confirmed_by,created_at,updated_at,
                      confirmed_at,archived_at,invalidated_at,invalidation_reason,
                      sensitivity,profile_dependencies
        """), {
            "tenant_id": tenant_id,
            "user_id": owner_user_id,
            "workspace_id": scoped_workspace_id,
            "memory_type": memory_type,
            "memory_value": json.dumps(value, ensure_ascii=False),
            "scope": scope,
            "confidence": confidence,
            "source_message_uuid": source_message_uuid,
            "supersedes_memory_uuid": supersedes,
            "retention_days": retention_days,
            "sensitivity": sensitivity,
            "profile_dependencies": json.dumps(profile_dependencies or {}, ensure_ascii=False),
            "expires_at": expires_at,
            "expires_at_set": expires_at_set,
        })
        item = _memory_row(result.mappings().one())
        if scope == "workspace":
            item["workspace_uuid"] = await session.scalar(text("""
                SELECT workspace_uuid::text FROM analysis_workspaces
                 WHERE tenant_id=:tenant_id AND id=:workspace_id
            """), {"tenant_id": tenant_id, "workspace_id": scoped_workspace_id})
        return item

    async def patch(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        memory_uuid: str,
        value: dict[str, Any] | None,
        scope: str | None,
        workspace_id: int | None,
        retention_days: int,
        expires_at: Any | None = None,
        expires_at_set: bool = False,
        sensitivity: str | None = None,
    ) -> dict[str, Any] | None:
        current = await self.get(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            memory_uuid=memory_uuid,
            for_update=True,
        )
        if current is None or current["status"] in {"archived", "superseded", "invalidated"}:
            return None
        next_scope = scope or current["scope"]
        next_value = value if value is not None else current["value"]
        next_workspace_id = (
            workspace_id if next_scope == "workspace" else None
        )
        if current["status"] == "confirmed":
            return await self.create_candidate(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=next_workspace_id,
                memory_type=current["memory_type"],
                value=next_value,
                confidence=current["confidence"],
                source_message_uuid=current.get("source_message_uuid"),
                scope=next_scope,
                retention_days=retention_days,
                sensitivity=sensitivity or current.get("sensitivity") or "confidential",
                profile_dependencies=current.get("profile_dependencies") or {},
                expires_at=expires_at,
                expires_at_set=expires_at_set,
            )
        owner_user_id = None if next_scope == "tenant" else user_id
        result = await session.execute(text("""
            UPDATE customer_memories
               SET memory_value=CAST(:memory_value AS jsonb),scope=:scope,
                   user_id=:owner_user_id,workspace_id=:workspace_id,
                   expires_at=CASE WHEN :expires_at_set THEN :expires_at
                     ELSE CURRENT_TIMESTAMP+make_interval(days=>:retention_days) END,
                   sensitivity=COALESCE(:sensitivity,sensitivity),
                   updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND memory_uuid=CAST(:memory_uuid AS uuid)
               AND status='candidate'
            RETURNING memory_uuid::text,memory_type,memory_value,scope,status,
                      confidence::float8,NULL::text AS workspace_uuid,
                      source_message_uuid::text,supersedes_memory_uuid::text,
                      effective_at,expires_at,confirmed_by,created_at,updated_at,
                      confirmed_at,archived_at,invalidated_at,invalidation_reason,
                      sensitivity,profile_dependencies
        """), {
            "tenant_id": tenant_id,
            "memory_uuid": memory_uuid,
            "memory_value": json.dumps(next_value, ensure_ascii=False),
            "scope": next_scope,
            "owner_user_id": owner_user_id,
            "workspace_id": next_workspace_id,
            "retention_days": retention_days,
            "expires_at": expires_at,
            "expires_at_set": expires_at_set,
            "sensitivity": sensitivity,
        })
        record = result.mappings().one_or_none()
        return _memory_row(record) if record else None

    async def confirm(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        memory_uuid: str,
    ) -> dict[str, Any] | None:
        current = await self.get(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            memory_uuid=memory_uuid,
            for_update=True,
        )
        if current is None:
            return None
        if current["status"] == "confirmed":
            return current
        if current["status"] != "candidate":
            return None
        lock_key = "|".join([
            str(tenant_id),
            current["scope"],
            str(current.get("user_id") or 0),
            str(current.get("workspace_id") or 0),
            current["memory_type"],
        ])
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": lock_key})
        previous = await session.scalar(text("""
            SELECT memory_uuid::text FROM customer_memories
             WHERE tenant_id=:tenant_id
               AND user_id IS NOT DISTINCT FROM :owner_user_id
               AND workspace_id IS NOT DISTINCT FROM :workspace_id
               AND scope=:scope AND memory_key=:memory_type AND status='confirmed'
               AND memory_uuid<>CAST(:memory_uuid AS uuid)
             ORDER BY updated_at DESC,id DESC LIMIT 1
             FOR UPDATE
        """), {
            "tenant_id": tenant_id,
            "owner_user_id": current.get("user_id"),
            "workspace_id": current.get("workspace_id"),
            "scope": current["scope"],
            "memory_type": current["memory_type"],
            "memory_uuid": memory_uuid,
        })
        if previous:
            await session.execute(text("""
                UPDATE customer_memories
                   SET status='superseded',archived_at=CURRENT_TIMESTAMP,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE tenant_id=:tenant_id AND memory_uuid=CAST(:previous AS uuid)
            """), {"tenant_id": tenant_id, "previous": previous})
        result = await session.execute(text("""
            UPDATE customer_memories
               SET status='confirmed',confirmed_at=CURRENT_TIMESTAMP,confirmed_by=:user_id,
                   effective_at=CURRENT_TIMESTAMP,archived_at=NULL,
                   invalidated_at=NULL,invalidation_reason=NULL,
                   supersedes_memory_uuid=COALESCE(
                     supersedes_memory_uuid,CAST(:previous AS uuid)
                   ),updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND memory_uuid=CAST(:memory_uuid AS uuid)
               AND status='candidate'
            RETURNING memory_uuid::text,memory_type,memory_value,scope,status,
                      confidence::float8,NULL::text AS workspace_uuid,
                      source_message_uuid::text,supersedes_memory_uuid::text,
                      effective_at,expires_at,confirmed_by,created_at,updated_at,
                      confirmed_at,archived_at,invalidated_at,invalidation_reason,
                      sensitivity,profile_dependencies
        """), {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "memory_uuid": memory_uuid,
            "previous": previous,
        })
        record = result.mappings().one_or_none()
        if record is None:
            return None
        item = _memory_row(record)
        item["workspace_uuid"] = current.get("workspace_uuid")
        return item

    async def archive(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        memory_uuid: str,
    ) -> dict[str, Any] | None:
        current = await self.get(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            memory_uuid=memory_uuid,
            for_update=True,
        )
        if current is None:
            return None
        if current["status"] == "archived":
            return current
        result = await session.execute(text("""
            UPDATE customer_memories
               SET status='archived',archived_at=CURRENT_TIMESTAMP,
                   invalidated_at=NULL,invalidation_reason=NULL,updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND memory_uuid=CAST(:memory_uuid AS uuid)
            RETURNING memory_uuid::text,memory_type,memory_value,scope,status,
                      confidence::float8,NULL::text AS workspace_uuid,
                      source_message_uuid::text,supersedes_memory_uuid::text,
                      effective_at,expires_at,confirmed_by,created_at,updated_at,
                      confirmed_at,archived_at,invalidated_at,invalidation_reason,
                      sensitivity,profile_dependencies
        """), {"tenant_id": tenant_id, "memory_uuid": memory_uuid})
        item = _memory_row(result.mappings().one())
        item["workspace_uuid"] = current.get("workspace_uuid")
        return item

    async def reject(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        memory_uuid: str,
    ) -> dict[str, Any] | None:
        current = await self.get(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            memory_uuid=memory_uuid,
            for_update=True,
        )
        if current is None or current["status"] != "candidate":
            return None
        return await self.archive(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            memory_uuid=memory_uuid,
        )

    async def history(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        memory_uuid: str,
    ) -> list[dict[str, Any]]:
        current = await self.get(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            memory_uuid=memory_uuid,
        )
        if current is None:
            return []
        rows = await session.execute(text("""
            SELECT m.memory_uuid::text,m.memory_type,m.memory_value,m.scope,m.status,
                   m.confidence::float8,w.workspace_uuid::text,m.source_message_uuid::text,
                   m.supersedes_memory_uuid::text,m.created_at,m.updated_at,
                   m.effective_at,m.expires_at,m.confirmed_by,m.confirmed_at,m.archived_at,
                   m.invalidated_at,m.invalidation_reason,m.sensitivity,m.profile_dependencies
              FROM customer_memories m
              LEFT JOIN analysis_workspaces w
                ON w.id=m.workspace_id AND w.tenant_id=m.tenant_id
             WHERE m.tenant_id=:tenant_id
               AND m.user_id IS NOT DISTINCT FROM :owner_user_id
               AND m.workspace_id IS NOT DISTINCT FROM :workspace_id
               AND m.scope=:scope AND m.memory_key=:memory_type
             ORDER BY m.created_at DESC,m.id DESC
        """), {
            "tenant_id": tenant_id,
            "owner_user_id": current.get("user_id"),
            "workspace_id": current.get("workspace_id"),
            "scope": current["scope"],
            "memory_type": current["memory_type"],
        })
        return [_memory_row(row) for row in rows.mappings().all()]

    async def find_confirmed_targets(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_id: int | None,
        memory_types: list[str],
    ) -> list[dict[str, Any]]:
        rows = await session.execute(text("""
            SELECT m.memory_uuid::text,m.memory_type,m.memory_value,m.scope,m.status,
                   m.confidence::float8,w.workspace_uuid::text,m.source_message_uuid::text,
                   m.supersedes_memory_uuid::text,m.created_at,m.updated_at,
                   m.effective_at,m.expires_at,m.confirmed_by,m.confirmed_at,m.archived_at,
                   m.invalidated_at,m.invalidation_reason,m.sensitivity,m.profile_dependencies
              FROM customer_memories m
              LEFT JOIN analysis_workspaces w
                ON w.id=m.workspace_id AND w.tenant_id=m.tenant_id
             WHERE m.tenant_id=:tenant_id AND m.status='confirmed'
               AND m.invalidated_at IS NULL AND m.effective_at<=clock_timestamp()
               AND (m.expires_at IS NULL OR m.expires_at>clock_timestamp())
               AND m.memory_key=ANY(:memory_types)
               AND (
                 m.scope='tenant' OR
                 (m.scope='user' AND m.user_id=:user_id) OR
                 (m.scope='workspace' AND m.user_id=:user_id AND m.workspace_id=:workspace_id)
               )
             ORDER BY CASE m.scope WHEN 'workspace' THEN 1 WHEN 'user' THEN 2 ELSE 3 END,m.updated_at DESC
        """), {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "workspace_id": workspace_id,
            "memory_types": memory_types,
        })
        return [_memory_row(row) for row in rows.mappings().all()]

    async def get_policy(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        row = await session.execute(text("""
            SELECT auto_extract,confirmation_required,default_scope,retention_days,allowed_types
              FROM customer_memory_policies
             WHERE tenant_id=:tenant_id AND user_id=:user_id
        """), {"tenant_id": tenant_id, "user_id": user_id})
        record = row.mappings().one_or_none()
        return dict(record) if record else dict(DEFAULT_MEMORY_POLICY)

    async def put_policy(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        result = await session.execute(text("""
            INSERT INTO customer_memory_policies(
              tenant_id,user_id,auto_extract,confirmation_required,default_scope,
              retention_days,allowed_types
            )
            VALUES(
              :tenant_id,:user_id,:auto_extract,:confirmation_required,:default_scope,
              :retention_days,CAST(:allowed_types AS jsonb)
            )
            ON CONFLICT(tenant_id,user_id) DO UPDATE SET
              auto_extract=EXCLUDED.auto_extract,
              confirmation_required=EXCLUDED.confirmation_required,
              default_scope=EXCLUDED.default_scope,
              retention_days=EXCLUDED.retention_days,
              allowed_types=EXCLUDED.allowed_types,
              updated_at=CURRENT_TIMESTAMP
            RETURNING auto_extract,confirmation_required,default_scope,retention_days,allowed_types
        """), {
            "tenant_id": tenant_id,
            "user_id": user_id,
            **policy,
            "allowed_types": json.dumps(policy["allowed_types"], ensure_ascii=False),
        })
        return dict(result.mappings().one())
