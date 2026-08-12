"""Real PostgreSQL transaction tests using explicitly synthetic demo fixtures."""

from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
from langgraph.checkpoint.memory import InMemorySaver

from backend.furniscope_agent.demo_support import demo_initial_state
from backend.furniscope_agent.graph import FurniScopeAgentEngine, build_graph
from backend.furniscope_agent.repository import PostgresWorkflowRepository
from backend.furniscope_agent.synthetic_toolbox import SyntheticFurnitureToolbox


TEST_DSN = os.getenv("FURNISCOPE_TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DSN, "FURNISCOPE_TEST_DATABASE_URL is not configured")
class PostgresConfirmationTransactionTest(unittest.TestCase):
    def test_confirmation_transaction_outbox_and_rollback(self) -> None:
        asyncio.run(self._scenario())

    def test_synthetic_case_completes_real_database_persistence(self) -> None:
        asyncio.run(self._synthetic_workflow_scenario())

    async def _synthetic_workflow_scenario(self) -> None:
        pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=5)
        try:
            ids = await self._seed_synthetic_case(pool)
            repository = PostgresWorkflowRepository(pool)
            state = demo_initial_state()
            state.update(
                task_id=ids["task_id"], task_uuid=ids["task_uuid"],
                tenant_id=ids["tenant_id"], product_id=ids["product_id"],
                product_profile_version=1, dataset_id=ids["dataset_id"],
            )
            graph = build_graph(repository, SyntheticFurnitureToolbox(pool), checkpointer=InMemorySaver())
            result = await FurniScopeAgentEngine(graph, repository).run(state)
            self.assertEqual(result["status"], "succeeded")
            persisted = await pool.fetchrow(
                """SELECT t.status,t.external_stage,t.progress_percent,r.report_uuid,
                          r.data_scope_snapshot->>'data_class' data_class,m.provider
                   FROM furniscope.analysis_tasks t
                   JOIN furniscope.analysis_reports r ON r.analysis_job_id=t.id
                   JOIN furniscope.ai_model_runs m ON m.id=r.generated_model_run_id
                   WHERE t.id=$1""",
                ids["task_id"],
            )
            self.assertEqual(persisted["status"], "succeeded")
            self.assertEqual(persisted["external_stage"], "completed")
            self.assertEqual(float(persisted["progress_percent"]), 100)
            self.assertEqual(persisted["data_class"], "synthetic_demo")
            self.assertEqual(persisted["provider"], "synthetic_demo")
        finally:
            await pool.close()

    async def _scenario(self) -> None:
        pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=3)
        try:
            ids = await self._seed_synthetic_case(pool)
            repository = PostgresWorkflowRepository(pool)
            state = demo_initial_state()
            state.update(
                task_id=ids["task_id"], task_uuid=ids["task_uuid"],
                tenant_id=ids["tenant_id"], product_id=ids["product_id"],
                product_profile_version=1, dataset_id=ids["dataset_id"],
                internal_stage="data_quality", external_stage="researching_market",
                progress_percent=20,
            )

            # A malformed confirmation must roll back checkpoint, stage and task changes.
            bad = self._confirmation(str(uuid4()))
            bad["evidence_refs"] = []
            with self.assertRaises(asyncpg.CheckViolationError):
                await repository.begin_confirmation_wait(state, bad)
            counts = await pool.fetchrow(
                """SELECT
                     (SELECT count(*) FROM furniscope.workflow_checkpoints WHERE task_id=$1) checkpoints,
                     (SELECT count(*) FROM furniscope.task_stage_runs WHERE task_id=$1) stages,
                     (SELECT count(*) FROM furniscope.user_confirmations WHERE task_id=$1) confirmations""",
                ids["task_id"],
            )
            self.assertEqual(tuple(counts), (0, 0, 0))

            confirmation = self._confirmation(str(uuid4()))
            checkpoint_id = await repository.begin_confirmation_wait(state, confirmation)
            waiting = await pool.fetchrow(
                """SELECT t.status,t.internal_stage,c.status confirmation_status,w.is_safe_resume,s.status stage_status
                   FROM furniscope.analysis_tasks t
                   JOIN furniscope.user_confirmations c ON c.task_id=t.id
                   JOIN furniscope.workflow_checkpoints w ON w.checkpoint_id=c.checkpoint_id
                   JOIN furniscope.task_stage_runs s ON s.task_id=t.id AND s.stage_code='user_confirmation'
                   WHERE t.id=$1""",
                ids["task_id"],
            )
            self.assertEqual(waiting["status"], "waiting_human")
            self.assertEqual(waiting["confirmation_status"], "pending")
            self.assertTrue(waiting["is_safe_resume"])
            self.assertEqual(waiting["stage_status"], "waiting_human")

            with self.assertRaises(ValueError):
                await repository.accept_confirmation(
                    confirmation["confirmation_id"], "continue_with_limit", None,
                    ids["other_tenant_user_id"],
                )
            await pool.execute(
                "UPDATE furniscope.workflow_checkpoints SET is_safe_resume=false WHERE checkpoint_id=$1",
                checkpoint_id,
            )
            with self.assertRaises(ValueError):
                await repository.accept_confirmation(
                    confirmation["confirmation_id"], "continue_with_limit", None, ids["user_id"],
                )
            await pool.execute(
                "UPDATE furniscope.workflow_checkpoints SET is_safe_resume=true WHERE checkpoint_id=$1",
                checkpoint_id,
            )

            accepted = await repository.accept_confirmation(
                confirmation["confirmation_id"], "continue_with_limit",
                {"fixture": "synthetic_demo"}, ids["user_id"],
            )
            self.assertEqual(accepted.checkpoint_id, checkpoint_id)
            self.assertEqual(accepted.payload["checkpoint_stage"], "data_quality")
            accepted_again = await repository.accept_confirmation(
                confirmation["confirmation_id"], "continue_with_limit",
                {"fixture": "synthetic_demo"}, ids["user_id"],
            )
            self.assertTrue(accepted_again.already_accepted)
            self.assertEqual(accepted_again.event_uuid, accepted.event_uuid)
            with self.assertRaises(ValueError):
                await repository.accept_confirmation(
                    confirmation["confirmation_id"], "stop", None, ids["user_id"],
                )

            event = await repository.claim_resume_event()
            self.assertIsNotNone(event)
            self.assertEqual(event.payload["checkpoint_stage"], "data_quality")
            # Simulate a worker crash after claim. A stale lease is reclaimable.
            await pool.execute(
                """UPDATE furniscope.workflow_control_events
                   SET enqueued_at=now()-interval '6 minutes' WHERE event_uuid=$1::uuid""",
                event.event_uuid,
            )
            reclaimed = await repository.claim_resume_event()
            self.assertEqual(reclaimed.event_uuid, event.event_uuid)
            attempts = await pool.fetchval(
                "SELECT delivery_attempts FROM furniscope.workflow_control_events WHERE event_uuid=$1::uuid",
                event.event_uuid,
            )
            self.assertEqual(attempts, 2)
            await repository.consume_resume_event(reclaimed)
            final = await pool.fetchrow(
                """SELECT e.status event_status,w.status checkpoint_status,c.status confirmation_status,s.status stage_status
                   FROM furniscope.workflow_control_events e
                   JOIN furniscope.workflow_checkpoints w ON w.checkpoint_id=e.checkpoint_id
                   JOIN furniscope.user_confirmations c ON c.checkpoint_id=w.checkpoint_id
                   JOIN furniscope.task_stage_runs s ON s.task_id=e.task_id AND s.stage_code='user_confirmation'
                   WHERE e.event_uuid=$1::uuid""",
                accepted.event_uuid,
            )
            self.assertEqual(tuple(final), ("consumed", "consumed", "responded", "succeeded"))
            self.assertIsNone(await repository.claim_resume_event())
        finally:
            await pool.close()

    @staticmethod
    def _confirmation(confirmation_id: str) -> dict:
        return {
            "confirmation_id": confirmation_id,
            "confirmation_type": "insufficient_data",
            "question": "合成评论样本不足，是否继续并降低置信度？",
            "recommended_option": "continue_with_limit",
            "options": [
                {"code": "continue_with_limit", "label": "继续并标记限制"},
                {"code": "stop", "label": "停止"},
            ],
            "evidence_refs": [{"type": "synthetic_fixture", "id": "SYN-FURN-001"}],
            "impact": {"confidence_cap": 0.7, "data_class": "synthetic_demo"},
            "checkpoint_stage": "data_quality",
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }

    @staticmethod
    async def _seed_synthetic_case(pool: asyncpg.Pool) -> dict:
        suffix = uuid4().hex[:10]
        async with pool.acquire() as conn, conn.transaction():
            tenant_id = await conn.fetchval(
                "INSERT INTO furniscope.tenants(tenant_code,name,status) VALUES($1,$2,'active') RETURNING id",
                f"SYN_{suffix.upper()}", "合成家具企业（仅测试）",
            )
            user_id = await conn.fetchval(
                """INSERT INTO furniscope.users(tenant_id,email,password_hash,name,role_code,status)
                   VALUES($1,$2,$3,'合成测试用户','user','active') RETURNING id""",
                tenant_id, f"synthetic-{suffix}@example.invalid", "synthetic-fixture-not-a-login-secret",
            )
            product_id = await conn.fetchval(
                "INSERT INTO furniscope.products(tenant_id,sku,name,analysis_status) VALUES($1,$2,'合成模块沙发','ready') RETURNING id",
                tenant_id, f"SYN-SOFA-{suffix}",
            )
            profile_id = await conn.fetchval(
                """INSERT INTO furniscope.product_profile_versions
                   (tenant_id,product_id,version_no,schema_version,status,completeness_score,source_summary,confirmed_by,confirmed_at)
                   VALUES($1,$2,1,'synthetic-v1','confirmed',1,'{"data_class":"synthetic_demo"}'::jsonb,$3,now()) RETURNING id""",
                tenant_id, product_id, user_id,
            )
            await conn.execute("UPDATE furniscope.products SET current_profile_version_id=$2 WHERE id=$1", product_id, profile_id)
            dataset_id = await conn.fetchval(
                """INSERT INTO furniscope.market_datasets
                   (tenant_id,name,platform,market_country,category_code,data_end_date,source_type,source_name,status,quality_report,limitations)
                   VALUES($1,'合成美国家具样本','amazon','US','sofa',CURRENT_DATE,'demo_synthetic','FurniScope synthetic fixture','ready',
                          '{"data_class":"synthetic_demo"}'::jsonb,'["非真实市场数据"]'::jsonb) RETURNING id""",
                tenant_id,
            )
            task = await conn.fetchrow(
                """INSERT INTO furniscope.analysis_tasks
                   (tenant_id,job_name,product_id,product_profile_version_id,dataset_id,target_country,target_platform,
                    analysis_currency,status,external_stage,internal_stage,progress_percent,ontology_version,scoring_version,
                    prompt_bundle_version,model_route_version,idempotency_key,created_by)
                   VALUES($1,'合成端到端事务测试',$2,$3,$4,'US','amazon','USD','running','researching_market','data_quality',20,
                          'synthetic-v1','synthetic-v1','synthetic-v1','synthetic-v1',$5,$6)
                   RETURNING id,task_uuid""",
                tenant_id, product_id, profile_id, dataset_id, f"synthetic-{suffix}", user_id,
            )
            other_tenant_id = await conn.fetchval(
                "INSERT INTO furniscope.tenants(tenant_code,name,status) VALUES($1,'其他合成租户','active') RETURNING id",
                f"SYN_OTHER_{suffix.upper()}",
            )
            other_tenant_user_id = await conn.fetchval(
                """INSERT INTO furniscope.users(tenant_id,email,password_hash,name,role_code,status)
                   VALUES($1,$2,'synthetic-fixture-not-a-login-secret','其他租户用户','user','active') RETURNING id""",
                other_tenant_id, f"synthetic-other-{suffix}@example.invalid",
            )
        return {"tenant_id": tenant_id, "user_id": user_id, "other_tenant_user_id": other_tenant_user_id, "product_id": product_id, "dataset_id": dataset_id, "task_id": task["id"], "task_uuid": str(task["task_uuid"])}
