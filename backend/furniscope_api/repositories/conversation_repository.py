"""Persistence primitives for atomic workbench turns and frozen context."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _json(value: Any, fallback: Any) -> Any:
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


class ConversationRepository:
    async def reserve_turn(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_id: int,
        client_turn_id: str,
        idempotency_key: str,
        request_hash: str,
        request_payload: dict[str, Any],
        task_uuid: str | None,
        regenerated_from_turn_uuid: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"conversation-turn:{tenant_id}:{workspace_id}"},
        )
        existing = await session.execute(text("""
            SELECT id,turn_uuid::text,client_turn_id::text,idempotency_key,request_hash,
                   status,request_payload,response_payload,task_id,sequence_no,
                   regenerated_from_turn_uuid::text,created_at,completed_at
              FROM analysis_workspace_turns
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
               AND (
                 idempotency_key=:idempotency_key OR
                 client_turn_id=CAST(:client_turn_id AS uuid)
               )
             ORDER BY id DESC
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "idempotency_key": idempotency_key,
            "client_turn_id": client_turn_id,
        })
        matches = existing.mappings().all()
        if len(matches) == 1:
            current = self._turn_row(matches[0])
            if (
                current["status"] in {"failed", "cancelled"}
                and current["request_hash"] == request_hash
            ):
                retried = await session.execute(text("""
                    UPDATE analysis_workspace_turns
                       SET status='pending',response_payload=NULL,error_code=NULL,
                           user_message_uuid=NULL,assistant_message_uuid=NULL,
                           started_at=CURRENT_TIMESTAMP,completed_at=NULL,cancelled_at=NULL,
                           updated_at=CURRENT_TIMESTAMP
                     WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
                       AND turn_uuid=CAST(:turn_uuid AS uuid)
                       AND status IN ('failed','cancelled')
                    RETURNING id,turn_uuid::text,client_turn_id::text,idempotency_key,
                              request_hash,status,request_payload,response_payload,task_id,
                              sequence_no,regenerated_from_turn_uuid::text,created_at,completed_at
                """), {
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "turn_uuid": current["turn_uuid"],
                })
                return self._turn_row(retried.mappings().one()), True
            return current, False
        if len(matches) > 1:
            return {}, False
        blocking = await session.execute(text("""
            SELECT turn_uuid::text,sequence_no FROM analysis_workspace_turns
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id AND status='pending'
             ORDER BY sequence_no LIMIT 1
        """), {"tenant_id": tenant_id, "workspace_id": workspace_id})
        blocking_row = blocking.mappings().one_or_none()
        if blocking_row:
            return {
                "blocking_turn_uuid": blocking_row["turn_uuid"],
                "blocking_sequence_no": int(blocking_row["sequence_no"]),
            }, False
        task_id = None
        if task_uuid:
            task_id = await session.scalar(text("""
                SELECT t.id FROM analysis_tasks t
                  JOIN analysis_workspaces w
                    ON w.id=:workspace_id AND w.tenant_id=t.tenant_id
                 WHERE t.tenant_id=:tenant_id AND t.task_uuid=CAST(:task_uuid AS uuid)
                   AND t.status<>'cancelled'
                   AND t.analysis_config->>'workspace_uuid'=w.workspace_uuid::text
            """), {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "task_uuid": task_uuid,
            })
            if task_id is None:
                return {}, False
        turn_uuid = str(uuid4())
        sequence_no = int(await session.scalar(text("""
            SELECT COALESCE(max(sequence_no),0)+1 FROM analysis_workspace_turns
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
        """), {"tenant_id": tenant_id, "workspace_id": workspace_id}) or 1)
        result = await session.execute(text("""
            INSERT INTO analysis_workspace_turns(
              turn_uuid,tenant_id,workspace_id,client_turn_id,idempotency_key,
              request_hash,request_payload,task_id,regenerated_from_turn_uuid,created_by,
              sequence_no
            )
            VALUES(
              CAST(:turn_uuid AS uuid),:tenant_id,:workspace_id,CAST(:client_turn_id AS uuid),
              :idempotency_key,:request_hash,CAST(:request_payload AS jsonb),:task_id,
              CAST(:regenerated_from_turn_uuid AS uuid),:user_id,:sequence_no
            )
            ON CONFLICT DO NOTHING
            RETURNING id,turn_uuid::text,client_turn_id::text,idempotency_key,request_hash,
                      status,request_payload,response_payload,task_id,sequence_no,
                      regenerated_from_turn_uuid::text,created_at,completed_at
        """), {
            "turn_uuid": turn_uuid,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "client_turn_id": client_turn_id,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
            "request_payload": json.dumps(request_payload, ensure_ascii=False, default=str),
            "task_id": task_id,
            "regenerated_from_turn_uuid": regenerated_from_turn_uuid,
            "user_id": user_id,
            "sequence_no": sequence_no,
        })
        row = result.mappings().one_or_none()
        if row:
            return self._turn_row(row), True
        existing = await session.execute(text("""
            SELECT id,turn_uuid::text,client_turn_id::text,idempotency_key,request_hash,
                   status,request_payload,response_payload,task_id,sequence_no,
                   regenerated_from_turn_uuid::text,created_at,completed_at
              FROM analysis_workspace_turns
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
               AND (
                 idempotency_key=:idempotency_key OR
                 client_turn_id=CAST(:client_turn_id AS uuid)
               )
             ORDER BY id DESC
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "idempotency_key": idempotency_key,
            "client_turn_id": client_turn_id,
        })
        matches = existing.mappings().all()
        if len(matches) != 1:
            return {}, False
        return self._turn_row(matches[0]), False

    async def get_turn(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_id: int,
        turn_uuid: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        lock = " FOR UPDATE OF t" if for_update else ""
        result = await session.execute(text(f"""
            SELECT t.id,t.turn_uuid::text,t.client_turn_id::text,t.idempotency_key,
                   t.request_hash,t.status,t.request_payload,t.response_payload,
                   task.task_uuid::text,t.sequence_no,t.regenerated_from_turn_uuid::text,
                   t.user_message_uuid::text,t.assistant_message_uuid::text,
                   t.error_code,t.created_at,t.completed_at,t.cancelled_at
              FROM analysis_workspace_turns t
              LEFT JOIN analysis_tasks task
                ON task.id=t.task_id AND task.tenant_id=t.tenant_id
             WHERE t.tenant_id=:tenant_id AND t.workspace_id=:workspace_id
               AND t.turn_uuid=CAST(:turn_uuid AS uuid)
             {lock}
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "turn_uuid": turn_uuid,
        })
        row = result.mappings().one_or_none()
        return self._turn_row(row) if row else None

    async def list_turns(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_id: int,
        offset: int,
        limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        params = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "offset": offset,
            "limit": limit,
        }
        total = int(await session.scalar(text("""
            SELECT count(*) FROM analysis_workspace_turns
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
        """), params) or 0)
        rows = await session.execute(text("""
            SELECT t.turn_uuid::text,t.client_turn_id::text,t.status,t.sequence_no,
                   task.task_uuid::text,t.regenerated_from_turn_uuid::text,
                   t.user_message_uuid::text,t.assistant_message_uuid::text,
                   t.request_payload->>'question' AS question,
                   t.response_payload->>'answer' AS answer,
                   t.created_at,t.completed_at
              FROM analysis_workspace_turns t
              LEFT JOIN analysis_tasks task
                ON task.id=t.task_id AND task.tenant_id=t.tenant_id
             WHERE t.tenant_id=:tenant_id AND t.workspace_id=:workspace_id
             ORDER BY t.created_at DESC,t.id DESC
             OFFSET :offset LIMIT :limit
        """), params)
        return [dict(row) for row in rows.mappings().all()], total

    async def get_state(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_id: int,
    ) -> dict[str, Any]:
        result = await session.execute(text("""
            SELECT s.state_uuid::text,s.revision,s.current_product_id,p.name AS current_product_label,
                   s.current_market,s.compared_markets,s.current_dataset_id,
                   task.task_uuid::text AS current_task_uuid,s.current_analysis_stage,
                   s.pending_confirmation,s.last_user_intent,s.resolved_references,s.updated_at
              FROM analysis_workspace_states s
              LEFT JOIN products p
                ON p.id=s.current_product_id AND p.tenant_id=s.tenant_id
              LEFT JOIN analysis_tasks task
                ON task.id=s.current_task_id AND task.tenant_id=s.tenant_id
             WHERE s.tenant_id=:tenant_id AND s.workspace_id=:workspace_id
        """), {"tenant_id": tenant_id, "workspace_id": workspace_id})
        row = result.mappings().one_or_none()
        if row is None:
            return {
                "state_uuid": None,
                "revision": 0,
                "current_product_id": None,
                "current_product_label": None,
                "current_market": None,
                "compared_markets": [],
                "current_dataset_id": None,
                "current_task_uuid": None,
                "current_analysis_stage": None,
                "pending_confirmation": None,
                "last_user_intent": None,
                "resolved_references": [],
            }
        item = dict(row)
        item["compared_markets"] = _json(item.get("compared_markets"), [])
        item["pending_confirmation"] = _json(item.get("pending_confirmation"), None)
        item["resolved_references"] = _json(item.get("resolved_references"), [])
        return item

    async def put_state(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_id: int,
        turn_uuid: str,
        expected_revision: int,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = None
        if state.get("current_task_uuid"):
            task_id = await session.scalar(text("""
                SELECT id FROM analysis_tasks
                 WHERE tenant_id=:tenant_id
                   AND task_uuid=CAST(:task_uuid AS uuid)
                   AND status<>'cancelled'
            """), {
                "tenant_id": tenant_id,
                "task_uuid": state["current_task_uuid"],
            })
        result = await session.execute(text("""
            INSERT INTO analysis_workspace_states(
              tenant_id,workspace_id,revision,current_product_id,current_market,
              compared_markets,current_dataset_id,current_task_id,current_analysis_stage,
              pending_confirmation,last_user_intent,resolved_references,updated_by_turn_uuid
            )
            VALUES(
              :tenant_id,:workspace_id,1,:current_product_id,:current_market,
              CAST(:compared_markets AS jsonb),:current_dataset_id,:current_task_id,
              :current_analysis_stage,CAST(:pending_confirmation AS jsonb),:last_user_intent,
              CAST(:resolved_references AS jsonb),CAST(:turn_uuid AS uuid)
            )
            ON CONFLICT(tenant_id,workspace_id) DO UPDATE SET
              revision=analysis_workspace_states.revision+1,
              current_product_id=EXCLUDED.current_product_id,
              current_market=EXCLUDED.current_market,
              compared_markets=EXCLUDED.compared_markets,
              current_dataset_id=EXCLUDED.current_dataset_id,
              current_task_id=EXCLUDED.current_task_id,
              current_analysis_stage=EXCLUDED.current_analysis_stage,
              pending_confirmation=EXCLUDED.pending_confirmation,
              last_user_intent=EXCLUDED.last_user_intent,
              resolved_references=EXCLUDED.resolved_references,
              updated_by_turn_uuid=EXCLUDED.updated_by_turn_uuid,
              updated_at=CURRENT_TIMESTAMP
            WHERE analysis_workspace_states.revision=:expected_revision
            RETURNING state_uuid::text,revision,current_product_id,current_market,
                      compared_markets,current_dataset_id,current_analysis_stage,
                      pending_confirmation,last_user_intent,resolved_references,updated_at
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "turn_uuid": turn_uuid,
            "expected_revision": expected_revision,
            "current_product_id": state.get("current_product_id"),
            "current_market": state.get("current_market"),
            "compared_markets": json.dumps(state.get("compared_markets") or []),
            "current_dataset_id": state.get("current_dataset_id"),
            "current_task_id": task_id,
            "current_analysis_stage": state.get("current_analysis_stage"),
            "pending_confirmation": (
                None
                if state.get("pending_confirmation") is None
                else json.dumps(state["pending_confirmation"], ensure_ascii=False, default=str)
            ),
            "last_user_intent": state.get("last_user_intent"),
            "resolved_references": json.dumps(
                state.get("resolved_references") or [],
                ensure_ascii=False,
                default=str,
            ),
        })
        row = result.mappings().one_or_none()
        if row is None:
            raise RuntimeError("CONVERSATION_STATE_CONFLICT")
        item = dict(row)
        item["current_product_label"] = state.get("current_product_label")
        item["current_task_uuid"] = state.get("current_task_uuid")
        item["compared_markets"] = _json(item.get("compared_markets"), [])
        item["pending_confirmation"] = _json(item.get("pending_confirmation"), None)
        item["resolved_references"] = _json(item.get("resolved_references"), [])
        return item

    async def complete_turn(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_id: int,
        turn_uuid: str,
        question: str,
        answer: str,
        message_kind: str,
        task_uuid: str | None,
        context_revision: int | None,
        context_configuration: dict[str, Any],
        context_sources: list[dict[str, Any]],
        estimated_tokens: int,
        truncated_sources: list[dict[str, Any]],
        context_hash: str,
        citations: list[dict[str, Any]],
        suggested_actions: list[dict[str, Any]],
        resolved_state: dict[str, Any],
        state_revision: int,
        resolution_log: list[dict[str, Any]],
        context_conflicts: list[dict[str, Any]],
        tool_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        tool_results = tool_results or []
        current = await self.get_turn(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            turn_uuid=turn_uuid,
            for_update=True,
        )
        if current is None or current["status"] != "pending":
            return current
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:workspace_id)"),
            {"workspace_id": workspace_id},
        )
        task_id = None
        if task_uuid:
            task_id = await session.scalar(text("""
                SELECT id FROM analysis_tasks
                 WHERE tenant_id=:tenant_id AND task_uuid=CAST(:task_uuid AS uuid)
            """), {"tenant_id": tenant_id, "task_uuid": task_uuid})
        sequence = int(await session.scalar(text("""
            SELECT COALESCE(max(seq_no),0) FROM analysis_workspace_messages
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
        """), {"tenant_id": tenant_id, "workspace_id": workspace_id}) or 0)
        user_message_uuid = str(uuid4())
        assistant_message_uuid = str(uuid4())
        await session.execute(text("""
            INSERT INTO analysis_workspace_messages(
              message_uuid,tenant_id,workspace_id,analysis_task_id,role,message_kind,
              content,seq_no,client_message_id,evidence_refs,metadata,created_by,turn_uuid
            )
            VALUES(
              CAST(:user_message_uuid AS uuid),:tenant_id,:workspace_id,:task_id,'user','text',
              :question,:user_seq,:user_client_id,'[]'::jsonb,'{}'::jsonb,:user_id,
              CAST(:turn_uuid AS uuid)
            ),(
              CAST(:assistant_message_uuid AS uuid),:tenant_id,:workspace_id,:task_id,
              'assistant',:message_kind,:answer,:assistant_seq,:assistant_client_id,
              CAST(:evidence_refs AS jsonb),CAST(:metadata AS jsonb),:user_id,
              CAST(:turn_uuid AS uuid)
            )
        """), {
            "user_message_uuid": user_message_uuid,
            "assistant_message_uuid": assistant_message_uuid,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "task_id": task_id,
            "question": question[:20000],
            "answer": answer[:20000],
            "user_seq": sequence + 1,
            "assistant_seq": sequence + 2,
            "user_client_id": f"turn:{turn_uuid}:user",
            "assistant_client_id": f"turn:{turn_uuid}:assistant",
            "message_kind": message_kind,
            "evidence_refs": json.dumps(
                [item["citation_uuid"] for item in citations],
                ensure_ascii=False,
            ),
            "metadata": json.dumps(
                {
                    "context_sources": context_sources,
                    "citations": citations,
                    "suggested_actions": suggested_actions,
                    "resolved_state": resolved_state,
                    "context_conflicts": context_conflicts,
                    "resolution_log": resolution_log,
                    "tool_results": tool_results,
                },
                ensure_ascii=False,
                default=str,
            ),
            "user_id": user_id,
            "turn_uuid": turn_uuid,
        })
        saved_state = await self.put_state(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            turn_uuid=turn_uuid,
            expected_revision=state_revision,
            state=resolved_state,
        )
        snapshot_uuid = str(uuid4())
        await session.execute(text("""
            INSERT INTO analysis_context_snapshots(
              context_snapshot_uuid,tenant_id,workspace_id,turn_uuid,context_revision,
              configuration,sources,estimated_tokens,truncated_sources,context_hash,
              state_snapshot,resolution_log
            )
            VALUES(
              CAST(:snapshot_uuid AS uuid),:tenant_id,:workspace_id,CAST(:turn_uuid AS uuid),
              :context_revision,CAST(:configuration AS jsonb),CAST(:sources AS jsonb),
              :estimated_tokens,CAST(:truncated_sources AS jsonb),:context_hash,
              CAST(:state_snapshot AS jsonb),CAST(:resolution_log AS jsonb)
            )
        """), {
            "snapshot_uuid": snapshot_uuid,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "turn_uuid": turn_uuid,
            "context_revision": context_revision,
            "configuration": json.dumps(context_configuration, ensure_ascii=False, default=str),
            "sources": json.dumps(context_sources, ensure_ascii=False, default=str),
            "estimated_tokens": estimated_tokens,
            "truncated_sources": json.dumps(truncated_sources, ensure_ascii=False, default=str),
            "context_hash": context_hash,
            "state_snapshot": json.dumps(saved_state, ensure_ascii=False, default=str),
            "resolution_log": json.dumps(resolution_log, ensure_ascii=False, default=str),
        })
        for citation in citations:
            await session.execute(text("""
                INSERT INTO citations(
                  citation_uuid,tenant_id,workspace_id,turn_uuid,assistant_message_uuid,
                  source_type,source_id,source_version,label,locator,excerpt,score
                )
                VALUES(
                  CAST(:citation_uuid AS uuid),:tenant_id,:workspace_id,CAST(:turn_uuid AS uuid),
                  CAST(:assistant_message_uuid AS uuid),:source_type,:source_id,:source_version,
                  :label,CAST(:locator AS jsonb),:excerpt,:score
                )
            """), {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "turn_uuid": turn_uuid,
                "assistant_message_uuid": assistant_message_uuid,
                "citation_uuid": citation["citation_uuid"],
                "source_type": citation["source_type"],
                "source_id": citation["source_id"],
                "source_version": (
                    str(citation["source_version"])
                    if citation.get("source_version") is not None else None
                ),
                "label": citation["label"],
                "locator": json.dumps(citation.get("locator") or {}, ensure_ascii=False),
                "excerpt": citation.get("excerpt"),
                "score": citation.get("score"),
            })
        payload = {
            "turn_uuid": turn_uuid,
            "user_message_uuid": user_message_uuid,
            "assistant_message_uuid": assistant_message_uuid,
            "answer": answer,
            "citations": citations,
            "memory_candidates": [],
            "context_sources": context_sources,
            "suggested_actions": suggested_actions,
            "tool_results": tool_results,
            "context_snapshot_uuid": snapshot_uuid,
            "resolved_state": saved_state,
            "context_conflicts": context_conflicts,
            "resolution_log": resolution_log,
        }
        await session.execute(text("""
            UPDATE analysis_workspace_turns
               SET status='completed',user_message_uuid=CAST(:user_message_uuid AS uuid),
                   assistant_message_uuid=CAST(:assistant_message_uuid AS uuid),
                   response_payload=CAST(:response_payload AS jsonb),
                   completed_at=CURRENT_TIMESTAMP,error_code=NULL,updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
               AND turn_uuid=CAST(:turn_uuid AS uuid) AND status='pending'
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "turn_uuid": turn_uuid,
            "user_message_uuid": user_message_uuid,
            "assistant_message_uuid": assistant_message_uuid,
            "response_payload": json.dumps(payload, ensure_ascii=False, default=str),
        })
        await session.execute(text("""
            UPDATE analysis_workspaces SET updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND id=:workspace_id
        """), {"tenant_id": tenant_id, "workspace_id": workspace_id})
        return payload

    async def attach_memory_candidates(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_id: int,
        turn_uuid: str,
        memory_candidates: list[dict[str, Any]],
    ) -> None:
        await session.execute(text("""
            UPDATE analysis_workspace_turns
               SET response_payload=jsonb_set(
                     response_payload,'{memory_candidates}',CAST(:candidates AS jsonb),true
                   ),
                   updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
               AND turn_uuid=CAST(:turn_uuid AS uuid) AND status='completed'
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "turn_uuid": turn_uuid,
            "candidates": json.dumps(memory_candidates, ensure_ascii=False, default=str),
        })
        await session.execute(text("""
            UPDATE analysis_workspace_messages m
               SET metadata=jsonb_set(
                     m.metadata,'{memory_candidates}',CAST(:candidates AS jsonb),true
                   )
              FROM analysis_workspace_turns t
             WHERE t.tenant_id=:tenant_id AND t.workspace_id=:workspace_id
               AND t.turn_uuid=CAST(:turn_uuid AS uuid)
               AND m.tenant_id=t.tenant_id
               AND m.message_uuid=t.assistant_message_uuid
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "turn_uuid": turn_uuid,
            "candidates": json.dumps(memory_candidates, ensure_ascii=False, default=str),
        })

    async def fail_turn(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_id: int,
        turn_uuid: str,
        error_code: str,
    ) -> bool:
        result = await session.execute(text("""
            UPDATE analysis_workspace_turns
               SET status='failed',error_code=:error_code,completed_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
               AND turn_uuid=CAST(:turn_uuid AS uuid) AND status='pending'
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "turn_uuid": turn_uuid,
            "error_code": error_code,
        })
        return bool(result.rowcount)

    async def cancel_turn(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_id: int,
        turn_uuid: str,
    ) -> bool:
        result = await session.execute(text("""
            UPDATE analysis_workspace_turns
               SET status='cancelled',cancelled_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
               AND turn_uuid=CAST(:turn_uuid AS uuid) AND status='pending'
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "turn_uuid": turn_uuid,
        })
        return bool(result.rowcount)

    async def get_context(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        workspace_id: int,
    ) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT context_uuid::text,revision,configuration,created_at
              FROM analysis_workspace_contexts
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id AND is_current
        """), {"tenant_id": tenant_id, "workspace_id": workspace_id})
        row = result.mappings().one_or_none()
        if not row:
            return None
        item = dict(row)
        item["configuration"] = _json(item.get("configuration"), {})
        return item

    async def put_context(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        workspace_id: int,
        configuration: dict[str, Any],
    ) -> dict[str, Any]:
        current = await self.get_context(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        if current is not None and current["configuration"] == configuration:
            return current
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:workspace_id)"),
            {"workspace_id": workspace_id},
        )
        current_revision = int(await session.scalar(text("""
            SELECT COALESCE(max(revision),0) FROM analysis_workspace_contexts
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id
        """), {"tenant_id": tenant_id, "workspace_id": workspace_id}) or 0)
        await session.execute(text("""
            UPDATE analysis_workspace_contexts SET is_current=FALSE
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id AND is_current
        """), {"tenant_id": tenant_id, "workspace_id": workspace_id})
        result = await session.execute(text("""
            INSERT INTO analysis_workspace_contexts(
              tenant_id,workspace_id,revision,is_current,configuration,created_by
            )
            VALUES(:tenant_id,:workspace_id,:revision,TRUE,CAST(:configuration AS jsonb),:user_id)
            RETURNING context_uuid::text,revision,configuration,created_at
        """), {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "revision": current_revision + 1,
            "configuration": json.dumps(configuration, ensure_ascii=False, default=str),
            "user_id": user_id,
        })
        item = dict(result.mappings().one())
        item["configuration"] = _json(item.get("configuration"), {})
        return item

    async def get_citation(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        citation_uuid: str,
    ) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT citation_uuid::text,source_type,source_id,source_version,label,
                   locator,excerpt,score::float8
              FROM citations
             WHERE tenant_id=:tenant_id AND citation_uuid=CAST(:citation_uuid AS uuid)
        """), {"tenant_id": tenant_id, "citation_uuid": citation_uuid})
        row = result.mappings().one_or_none()
        if not row:
            return None
        item = dict(row)
        item["locator"] = _json(item.get("locator"), {})
        return item

    @staticmethod
    def _turn_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["request_payload"] = _json(item.get("request_payload"), {})
        item["response_payload"] = _json(item.get("response_payload"), None)
        return item
