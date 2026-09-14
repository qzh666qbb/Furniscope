"""Persistence for AI workbench containers and chat turns."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _jsonish(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


class WorkspaceRepository:
    @staticmethod
    def _message_row(row: dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        item["evidence_refs"] = _jsonish(item.get("evidence_refs"), [])
        item["metadata"] = _jsonish(item.get("metadata"), {})
        return item

    async def ensure_schema(self, session: AsyncSession) -> None:
        await session.execute(text("""
            DO $$
            BEGIN
              IF to_regclass('analysis_workspace_messages') IS NULL THEN
                RETURN;
              END IF;
              IF EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conrelid='analysis_workspace_messages'::regclass
                   AND conname='chk_workspace_message_kind'
                   AND pg_get_constraintdef(oid) NOT LIKE '%context%'
              ) THEN
                ALTER TABLE analysis_workspace_messages DROP CONSTRAINT chk_workspace_message_kind;
                ALTER TABLE analysis_workspace_messages ADD CONSTRAINT chk_workspace_message_kind CHECK (
                  message_kind IN ('greeting', 'text', 'run_event', 'task_chat', 'error', 'context')
                );
              END IF;
            END $$
        """))

    async def upsert(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     payload: dict[str, Any]) -> dict[str, Any]:
        workspace_uuid = str(payload.get("workspace_uuid") or uuid4())
        result = await session.execute(text("""
            INSERT INTO analysis_workspaces
              (workspace_uuid, tenant_id, name, source, status, product_id, created_by)
            VALUES
              (CAST(:workspace_uuid AS uuid), :tenant_id, :name, :source, 'active', :product_id, :user_id)
            ON CONFLICT (tenant_id, workspace_uuid) DO UPDATE SET
              name=EXCLUDED.name,
              source=EXCLUDED.source,
              product_id=COALESCE(EXCLUDED.product_id, analysis_workspaces.product_id),
              status='active',
              archived_at=NULL,
              updated_at=CURRENT_TIMESTAMP
            RETURNING id, workspace_uuid::text, name, source, status, product_id, created_at, updated_at
        """), {
            "workspace_uuid": workspace_uuid, "tenant_id": tenant_id, "user_id": user_id,
            "name": payload["name"], "source": payload.get("source") or "node_workflow_canvas",
            "product_id": payload.get("product_id"),
        })
        return dict(result.mappings().one())

    async def find_canonical_for_product(self, session: AsyncSession, *, tenant_id: int,
                                         product_id: int, source: str | None = None) -> str | None:
        result = await session.execute(text("""
            SELECT w.workspace_uuid::text
              FROM analysis_workspaces w
             WHERE w.tenant_id=:tenant_id AND w.product_id=:product_id AND w.status='active'
               AND (CAST(:source AS varchar) IS NULL OR w.source=:source)
             ORDER BY (
                 SELECT count(*) FROM analysis_tasks t
                  WHERE t.tenant_id=w.tenant_id AND t.status<>'cancelled'
                    AND t.analysis_config->>'workspace_uuid'=w.workspace_uuid::text
               ) DESC, w.updated_at DESC
             LIMIT 1
        """), {"tenant_id": tenant_id, "product_id": product_id, "source": source})
        row = result.mappings().one_or_none()
        return str(row["workspace_uuid"]) if row else None

    async def list(self, session: AsyncSession, *, tenant_id: int, offset: int, limit: int,
                   keyword: str | None = None, workspace_uuid: str | None = None
                   ) -> tuple[list[dict[str, Any]], int]:
        params = {
            "tenant_id": tenant_id, "offset": offset, "limit": limit, "keyword": keyword,
            "workspace_uuid": workspace_uuid,
        }
        where = (
            "w.tenant_id=:tenant_id AND w.status='active' AND "
            "(w.last_analysis_task_id IS NOT NULL OR EXISTS ("
            "  SELECT 1 FROM analysis_tasks x"
            "   WHERE x.tenant_id=w.tenant_id AND x.status<>'cancelled'"
            "     AND x.analysis_config->>'workspace_uuid'=w.workspace_uuid::text"
            ")) AND "
            "(CAST(:workspace_uuid AS text) IS NULL "
            " OR w.workspace_uuid=CAST(:workspace_uuid AS uuid)) AND "
            "(CAST(:keyword AS text) IS NULL OR w.name ILIKE '%'||CAST(:keyword AS text)||'%' "
            "OR COALESCE(p.sku,'') ILIKE '%'||CAST(:keyword AS text)||'%' "
            "OR COALESCE(p.name,'') ILIKE '%'||CAST(:keyword AS text)||'%')"
        )
        total = int(await session.scalar(text(f"""
            SELECT count(*)
              FROM analysis_workspaces w
              LEFT JOIN products p ON p.id=w.product_id AND p.tenant_id=w.tenant_id
             WHERE {where}
        """), params) or 0)
        rows = await session.execute(text(f"""
            SELECT w.workspace_uuid::text, w.name AS job_name, w.source, w.status AS workspace_status,
                   COALESCE(t.status, 'draft') AS status,
                   COALESCE(counts.analysis_count, 0) AS analysis_count,
                   w.product_id, p.sku AS product_sku, p.name AS product_name,
                   trim(t.target_country) AS target_country, t.target_platform,
                   t.task_uuid::text, report.report_uuid::text,
                   w.created_at, w.updated_at
              FROM analysis_workspaces w
              LEFT JOIN products p ON p.id=w.product_id AND p.tenant_id=w.tenant_id
              LEFT JOIN analysis_tasks t ON t.id=w.last_analysis_task_id AND t.tenant_id=w.tenant_id
              LEFT JOIN LATERAL (
                SELECT count(*)::int AS analysis_count
                  FROM analysis_tasks x
                 WHERE x.tenant_id=w.tenant_id AND x.status<>'cancelled'
                   AND x.analysis_config->>'workspace_uuid'=w.workspace_uuid::text
              ) counts ON true
              LEFT JOIN LATERAL (
                SELECT r.report_uuid
                  FROM analysis_reports r
                 WHERE r.analysis_job_id=t.id AND r.tenant_id=w.tenant_id AND r.status='draft'
                 ORDER BY r.report_version DESC, r.id DESC LIMIT 1
              ) report ON true
             WHERE {where}
             ORDER BY w.updated_at DESC, w.id DESC
             OFFSET :offset LIMIT :limit
        """), params)
        items = []
        for row in rows.mappings().all():
            item = dict(row)
            item["analysis_count"] = int(item.get("analysis_count") or 0)
            items.append(item)
        return items, total

    async def summary(self, session: AsyncSession, *, tenant_id: int,
                      workspace_uuid: str) -> dict[str, Any] | None:
        rows, _ = await self.list(
            session, tenant_id=tenant_id, offset=0, limit=1, keyword=None,
            workspace_uuid=workspace_uuid,
        )
        return rows[0] if rows else None

    async def get(self, session: AsyncSession, *, tenant_id: int,
                  workspace_uuid: str) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT w.id, w.workspace_uuid::text, w.name, w.source, w.status, w.product_id
              FROM analysis_workspaces w
             WHERE w.tenant_id=:tenant_id AND w.workspace_uuid=CAST(:workspace_uuid AS uuid)
               AND w.status='active'
        """), {"tenant_id": tenant_id, "workspace_uuid": workspace_uuid})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def touch_task(self, session: AsyncSession, *, tenant_id: int, workspace_uuid: str,
                         task_id: int, product_id: int | None = None) -> None:
        await session.execute(text("""
            UPDATE analysis_workspaces
               SET last_analysis_task_id=:task_id,
                   product_id=COALESCE(:product_id, product_id),
                   updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND workspace_uuid=CAST(:workspace_uuid AS uuid)
               AND status='active'
        """), {
            "tenant_id": tenant_id, "workspace_uuid": workspace_uuid,
            "task_id": task_id, "product_id": product_id,
        })

    async def list_messages(self, session: AsyncSession, *, tenant_id: int, workspace_id: int,
                            offset: int, limit: int) -> tuple[list[dict[str, Any]], int]:
        params = {"tenant_id": tenant_id, "workspace_id": workspace_id, "offset": offset, "limit": limit}
        total = int(await session.scalar(text("""
            SELECT count(*) FROM analysis_workspace_messages
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
               AND content NOT LIKE '本次分析执行失败%'
        """), params) or 0)
        rows = await session.execute(text("""
            SELECT m.message_uuid::text, m.role, m.message_kind, m.content, m.seq_no,
                   m.client_message_id, t.task_uuid::text AS analysis_task_uuid,
                   m.evidence_refs, m.metadata, m.created_at
              FROM analysis_workspace_messages m
              LEFT JOIN analysis_tasks t ON t.id=m.analysis_task_id AND t.tenant_id=m.tenant_id
             WHERE m.tenant_id=:tenant_id AND m.workspace_id=:workspace_id
               AND m.content NOT LIKE '本次分析执行失败%'
             ORDER BY m.seq_no, m.id
             OFFSET :offset LIMIT :limit
        """), params)
        return [self._message_row(row) for row in rows.mappings().all()], total

    async def append_message(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                             workspace_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        client_id = payload.get("client_message_id")
        if client_id:
            existing = await session.execute(text("""
                SELECT m.message_uuid::text, m.role, m.message_kind, m.content, m.seq_no,
                       m.client_message_id, t.task_uuid::text AS analysis_task_uuid,
                       m.evidence_refs, m.metadata, m.created_at
                  FROM analysis_workspace_messages m
                  LEFT JOIN analysis_tasks t ON t.id=m.analysis_task_id
                 WHERE m.tenant_id=:tenant_id AND m.client_message_id=:client_id
            """), {"tenant_id": tenant_id, "client_id": client_id})
            row = existing.mappings().one_or_none()
            if row:
                return self._message_row(row)
        task_id = None
        if payload.get("analysis_task_uuid"):
            task_id = await session.scalar(text("""
                SELECT id FROM analysis_tasks
                 WHERE tenant_id=:tenant_id AND task_uuid=CAST(:task_uuid AS uuid)
            """), {"tenant_id": tenant_id, "task_uuid": str(payload["analysis_task_uuid"])})
        result = await session.execute(text("""
            INSERT INTO analysis_workspace_messages
              (tenant_id, workspace_id, analysis_task_id, role, message_kind, content, seq_no,
               client_message_id, evidence_refs, metadata, created_by)
            VALUES
              (:tenant_id, :workspace_id, :task_id, :role, :message_kind, :content,
               COALESCE((SELECT max(seq_no) FROM analysis_workspace_messages
                          WHERE workspace_id=:workspace_id), 0) + 1,
               :client_id, CAST(:evidence AS jsonb), CAST(:metadata AS jsonb), :user_id)
            RETURNING message_uuid::text, role, message_kind, content, seq_no, client_message_id,
                      CAST(:task_uuid AS text) AS analysis_task_uuid, evidence_refs, metadata, created_at
        """), {
            "tenant_id": tenant_id, "workspace_id": workspace_id, "task_id": task_id,
            "role": payload["role"], "message_kind": payload.get("message_kind") or "text",
            "content": payload["content"], "client_id": client_id, "user_id": user_id,
            "evidence": json.dumps(payload.get("evidence_refs") or [], ensure_ascii=False),
            "metadata": json.dumps(payload.get("metadata") or {}, ensure_ascii=False),
            "task_uuid": str(payload["analysis_task_uuid"]) if payload.get("analysis_task_uuid") else None,
        })
        await session.execute(text("""
            UPDATE analysis_workspaces SET updated_at=CURRENT_TIMESTAMP WHERE id=:id AND tenant_id=:tenant_id
        """), {"id": workspace_id, "tenant_id": tenant_id})
        return self._message_row(result.mappings().one())

    async def archive(self, session: AsyncSession, *, tenant_id: int,
                      workspace_uuid: str) -> dict[str, Any]:
        workspace = await session.execute(text("""
            UPDATE analysis_workspaces
               SET status='archived', archived_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND workspace_uuid=CAST(:workspace_uuid AS uuid)
               AND status='active'
            RETURNING workspace_uuid::text
        """), {"tenant_id": tenant_id, "workspace_uuid": workspace_uuid})
        row = workspace.mappings().one_or_none()
        if row is None:
            return {"workspace_uuid": workspace_uuid, "archived": False, "archived_tasks": 0}
        task_rows = await session.execute(text("""
            SELECT CAST(task_uuid AS text) AS task_uuid
              FROM analysis_tasks
             WHERE tenant_id=:tenant_id AND analysis_config->>'workspace_uuid'=:workspace_uuid
        """), {"tenant_id": tenant_id, "workspace_uuid": workspace_uuid})
        uuids = [item["task_uuid"] for item in task_rows.mappings().all()]
        archived_tasks = 0
        if uuids:
            from .analysis_task_repository import AnalysisTaskRepository
            archived_tasks = await AnalysisTaskRepository().archive_tasks(
                session, tenant_id=tenant_id, task_uuids=uuids)
        return {"workspace_uuid": workspace_uuid, "archived": True, "archived_tasks": archived_tasks}
