"""PostgreSQL persistence for workflow control state.

Business result writers remain in injected capability tools because their exact
algorithms and payload schemas are not fully locked by the three supplied docs.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg

from .contracts import ConfirmationAcceptance, ResumeOutboxEvent, StageRunHandle
from .state import FurniScopeGraphState, PartialFailure, UserConfirmation


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _snapshot(state: FurniScopeGraphState) -> dict[str, Any]:
    allowed = {
        "task_id", "task_uuid", "tenant_id", "status", "external_stage",
        "internal_stage", "progress_percent", "product_id",
        "product_profile_version", "dataset_id", "target_market",
        "version_bundle", "analysis_config", "product_context_ref",
        "valid_listing_ids", "valid_review_ids", "competitor_set_version",
        "quality_flags", "trend_eligible", "stage_results",
        "user_confirmation", "retry_context", "partial_failures",
        "fatal_error", "cancel_requested",
    }
    return {key: state[key] for key in allowed if key in state}


class PostgresWorkflowRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    async def create_or_load_task(self, state: FurniScopeGraphState) -> dict[str, Any]:
        # Task creation requires created_by and full creation command, which are
        # intentionally not members of the locked Graph State. The caller creates
        # the draft task transactionally, then starts this engine with task_id.
        row = await self.pool.fetchrow(
            """SELECT id,task_uuid,tenant_id,product_id,product_profile_version_id,
                      dataset_id,target_country,target_platform,analysis_currency,
                      status,external_stage,internal_stage,progress_percent,
                      analysis_config,ontology_version,scoring_version,
                      prompt_bundle_version,model_route_version
               FROM furniscope.analysis_tasks
               WHERE id=$1 AND tenant_id=$2""",
            state["task_id"], state["tenant_id"],
        )
        if row is None:
            raise ValueError("Task not found in tenant scope")
        return dict(row)

    async def start_stage(
        self, state: FurniScopeGraphState, stage_code: str, input_ref: dict[str, Any]
    ) -> StageRunHandle:
        stable = _json(input_ref)
        idem_base = hashlib.sha256(
            f'{state["task_uuid"]}:{stage_code}:{stable}'.encode()
        ).hexdigest()
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", state["task_id"])
            existing = await conn.fetchrow(
                """SELECT id,status,output_ref FROM furniscope.task_stage_runs
                   WHERE idempotency_key LIKE $1 AND status IN ('succeeded','partial_succeeded','skipped')
                   ORDER BY attempt_no DESC LIMIT 1""",
                f"{idem_base}:%",
            )
            if existing:
                output_ref = existing["output_ref"]
                if isinstance(output_ref, str):
                    output_ref = json.loads(output_ref)
                return StageRunHandle(existing["id"], existing["status"], output_ref)
            attempt = await conn.fetchval(
                "SELECT COALESCE(max(attempt_no),0)+1 FROM furniscope.task_stage_runs WHERE task_id=$1 AND stage_code=$2",
                state["task_id"], stage_code,
            )
            idem = f"{idem_base}:{attempt}"
            run_id = await conn.fetchval(
                """INSERT INTO furniscope.task_stage_runs
                   (tenant_id,task_id,stage_code,attempt_no,idempotency_key,status,input_ref,started_at)
                   VALUES($1,$2,$3,$4,$5,'running',$6::jsonb,now()) RETURNING id""",
                state["tenant_id"], state["task_id"], stage_code, attempt, idem, stable,
            )
            return StageRunHandle(int(run_id), "running")

    async def finish_stage(self, run_id: int, status: str, output_ref: dict[str, Any]) -> None:
        await self.pool.execute(
            """UPDATE furniscope.task_stage_runs SET status=$2,output_ref=$3::jsonb,ended_at=now()
               WHERE id=$1 AND status IN ('running','queued','retry_scheduled','waiting_human')""",
            run_id, status, _json(output_ref),
        )

    async def fail_stage(self, run_id: int, code: str, message: str, retryable: bool) -> None:
        await self.pool.execute(
            """UPDATE furniscope.task_stage_runs
               SET status='failed',error_code=$2,error_message=$3,retryable=$4,ended_at=now()
               WHERE id=$1""",
            run_id, code, message[:1000], retryable,
        )

    async def update_task(self, state: FurniScopeGraphState, **changes: Any) -> None:
        permitted = {
            "status", "external_stage", "internal_stage", "progress_percent",
            "checkpoint_stage", "failure_code", "failure_message",
        }
        illegal = set(changes) - permitted
        if illegal:
            raise ValueError(f"Illegal task update fields: {sorted(illegal)}")
        assignments: list[str] = []
        values: list[Any] = [state["task_id"], state["tenant_id"]]
        for key, value in changes.items():
            values.append(value)
            assignments.append(f"{key}=${len(values)}")
        if not assignments:
            return
        await self.pool.execute(
            f"UPDATE furniscope.analysis_tasks SET {','.join(assignments)},updated_at=now() WHERE id=$1 AND tenant_id=$2",
            *values,
        )

    async def save_partial_failures(
        self, state: FurniScopeGraphState, run_id: int, failures: list[PartialFailure]
    ) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            for failure in failures:
                await conn.execute(
                    """INSERT INTO furniscope.workflow_partial_failures
                       (tenant_id,task_id,stage_run_id,stage_code,unit_type,failed_unit_ids,
                        failed_count,total_count,impact,confidence_cap,retryable)
                       VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10,$11)""",
                    state["tenant_id"], state["task_id"], run_id,
                    failure["stage_code"], failure["unit_type"],
                    _json(failure["failed_unit_ids"]), failure["failed_count"],
                    failure["total_count"], failure["impact"],
                    failure.get("confidence_cap"), failure["retryable"],
                )

    async def save_business_checkpoint(
        self, state: FurniScopeGraphState, stage_code: str, safe: bool
    ) -> str:
        snapshot = _snapshot(state)
        encoded = _json(snapshot)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        checkpoint_id = f'{state["task_uuid"]}:{stage_code}:{digest[:16]}'
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", state["task_id"])
            existing = await conn.fetchval(
                "SELECT checkpoint_id FROM furniscope.workflow_checkpoints WHERE checkpoint_id=$1",
                checkpoint_id,
            )
            if existing:
                return str(existing)
            version = await conn.fetchval(
                "SELECT COALESCE(max(checkpoint_version),0)+1 FROM furniscope.workflow_checkpoints WHERE task_id=$1",
                state["task_id"],
            )
            await conn.execute(
                """INSERT INTO furniscope.workflow_checkpoints
                   (checkpoint_id,thread_id,tenant_id,task_id,stage_code,checkpoint_version,
                    state_snapshot,state_hash,is_safe_resume,status)
                   VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,'active')""",
                checkpoint_id, state["task_uuid"], state["tenant_id"], state["task_id"],
                stage_code, version, encoded, digest, safe,
            )
        return checkpoint_id

    async def begin_confirmation_wait(
        self, state: FurniScopeGraphState, confirmation: UserConfirmation
    ) -> str:
        """Atomically publish the safe checkpoint, wait stage, confirmation and task state."""
        checkpoint_state = {**state, "user_confirmation": confirmation}
        snapshot = _snapshot(checkpoint_state)
        encoded = _json(snapshot)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        stage_code = confirmation["checkpoint_stage"]
        checkpoint_id = f'{state["task_uuid"]}:{stage_code}:{digest[:16]}'
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", state["task_id"])
            existing = await conn.fetchrow(
                """SELECT checkpoint_id,checkpoint_stage,status FROM furniscope.user_confirmations
                   WHERE confirmation_id=$1::uuid AND tenant_id=$2 AND task_id=$3""",
                confirmation["confirmation_id"], state["tenant_id"], state["task_id"],
            )
            if existing:
                if existing["checkpoint_stage"] != stage_code or existing["status"] != "pending":
                    raise ValueError("Confirmation idempotency conflict")
                return str(existing["checkpoint_id"])
            version = await conn.fetchval(
                "SELECT COALESCE(max(checkpoint_version),0)+1 FROM furniscope.workflow_checkpoints WHERE task_id=$1",
                state["task_id"],
            )
            await conn.execute(
                """INSERT INTO furniscope.workflow_checkpoints
                   (checkpoint_id,thread_id,tenant_id,task_id,stage_code,checkpoint_version,
                    state_snapshot,state_hash,is_safe_resume,status)
                   VALUES($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,'active')""",
                checkpoint_id, state["task_uuid"], state["tenant_id"],
                state["task_id"], stage_code, version, encoded, digest, True,
            )
            attempt = await conn.fetchval(
                "SELECT COALESCE(max(attempt_no),0)+1 FROM furniscope.task_stage_runs WHERE task_id=$1 AND stage_code='user_confirmation'",
                state["task_id"],
            )
            wait_run_id = await conn.fetchval(
                """INSERT INTO furniscope.task_stage_runs
                   (tenant_id,task_id,stage_code,attempt_no,idempotency_key,status,input_ref,started_at)
                   VALUES($1,$2,'user_confirmation',$3,$4,'waiting_human',$5::jsonb,now())
                   RETURNING id""",
                state["tenant_id"], state["task_id"], attempt,
                f'confirmation-wait:{confirmation["confirmation_id"]}',
                _json({"confirmation_id": confirmation["confirmation_id"], "checkpoint_id": checkpoint_id}),
            )
            await conn.execute(
                """INSERT INTO furniscope.user_confirmations
               (confirmation_id,tenant_id,task_id,confirmation_type,question,
                recommended_option,options,evidence_refs,impact,checkpoint_stage,
                expires_at,idempotency_key,checkpoint_id)
               VALUES($1::uuid,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10,$11,$12,$13)""",
                confirmation["confirmation_id"], state["tenant_id"], state["task_id"],
                confirmation["confirmation_type"], confirmation["question"],
                confirmation["recommended_option"], _json(confirmation["options"]),
                _json(confirmation["evidence_refs"]), _json(confirmation["impact"]),
                stage_code, confirmation["expires_at"],
                f'confirmation:{confirmation["confirmation_id"]}', checkpoint_id,
            )
            await conn.execute(
                """UPDATE furniscope.analysis_tasks
                   SET status='waiting_human',internal_stage='user_confirmation',checkpoint_stage=$3,updated_at=now()
                   WHERE id=$1 AND tenant_id=$2""",
                state["task_id"], state["tenant_id"], stage_code,
            )
            if wait_run_id is None:
                raise RuntimeError("Failed to create user_confirmation stage run")
        return checkpoint_id

    async def accept_confirmation(
        self, confirmation_id: str, selected_option: str, user_input: Any, user_id: int
    ) -> ConfirmationAcceptance:
        expired = False
        accepted: ConfirmationAcceptance | None = None
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """SELECT c.*,u.tenant_id AS responder_tenant,u.role_code,
                          t.task_uuid,t.status AS task_status,
                          w.is_safe_resume,w.status AS checkpoint_status
                   FROM furniscope.user_confirmations c
                   JOIN furniscope.users u ON u.id=$2
                   JOIN furniscope.analysis_tasks t ON t.id=c.task_id AND t.tenant_id=c.tenant_id
                   JOIN furniscope.workflow_checkpoints w ON w.checkpoint_id=c.checkpoint_id
                   WHERE c.confirmation_id=$1::uuid
                   FOR UPDATE OF c,t,w""",
                confirmation_id, user_id,
            )
            if row is None or row["responder_tenant"] != row["tenant_id"] or row["role_code"] != "user":
                raise ValueError("Confirmation or tenant-scoped user not found")
            if row["status"] == "responded":
                if row["selected_option"] != selected_option:
                    raise ValueError("Idempotency conflict: confirmation already has another answer")
                event = await conn.fetchrow(
                    """SELECT event_uuid FROM furniscope.workflow_control_events
                       WHERE tenant_id=$1 AND idempotency_key=$2""",
                    row["tenant_id"], f"resume:{confirmation_id}",
                )
                if event is None:
                    raise RuntimeError("Responded confirmation has no resume outbox event")
                payload = {
                    "confirmation_id": confirmation_id, "selected_option": selected_option,
                    "user_input": row["user_input"], "checkpoint_stage": row["checkpoint_stage"],
                }
                return ConfirmationAcceptance(str(event["event_uuid"]), row["task_id"], str(row["task_uuid"]), row["tenant_id"], row["checkpoint_id"], payload, True)
            if row["status"] != "pending":
                raise ValueError("Confirmation is not active")
            if row["expires_at"] and row["expires_at"] <= datetime.now(timezone.utc):
                await conn.execute(
                    "UPDATE furniscope.user_confirmations SET status='expired',updated_at=now() WHERE id=$1",
                    row["id"],
                )
                expired = True
            if expired:
                accepted = None
            elif not row["checkpoint_id"] or not row["is_safe_resume"] or row["checkpoint_status"] != "active":
                raise ValueError("Safe active checkpoint is unavailable")
            options = row["options"] if isinstance(row["options"], list) else json.loads(row["options"])
            if not expired and selected_option not in {item["code"] for item in options}:
                raise ValueError("Selected option is not in the original options")
            if not expired:
                payload = {
                    "confirmation_id": confirmation_id, "selected_option": selected_option,
                    "user_input": user_input, "checkpoint_stage": row["checkpoint_stage"],
                }
                await conn.execute(
                """UPDATE furniscope.user_confirmations SET status='responded',selected_option=$2,
                   user_input=$3::jsonb,responded_by=$4,responded_at=now(),updated_at=now()
                   WHERE confirmation_id=$1::uuid AND status='pending'""",
                    confirmation_id, selected_option, _json(user_input), user_id,
                )
                await conn.execute(
                    """UPDATE furniscope.task_stage_runs
                       SET status='succeeded',output_ref=$2::jsonb,ended_at=now()
                       WHERE id=(SELECT id FROM furniscope.task_stage_runs
                                 WHERE task_id=$1 AND stage_code='user_confirmation' AND status='waiting_human'
                                 ORDER BY attempt_no DESC LIMIT 1)""",
                    row["task_id"], _json(payload),
                )
                event_uuid = uuid4()
                await conn.execute(
                """INSERT INTO furniscope.workflow_control_events
                   (event_uuid,tenant_id,task_id,event_type,checkpoint_id,stage_code,payload,
                    idempotency_key,requested_by)
                   VALUES($1,$2,$3,'resume_confirmation',$4,$5,$6::jsonb,$7,$8)
                   ON CONFLICT(tenant_id,idempotency_key) DO NOTHING""",
                    event_uuid, row["tenant_id"], row["task_id"], row["checkpoint_id"],
                    row["checkpoint_stage"], _json(payload), f"resume:{confirmation_id}", user_id,
                )
                await conn.execute(
                    "UPDATE furniscope.analysis_tasks SET status='queued',updated_at=now() WHERE id=$1 AND tenant_id=$2",
                    row["task_id"], row["tenant_id"],
                )
                accepted = ConfirmationAcceptance(str(event_uuid), row["task_id"], str(row["task_uuid"]), row["tenant_id"], row["checkpoint_id"], payload)
        if expired:
            raise ValueError("Confirmation expired; recommended option is not auto-selected")
        if accepted is None:
            raise RuntimeError("Confirmation acceptance did not produce an outbox event")
        return accepted

    async def claim_resume_event(self) -> ResumeOutboxEvent | None:
        async with self.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """WITH candidate AS (
                     SELECT e.id FROM furniscope.workflow_control_events e
                     WHERE e.event_type='resume_confirmation'
                       AND (e.status='pending' OR (e.status='enqueued' AND e.enqueued_at < now()-interval '5 minutes'))
                     ORDER BY e.requested_at,e.id FOR UPDATE SKIP LOCKED LIMIT 1
                   ), claimed AS (
                     UPDATE furniscope.workflow_control_events e
                     SET status='enqueued',enqueued_at=now(),delivery_attempts=e.delivery_attempts+1,error_code=NULL
                     FROM candidate c WHERE e.id=c.id
                     RETURNING e.*
                   )
                   SELECT c.event_uuid,c.task_id,c.tenant_id,c.checkpoint_id,c.payload,t.task_uuid
                   FROM claimed c JOIN furniscope.analysis_tasks t ON t.id=c.task_id"""
            )
            if row is None:
                return None
            payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
            return ResumeOutboxEvent(str(row["event_uuid"]), row["task_id"], str(row["task_uuid"]), row["tenant_id"], row["checkpoint_id"], payload)

    async def consume_resume_event(self, event: ResumeOutboxEvent) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", event.task_id)
            result = await conn.execute(
                """UPDATE furniscope.workflow_control_events
                   SET status='consumed',consumed_at=now(),error_code=NULL
                   WHERE event_uuid=$1::uuid AND status='enqueued'""",
                event.event_uuid,
            )
            if result != "UPDATE 1":
                raise ValueError("Resume event is not enqueued")
            await conn.execute(
                """UPDATE furniscope.workflow_checkpoints SET status='consumed',consumed_at=now()
                   WHERE checkpoint_id=$1 AND task_id=$2 AND status='active' AND is_safe_resume""",
                event.checkpoint_id, event.task_id,
            )

    async def fail_resume_event(self, event_uuid: str, error_code: str) -> None:
        await self.pool.execute(
            """UPDATE furniscope.workflow_control_events SET status='failed',error_code=$2
               WHERE event_uuid=$1::uuid AND status='enqueued'""",
            event_uuid, error_code[:100],
        )

    async def final_persist(
        self, state: FurniScopeGraphState, report_ref: dict[str, Any]
    ) -> dict[str, Any]:
        report_uuid = report_ref.get("report_uuid")
        if not report_uuid:
            raise ValueError("I18 tool must provide persisted report_uuid for final consistency check")
        async with self.pool.acquire() as conn, conn.transaction():
            exists = await conn.fetchval(
                """SELECT EXISTS(SELECT 1 FROM furniscope.analysis_reports
                   WHERE report_uuid=$1::uuid AND analysis_job_id=$2 AND tenant_id=$3)""",
                report_uuid, state["task_id"], state["tenant_id"],
            )
            if not exists:
                raise ValueError("Report does not exist in the task tenant")
            await conn.execute(
                """UPDATE furniscope.analysis_tasks SET status='succeeded',external_stage='completed',
                   internal_stage='completed',progress_percent=100,completed_at=now(),updated_at=now()
                   WHERE id=$1 AND tenant_id=$2""",
                state["task_id"], state["tenant_id"],
            )
        return {"report_uuid": report_uuid}


class PostgresModelTraceWriter:
    """Callable trace sink bound to one task/stage execution context."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        tenant_id: int,
        task_id: int,
        stage_run_id: int,
        prompt_template_id: int | None = None,
    ) -> None:
        self.pool = pool
        self.tenant_id = tenant_id
        self.task_id = task_id
        self.stage_run_id = stage_run_id
        self.prompt_template_id = prompt_template_id

    async def __call__(self, trace: Any) -> None:
        await self.pool.execute(
            """INSERT INTO furniscope.ai_model_runs
               (tenant_id,task_id,stage_run_id,provider,model_id,task_type,
                prompt_template_id,prompt_version,input_hash,output_schema_version,
                input_tokens,output_tokens,image_count,latency_ms,status,retry_count,
                schema_valid)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,0,$13,$14,$15,$16)""",
            self.tenant_id, self.task_id, self.stage_run_id, trace.provider,
            trace.model_id, trace.task_type, self.prompt_template_id,
            trace.prompt_version, trace.input_hash, trace.output_schema_version,
            trace.input_tokens, trace.output_tokens, trace.latency_ms,
            trace.status, trace.retry_count, trace.schema_valid,
        )
