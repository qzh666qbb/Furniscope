"""Persistence for the tenant-scoped AI employee runtime."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..services.agent_planner import skill_package_rows, tool_catalog


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class AgentRepository:
    async def ensure_catalog(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
    ) -> dict[str, Any]:
        profile = (await session.execute(text("""
            INSERT INTO ai_employee_profiles(tenant_id,user_id)
            VALUES(:tenant,:user)
            ON CONFLICT(tenant_id,user_id) DO UPDATE
              SET updated_at=ai_employee_profiles.updated_at
            RETURNING id,profile_uuid::text,display_name,role_title,autonomy_level,
                      status,enabled_skills,default_budget
        """), {"tenant": tenant_id, "user": user_id})).mappings().one()
        for item in tool_catalog():
            await session.execute(text("""
                INSERT INTO agent_tool_definitions(
                  tenant_id,capability_id,version,display_name,description,provider,
                  effect_level,permission_code,input_schema,output_schema,verifier_config
                )
                VALUES(
                  :tenant,:capability_id,:version,:display_name,:description,:provider,
                  :effect_level,:permission_code,'{}'::jsonb,'{}'::jsonb,
                  '{"mode":"deterministic"}'::jsonb
                )
                ON CONFLICT(tenant_id,capability_id,version) DO UPDATE SET
                  display_name=EXCLUDED.display_name,
                  description=EXCLUDED.description,
                  provider=EXCLUDED.provider,
                  effect_level=EXCLUDED.effect_level,
                  permission_code=EXCLUDED.permission_code,
                  status='active'
            """), {"tenant": tenant_id, **item})
        for item in skill_package_rows():
            await session.execute(text("""
                INSERT INTO agent_skill_packages(
                  tenant_id,skill_id,version,display_name,description,
                  objective_patterns,plan_template,content_sha256
                )
                VALUES(
                  :tenant,:skill_id,:version,:display_name,:description,
                  CAST(:patterns AS jsonb),CAST(:template AS jsonb),:sha
                )
                ON CONFLICT(tenant_id,skill_id,version) DO UPDATE SET
                  display_name=EXCLUDED.display_name,
                  description=EXCLUDED.description,
                  objective_patterns=EXCLUDED.objective_patterns,
                  plan_template=EXCLUDED.plan_template,
                  content_sha256=EXCLUDED.content_sha256,
                  status='active'
            """), {
                "tenant": tenant_id,
                "skill_id": item["skill_id"],
                "version": item["version"],
                "display_name": item["display_name"],
                "description": item["description"],
                "patterns": _json(item["objective_patterns"]),
                "template": _json(item["plan_template"]),
                "sha": item["content_sha256"],
            })
        return dict(profile)

    async def resolve_resources(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        objective: str,
        constraints: dict[str, Any],
        skill_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
        resolved = dict(constraints)
        resources: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        product_id = resolved.get("product_id")
        product = None
        if product_id:
            product = (await session.execute(text("""
                SELECT p.id,p.sku,p.name,p.category_code,p.current_profile_version_id,
                       pv.status AS profile_status
                  FROM products p
                  LEFT JOIN product_profile_versions pv
                    ON pv.id=p.current_profile_version_id AND pv.tenant_id=p.tenant_id
                 WHERE p.tenant_id=:tenant AND p.id=:product
                   AND p.deleted_at IS NULL
            """), {"tenant": tenant_id, "product": product_id})).mappings().one_or_none()
        elif skill_id == "market_entry_assessment":
            product = (await session.execute(text("""
                SELECT p.id,p.sku,p.name,p.category_code,p.current_profile_version_id,
                       pv.status AS profile_status
                  FROM products p
                  LEFT JOIN product_profile_versions pv
                    ON pv.id=p.current_profile_version_id AND pv.tenant_id=p.tenant_id
                 WHERE p.tenant_id=:tenant AND p.deleted_at IS NULL
                   AND (
                     :objective ILIKE '%%' || p.sku || '%%'
                     OR :objective ILIKE '%%' || p.name || '%%'
                   )
                 ORDER BY p.updated_at DESC,p.id DESC LIMIT 1
            """), {"tenant": tenant_id, "objective": objective})).mappings().one_or_none()
            if product is None:
                product = (await session.execute(text("""
                    SELECT p.id,p.sku,p.name,p.category_code,p.current_profile_version_id,
                           pv.status AS profile_status
                      FROM products p
                      JOIN product_profile_versions pv
                        ON pv.id=p.current_profile_version_id AND pv.tenant_id=p.tenant_id
                       AND pv.status='confirmed'
                     WHERE p.tenant_id=:tenant AND p.deleted_at IS NULL
                     ORDER BY p.updated_at DESC,p.id DESC LIMIT 1
                """), {"tenant": tenant_id})).mappings().one_or_none()
        if product:
            resolved["product_id"] = int(product["id"])
            resolved["product_profile_version_id"] = product["current_profile_version_id"]
            resolved["category_code"] = product["category_code"]
            resources.append({
                "type": "product",
                "id": int(product["id"]),
                "label": f"{product['sku']} · {product['name']}",
                "status": product["profile_status"] or "missing_profile",
                "href": f"product-detail?id={product['id']}&from=employee",
            })
            if skill_id == "market_entry_assessment" and product["profile_status"] != "confirmed":
                issues.append({
                    "code": "PRODUCT_PROFILE_NOT_CONFIRMED",
                    "message": "产品画像尚未确认，市场评估会在此处等待处理。",
                    "href": f"product-detail?id={product['id']}&from=employee",
                    "blocking": True,
                })
        elif skill_id == "market_entry_assessment":
            issues.append({
                "code": "PRODUCT_REQUIRED",
                "message": "没有找到可用于评估的产品，请先在产品中心确认产品画像。",
                "href": "products",
                "blocking": True,
            })

        if "market" not in resolved:
            market_map = {
                "美国": "US", "US": "US", "德国": "DE", "DE": "DE",
                "英国": "GB", "UK": "GB", "日本": "JP", "JP": "JP",
            }
            resolved["market"] = next(
                (code for label, code in market_map.items() if label.lower() in objective.lower()),
                "US",
            )
        if skill_id == "market_entry_assessment" and product:
            dataset_id = resolved.get("dataset_id")
            dataset = (await session.execute(text("""
                SELECT id,name,version_no,platform,market_country,category_code,status,
                       quality_score,data_start_date,data_end_date
                  FROM market_datasets
                 WHERE tenant_id=:tenant AND deleted_at IS NULL
                   AND status='ready'
                   AND (CAST(:dataset_id AS bigint) IS NULL OR id=:dataset_id)
                   AND market_country=:market
                   AND category_code=:category
                 ORDER BY updated_at DESC,id DESC LIMIT 1
            """), {
                "tenant": tenant_id,
                "dataset_id": dataset_id,
                "market": resolved["market"],
                "category": product["category_code"],
            })).mappings().one_or_none()
            if dataset:
                resolved["dataset_id"] = int(dataset["id"])
                resolved["target_platform"] = dataset["platform"]
                resources.append({
                    "type": "market_dataset",
                    "id": int(dataset["id"]),
                    "label": f"{dataset['name']} · V{dataset['version_no']}",
                    "status": dataset["status"],
                    "href": f"dataset-detail?id={dataset['id']}&from=employee",
                })
            else:
                issues.append({
                    "code": "MARKET_DATASET_REQUIRED",
                    "message": f"没有找到 {resolved['market']} 市场且品类匹配的已就绪数据集。",
                    "href": "market-data",
                    "blocking": True,
                })

        if skill_id in {"weekly_sales_review", "data_readiness_check"}:
            version = (await session.execute(text("""
                SELECT id,version_uuid::text,filename,canonical_sha256,status,
                       rules,quality AS quality_report,confirmed_at
                  FROM forecast_data_versions
                 WHERE tenant_id=:tenant AND status='confirmed'
                   AND rules->>'kind'='sales'
                 ORDER BY confirmed_at DESC,id DESC LIMIT 1
            """), {"tenant": tenant_id})).mappings().one_or_none()
            if version:
                resolved["data_version_uuid"] = version["version_uuid"]
                resources.append({
                    "type": "forecast_data_version",
                    "id": version["version_uuid"],
                    "label": version["filename"],
                    "status": version["status"],
                    "sha256": version["canonical_sha256"],
                    "href": "forecast?tab=training",
                })
            else:
                issues.append({
                    "code": "CONFIRMED_SALES_DATA_REQUIRED",
                    "message": "没有已确认的销量数据版本，请先完成标准数据建模。",
                    "href": "forecast?tab=training",
                    "blocking": True,
                })
        return resolved, resources, issues

    async def find_goal_by_idempotency(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            SELECT goal_uuid::text,request_hash
              FROM agent_goals
             WHERE tenant_id=:tenant AND idempotency_key=:key
        """), {"tenant": tenant_id, "key": idempotency_key})).mappings().one_or_none()
        return dict(row) if row else None

    async def create_goal(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        profile_id: int,
        idempotency_key: str,
        request_hash: str,
        payload: dict[str, Any],
    ) -> str:
        goal_uuid = (await session.execute(text("""
            INSERT INTO agent_goals(
              tenant_id,created_by,profile_id,objective,expected_deliverables,
              constraints,acceptance_criteria,autonomy_envelope,deadline,priority,
              trigger_type,source_mode,return_href,selected_skill_id,request_hash,
              idempotency_key
            )
            VALUES(
              :tenant,:user,:profile,:objective,CAST(:deliverables AS jsonb),
              CAST(:constraints AS jsonb),CAST(:criteria AS jsonb),CAST(:envelope AS jsonb),
              :deadline,:priority,:trigger_type,:source_mode,:return_href,
              :skill,:request_hash,:idempotency_key
            )
            RETURNING goal_uuid::text
        """), {
            "tenant": tenant_id,
            "user": user_id,
            "profile": profile_id,
            "objective": payload["objective"],
            "deliverables": _json(payload["expected_deliverables"]),
            "constraints": _json(payload["constraints"]),
            "criteria": _json(payload["acceptance_criteria"]),
            "envelope": _json(payload["autonomy_envelope"]),
            "deadline": payload.get("deadline"),
            "priority": payload["priority"],
            "trigger_type": payload["trigger_type"],
            "source_mode": payload["source_mode"],
            "return_href": payload.get("return_href"),
            "skill": payload["selected_skill_id"].split("@", 1)[0],
            "request_hash": request_hash,
            "idempotency_key": idempotency_key,
        })).scalar_one()
        return str(goal_uuid)

    async def goal_for_update(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        goal_uuid: str,
    ) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            SELECT * FROM agent_goals
             WHERE tenant_id=:tenant AND goal_uuid=CAST(:goal AS uuid)
             FOR UPDATE
        """), {"tenant": tenant_id, "goal": goal_uuid})).mappings().one_or_none()
        return dict(row) if row else None

    async def create_plan(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        goal: dict[str, Any],
        compiled: dict[str, Any],
    ) -> str:
        version = int(await session.scalar(text("""
            SELECT COALESCE(max(version),0)+1 FROM agent_plans
             WHERE tenant_id=:tenant AND goal_id=:goal_id
        """), {"tenant": tenant_id, "goal_id": goal["id"]}) or 1)
        plan = (await session.execute(text("""
            INSERT INTO agent_plans(
              tenant_id,goal_id,version,skill_id,skill_version,plan_sha256,
              risk_summary,created_by
            )
            VALUES(
              :tenant,:goal_id,:version,:skill_id,:skill_version,:sha,
              CAST(:risk AS jsonb),:user
            )
            RETURNING id,plan_uuid::text
        """), {
            "tenant": tenant_id,
            "goal_id": goal["id"],
            "version": version,
            "skill_id": compiled["skill_id"],
            "skill_version": compiled["skill_version"],
            "sha": compiled["plan_sha256"],
            "risk": _json(compiled["risk_summary"]),
            "user": user_id,
        })).mappings().one()
        step_ids: dict[int, str] = {}
        for step in compiled["steps"]:
            depends_on = [step_ids[item] for item in step["depends_on_ordinals"]]
            step_uuid = (await session.execute(text("""
                INSERT INTO agent_plan_steps(
                  tenant_id,plan_id,ordinal,title,capability_id,capability_version,
                  provider,effect_level,depends_on,input_refs,output_schema,
                  verifier_config,requires_approval,status
                )
                VALUES(
                  :tenant,:plan_id,:ordinal,:title,:capability_id,:capability_version,
                  :provider,:effect_level,CAST(:depends_on AS jsonb),CAST(:input_refs AS jsonb),
                  CAST(:output_schema AS jsonb),CAST(:verifier AS jsonb),:requires_approval,
                  CASE WHEN :ordinal=1 THEN 'ready' ELSE 'pending' END
                )
                RETURNING step_uuid::text
            """), {
                "tenant": tenant_id,
                "plan_id": plan["id"],
                **step,
                "depends_on": _json(depends_on),
                "input_refs": _json(step["input_refs"]),
                "output_schema": _json(step["output_schema"]),
                "verifier": _json(step["verifier_config"]),
            })).scalar_one()
            step_ids[step["ordinal"]] = str(step_uuid)
        await session.execute(text("""
            UPDATE agent_goals
               SET status='plan_ready',current_plan_id=:plan_id
             WHERE id=:goal_id AND tenant_id=:tenant
        """), {"tenant": tenant_id, "goal_id": goal["id"], "plan_id": plan["id"]})
        return str(plan["plan_uuid"])

    async def create_run(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        goal: dict[str, Any],
    ) -> dict[str, Any]:
        run = (await session.execute(text("""
            INSERT INTO agent_runs(
              tenant_id,goal_id,plan_id,status,budget,created_by
            )
            VALUES(
              :tenant,:goal_id,:plan_id,'queued',CAST(:budget AS jsonb),:user
            )
            RETURNING id,run_uuid::text
        """), {
            "tenant": tenant_id,
            "goal_id": goal["id"],
            "plan_id": goal["current_plan_id"],
            "budget": _json(goal["autonomy_envelope"]),
            "user": user_id,
        })).mappings().one()
        await session.execute(text("""
            UPDATE agent_goals
               SET status='queued',current_run_id=:run_id
             WHERE id=:goal_id AND tenant_id=:tenant
        """), {"tenant": tenant_id, "goal_id": goal["id"], "run_id": run["id"]})
        await session.execute(text("""
            UPDATE agent_plans SET status='active'
             WHERE id=:plan_id AND tenant_id=:tenant
        """), {"tenant": tenant_id, "plan_id": goal["current_plan_id"]})
        return {"id": int(run["id"]), "run_uuid": str(run["run_uuid"])}

    async def append_event(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        goal_id: int,
        run_id: int | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> int:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:goal_id)"),
            {"goal_id": goal_id},
        )
        sequence = int(await session.scalar(text("""
            SELECT COALESCE(max(sequence_no),0)+1
              FROM agent_events
             WHERE tenant_id=:tenant AND goal_id=:goal_id
        """), {"tenant": tenant_id, "goal_id": goal_id}) or 1)
        await session.execute(text("""
            INSERT INTO agent_events(
              tenant_id,goal_id,run_id,sequence_no,event_type,payload
            )
            VALUES(
              :tenant,:goal_id,:run_id,:sequence,:event_type,CAST(:payload AS jsonb)
            )
        """), {
            "tenant": tenant_id,
            "goal_id": goal_id,
            "run_id": run_id,
            "sequence": sequence,
            "event_type": event_type,
            "payload": _json(payload),
        })
        return sequence

    async def list_goals(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT g.goal_uuid::text,g.objective,g.selected_skill_id,
                   s.display_name AS skill_name,g.status,g.priority,g.return_href,
                   COALESCE(r.progress_percent,0) AS progress_percent,
                   r.run_uuid::text AS current_run_uuid,
                   g.created_at,g.updated_at,g.completed_at,
                   (SELECT count(*) FROM agent_approvals a
                     WHERE a.goal_id=g.id AND a.tenant_id=g.tenant_id
                       AND a.status='pending')::int AS pending_approvals,
                   (SELECT count(*) FROM agent_artifacts a
                     WHERE a.goal_id=g.id AND a.tenant_id=g.tenant_id)::int
                     AS artifact_count,
                   (SELECT ps.title FROM agent_plan_steps ps
                     WHERE ps.plan_id=g.current_plan_id AND ps.tenant_id=g.tenant_id
                       AND ps.status IN ('ready','running','waiting_human','pending')
                     ORDER BY ps.ordinal LIMIT 1) AS next_step_title
              FROM agent_goals g
              LEFT JOIN agent_runs r
                ON r.id=g.current_run_id AND r.tenant_id=g.tenant_id
              LEFT JOIN agent_skill_packages s
                ON s.tenant_id=g.tenant_id AND s.skill_id=g.selected_skill_id
               AND s.version=1
             WHERE g.tenant_id=:tenant AND g.created_by=:user
               AND (CAST(:status AS text) IS NULL OR g.status=:status)
             ORDER BY
               CASE g.status
                 WHEN 'waiting_human' THEN 0 WHEN 'running' THEN 1
                 WHEN 'queued' THEN 2 WHEN 'plan_ready' THEN 3 ELSE 4
               END,
               g.updated_at DESC,g.id DESC
             LIMIT :limit
        """), {
            "tenant": tenant_id,
            "user": user_id,
            "status": status,
            "limit": limit,
        })).mappings().all()
        return [dict(row) for row in rows]

    async def list_approvals(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        status: str = "pending",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT a.approval_uuid::text,g.goal_uuid::text,a.approval_type,
                   a.title,a.question,a.reason,a.impact,a.options,
                   a.recommended_option,a.status,a.response,a.expires_at,
                   a.responded_at,a.created_at,g.objective
              FROM agent_approvals a
              JOIN agent_goals g
                ON g.id=a.goal_id AND g.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant AND g.created_by=:user
               AND (CAST(:status AS text) IS NULL OR a.status=:status)
             ORDER BY a.created_at DESC,a.id DESC
             LIMIT :limit
        """), {
            "tenant": tenant_id,
            "user": user_id,
            "status": status,
            "limit": limit,
        })).mappings().all()
        return [dict(row) for row in rows]

    async def list_artifacts(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT a.artifact_uuid::text,g.goal_uuid::text,a.artifact_type,
                   a.title,a.summary,a.resource_type,a.resource_uuid::text,
                   a.resource_version,a.content,a.sha256,a.open_href,a.created_at,
                   g.objective
              FROM agent_artifacts a
              JOIN agent_goals g
                ON g.id=a.goal_id AND g.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant AND g.created_by=:user
             ORDER BY a.created_at DESC,a.id DESC
             LIMIT :limit
        """), {"tenant": tenant_id, "user": user_id, "limit": limit})).mappings().all()
        return [dict(row) for row in rows]

    async def list_events(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        run_uuid: str | None = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT e.event_uuid::text,e.sequence_no,e.event_type,e.payload,e.created_at,
                   g.goal_uuid::text,r.run_uuid::text
              FROM agent_events e
              JOIN agent_goals g
                ON g.id=e.goal_id AND g.tenant_id=e.tenant_id
              LEFT JOIN agent_runs r
                ON r.id=e.run_id AND r.tenant_id=e.tenant_id
             WHERE e.tenant_id=:tenant AND g.created_by=:user
               AND (CAST(:run_uuid AS text) IS NULL
                    OR r.run_uuid=CAST(:run_uuid AS uuid))
               AND e.sequence_no>:after
             ORDER BY e.created_at DESC,e.id DESC
             LIMIT :limit
        """), {
            "tenant": tenant_id,
            "user": user_id,
            "run_uuid": run_uuid,
            "after": after_sequence,
            "limit": limit,
        })).mappings().all()
        return [dict(row) for row in reversed(rows)]

    async def goal_detail(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        goal_uuid: str,
    ) -> dict[str, Any] | None:
        goal = (await session.execute(text("""
            SELECT g.*,g.goal_uuid::text AS goal_uuid_text,
                   s.display_name AS skill_name,
                   COALESCE(r.progress_percent,0) AS progress_percent,
                   r.run_uuid::text AS current_run_uuid,
                   (SELECT count(*) FROM agent_approvals a
                     WHERE a.goal_id=g.id AND a.tenant_id=g.tenant_id
                       AND a.status='pending')::int AS pending_approvals,
                   (SELECT count(*) FROM agent_artifacts a
                     WHERE a.goal_id=g.id AND a.tenant_id=g.tenant_id)::int
                     AS artifact_count,
                   (SELECT ps.title FROM agent_plan_steps ps
                     WHERE ps.plan_id=g.current_plan_id AND ps.tenant_id=g.tenant_id
                       AND ps.status IN ('ready','running','waiting_human','pending')
                     ORDER BY ps.ordinal LIMIT 1) AS next_step_title
              FROM agent_goals g
              LEFT JOIN agent_runs r
                ON r.id=g.current_run_id AND r.tenant_id=g.tenant_id
              LEFT JOIN agent_skill_packages s
                ON s.tenant_id=g.tenant_id AND s.skill_id=g.selected_skill_id
               AND s.version=1
             WHERE g.tenant_id=:tenant AND g.created_by=:user
               AND g.goal_uuid=CAST(:goal AS uuid)
        """), {
            "tenant": tenant_id,
            "user": user_id,
            "goal": goal_uuid,
        })).mappings().one_or_none()
        if goal is None:
            return None
        data = dict(goal)
        data["goal_uuid"] = data.pop("goal_uuid_text")
        plan = None
        if data["current_plan_id"]:
            plan_row = (await session.execute(text("""
                SELECT plan_uuid::text,version,skill_id,skill_version,plan_sha256,
                       risk_summary,status
                  FROM agent_plans
                 WHERE tenant_id=:tenant AND id=:plan_id
            """), {
                "tenant": tenant_id,
                "plan_id": data["current_plan_id"],
            })).mappings().one()
            steps = (await session.execute(text("""
                SELECT step_uuid::text,ordinal,title,capability_id,capability_version,
                       provider,effect_level,depends_on,input_refs,verifier_config,
                       requires_approval,status,started_at,completed_at
                  FROM agent_plan_steps
                 WHERE tenant_id=:tenant AND plan_id=:plan_id
                 ORDER BY ordinal
            """), {
                "tenant": tenant_id,
                "plan_id": data["current_plan_id"],
            })).mappings().all()
            plan = {**dict(plan_row), "steps": [dict(row) for row in steps]}
        run = None
        if data["current_run_id"]:
            run_row = (await session.execute(text("""
                SELECT run_uuid::text,status,progress_percent,tool_call_count,replan_count,
                       failure_code,failure_message,created_at,started_at,completed_at
                  FROM agent_runs
                 WHERE tenant_id=:tenant AND id=:run_id
            """), {
                "tenant": tenant_id,
                "run_id": data["current_run_id"],
            })).mappings().one()
            run = dict(run_row)
        data["plan"] = plan
        data["run"] = run
        data["artifacts"] = await self.list_artifacts(
            session, tenant_id=tenant_id, user_id=user_id, limit=100,
        )
        data["artifacts"] = [
            item for item in data["artifacts"] if item["goal_uuid"] == goal_uuid
        ]
        data["approvals"] = await self.list_approvals(
            session, tenant_id=tenant_id, user_id=user_id, status=None, limit=100,
        )
        data["approvals"] = [
            item for item in data["approvals"] if item["goal_uuid"] == goal_uuid
        ]
        data["timeline"] = await self.list_events(
            session, tenant_id=tenant_id, user_id=user_id,
            run_uuid=data["current_run_uuid"], limit=100,
        )
        messages = (await session.execute(text("""
            SELECT message_uuid::text,role,content,created_at
              FROM agent_goal_messages
             WHERE tenant_id=:tenant AND goal_id=:goal_id
             ORDER BY created_at,id
        """), {"tenant": tenant_id, "goal_id": data["id"]})).mappings().all()
        data["messages"] = [dict(row) for row in messages]
        return data

    async def run_context(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        run_uuid: str,
        for_update: bool = False,
    ) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            SELECT r.*,r.run_uuid::text AS run_uuid_text,
                   g.goal_uuid::text,g.objective,g.constraints,g.acceptance_criteria,
                   g.selected_skill_id,g.created_by AS goal_created_by
              FROM agent_runs r
              JOIN agent_goals g
                ON g.id=r.goal_id AND g.tenant_id=r.tenant_id
             WHERE r.tenant_id=:tenant AND r.run_uuid=CAST(:run AS uuid)
        """ + (" FOR UPDATE OF r,g" if for_update else "")), {
            "tenant": tenant_id,
            "run": run_uuid,
        })).mappings().one_or_none()
        if row is None:
            return None
        result = dict(row)
        result["run_uuid"] = result.pop("run_uuid_text")
        return result

    async def plan_steps(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        plan_id: int,
    ) -> list[dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT * FROM agent_plan_steps
             WHERE tenant_id=:tenant AND plan_id=:plan
             ORDER BY ordinal
        """), {"tenant": tenant_id, "plan": plan_id})).mappings().all()
        return [dict(row) for row in rows]

    async def has_effective_permission(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        permission_code: str,
    ) -> bool:
        allowed = await session.scalar(text("""
            SELECT EXISTS(
              SELECT 1
                FROM users u
                JOIN user_role_assignments a
                  ON a.tenant_id=u.tenant_id AND a.user_id=u.id
                 AND (a.expires_at IS NULL OR a.expires_at>CURRENT_TIMESTAMP)
                JOIN role_permissions p
                  ON p.tenant_id=a.tenant_id AND p.role_id=a.role_id
               WHERE u.tenant_id=:tenant AND u.id=:user
                 AND u.role_code='user' AND u.status='active'
                 AND p.permission_code=:permission
            )
        """), {
            "tenant": tenant_id,
            "user": user_id,
            "permission": permission_code,
        })
        return bool(allowed)

    async def reserve_tool_call(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        run_id: int,
        max_tool_calls: int,
    ) -> int | None:
        count = await session.scalar(text("""
            UPDATE agent_runs
               SET tool_call_count=tool_call_count+1
             WHERE tenant_id=:tenant AND id=:run
               AND tool_call_count<:limit
            RETURNING tool_call_count
        """), {
            "tenant": tenant_id,
            "run": run_id,
            "limit": max_tool_calls,
        })
        return int(count) if count is not None else None

    async def set_run_state(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        run_id: int,
        goal_id: int,
        status: str,
        progress_percent: float,
        current_step_id: int | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> None:
        terminal = status in {"succeeded", "partial_succeeded", "failed", "cancelled"}
        await session.execute(text("""
            UPDATE agent_runs
               SET status=CAST(:status AS varchar),progress_percent=:progress,
                   current_step_id=:step_id,
                   started_at=CASE
                     WHEN CAST(:status AS text) IN ('running','verifying','delivering')
                     THEN COALESCE(started_at,CURRENT_TIMESTAMP) ELSE started_at END,
                   completed_at=CASE WHEN :terminal THEN CURRENT_TIMESTAMP ELSE NULL END,
                   failure_code=:failure_code,failure_message=:failure_message
             WHERE tenant_id=:tenant AND id=:run_id
        """), {
            "tenant": tenant_id,
            "run_id": run_id,
            "status": status,
            "progress": progress_percent,
            "step_id": current_step_id,
            "terminal": terminal,
            "failure_code": failure_code,
            "failure_message": failure_message,
        })
        await session.execute(text("""
            UPDATE agent_goals
               SET status=CAST(:status AS varchar),
                   completed_at=CASE WHEN :terminal THEN CURRENT_TIMESTAMP ELSE NULL END
             WHERE tenant_id=:tenant AND id=:goal_id
        """), {
            "tenant": tenant_id,
            "goal_id": goal_id,
            "status": status,
            "terminal": terminal,
        })

    async def start_step(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        run_id: int,
        step: dict[str, Any],
        input_sha256: str,
    ) -> int:
        await session.execute(text("""
            UPDATE agent_plan_steps
               SET status='running',started_at=COALESCE(started_at,CURRENT_TIMESTAMP)
             WHERE tenant_id=:tenant AND id=:step
        """), {"tenant": tenant_id, "step": step["id"]})
        row = (await session.execute(text("""
            INSERT INTO agent_step_runs(
              tenant_id,run_id,step_id,attempt_no,status,input_sha256
            )
            VALUES(
              :tenant,:run_id,:step_id,
              (SELECT COALESCE(max(attempt_no),0)+1 FROM agent_step_runs
                WHERE tenant_id=:tenant AND run_id=:run_id AND step_id=:step_id),
              'running',:input_sha256
            )
            RETURNING id
        """), {
            "tenant": tenant_id,
            "run_id": run_id,
            "step_id": step["id"],
            "input_sha256": input_sha256,
        })).scalar_one()
        return int(row)

    async def complete_step(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        run_id: int,
        step: dict[str, Any],
        step_run_id: int,
        output: dict[str, Any],
        output_sha256: str,
        duration_ms: int,
    ) -> None:
        await session.execute(text("""
            UPDATE agent_step_runs
               SET status='succeeded',output_data=CAST(:output AS jsonb),
                   output_sha256=:sha,completed_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant AND id=:step_run
        """), {
            "tenant": tenant_id,
            "step_run": step_run_id,
            "output": _json(output),
            "sha": output_sha256,
        })
        await session.execute(text("""
            UPDATE agent_plan_steps
               SET status='succeeded',completed_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant AND id=:step
        """), {"tenant": tenant_id, "step": step["id"]})
        await session.execute(text("""
            INSERT INTO agent_tool_invocations(
              tenant_id,run_id,step_run_id,capability_id,capability_version,
              policy_decision,parameter_summary,result_summary,receipt_sha256,duration_ms
            )
            VALUES(
              :tenant,:run_id,:step_run,:capability,:version,'allow',
              CAST(:params AS jsonb),CAST(:result AS jsonb),:sha,:duration
            )
        """), {
            "tenant": tenant_id,
            "run_id": run_id,
            "step_run": step_run_id,
            "capability": step["capability_id"],
            "version": step["capability_version"],
            "params": _json(step["input_refs"]),
            "result": _json(output),
            "sha": output_sha256,
            "duration": duration_ms,
        })
        await session.execute(text("""
            UPDATE agent_plan_steps
               SET status='ready'
             WHERE tenant_id=:tenant AND plan_id=:plan
               AND status='pending'
               AND ordinal=:next_ordinal
        """), {
            "tenant": tenant_id,
            "plan": step["plan_id"],
            "next_ordinal": step["ordinal"] + 1,
        })

    async def step_outputs(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        run_id: int,
    ) -> dict[str, dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT ps.capability_id,sr.output_data
              FROM agent_step_runs sr
              JOIN agent_plan_steps ps
                ON ps.id=sr.step_id AND ps.tenant_id=sr.tenant_id
             WHERE sr.tenant_id=:tenant AND sr.run_id=:run
               AND sr.status='succeeded'
             ORDER BY ps.ordinal,sr.attempt_no DESC
        """), {"tenant": tenant_id, "run": run_id})).mappings().all()
        return {row["capability_id"]: dict(row["output_data"] or {}) for row in rows}

    async def create_approval(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        goal_id: int,
        run_id: int,
        step_id: int,
        approval_type: str,
        title: str,
        question: str,
        reason: str,
        href: str,
        impact: dict[str, Any] | None = None,
        options: list[dict[str, Any]] | None = None,
        recommended_option: str = "continue",
    ) -> str:
        impact_payload = {"effect": "execution_blocked", "href": href}
        impact_payload.update(impact or {})
        option_payload = options or [
            {"value": "continue", "label": "已处理，继续执行"},
            {"value": "cancel", "label": "取消目标"},
        ]
        approval_uuid = (await session.execute(text("""
            INSERT INTO agent_approvals(
              tenant_id,goal_id,run_id,step_id,approval_type,title,question,
              reason,impact,options,recommended_option
            )
            VALUES(
              :tenant,:goal,:run,:step,:type,:title,:question,:reason,
              CAST(:impact AS jsonb),CAST(:options AS jsonb),:recommended
            )
            RETURNING approval_uuid::text
        """), {
            "tenant": tenant_id,
            "goal": goal_id,
            "run": run_id,
            "step": step_id,
            "type": approval_type,
            "title": title,
            "question": question,
            "reason": reason,
            "impact": _json(impact_payload),
            "options": _json(option_payload),
            "recommended": recommended_option,
        })).scalar_one()
        await session.execute(text("""
            UPDATE agent_plan_steps SET status='waiting_human'
             WHERE tenant_id=:tenant AND id=:step
        """), {"tenant": tenant_id, "step": step_id})
        return str(approval_uuid)

    async def create_artifact(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        goal_id: int,
        run_id: int,
        step_id: int,
        artifact: dict[str, Any],
    ) -> str:
        artifact_uuid = (await session.execute(text("""
            INSERT INTO agent_artifacts(
              tenant_id,goal_id,run_id,step_id,artifact_type,title,summary,
              resource_type,resource_uuid,resource_version,content,sha256,open_href
            )
            VALUES(
              :tenant,:goal,:run,:step,:artifact_type,:title,:summary,
              :resource_type,CAST(:resource_uuid AS uuid),:resource_version,
              CAST(:content AS jsonb),:sha256,:open_href
            )
            RETURNING artifact_uuid::text
        """), {
            "tenant": tenant_id,
            "goal": goal_id,
            "run": run_id,
            "step": step_id,
            **artifact,
            "content": _json(artifact["content"]),
        })).scalar_one()
        return str(artifact_uuid)

    async def append_message(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        goal_id: int,
        role: str,
        content: str,
        created_by: int | None,
    ) -> dict[str, Any]:
        row = (await session.execute(text("""
            INSERT INTO agent_goal_messages(
              tenant_id,goal_id,role,content,created_by
            )
            VALUES(:tenant,:goal,:role,:content,:created_by)
            RETURNING message_uuid::text,role,content,created_at
        """), {
            "tenant": tenant_id,
            "goal": goal_id,
            "role": role,
            "content": content,
            "created_by": created_by,
        })).mappings().one()
        return dict(row)

    async def append_goal_instruction(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        goal_id: int,
        content: str,
    ) -> None:
        await session.execute(text("""
            UPDATE agent_goals
               SET constraints=jsonb_set(
                 constraints,
                 '{supplemental_instructions}',
                 COALESCE(constraints->'supplemental_instructions','[]'::jsonb)
                   || jsonb_build_array(:content::text),
                 true
               )
             WHERE tenant_id=:tenant AND id=:goal
        """), {
            "tenant": tenant_id,
            "goal": goal_id,
            "content": content,
        })

    async def request_run_control(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        run_uuid: str,
        action: str,
        reason: str | None,
    ) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            SELECT r.id,r.goal_id,r.status,g.goal_uuid::text
              FROM agent_runs r
              JOIN agent_goals g
                ON g.id=r.goal_id AND g.tenant_id=r.tenant_id
             WHERE r.tenant_id=:tenant AND r.run_uuid=CAST(:run AS uuid)
               AND g.created_by=:user
             FOR UPDATE OF r,g
        """), {
            "tenant": tenant_id,
            "user": user_id,
            "run": run_uuid,
        })).mappings().one_or_none()
        if row is None:
            return None
        if action == "pause":
            await session.execute(text("""
                UPDATE agent_runs SET pause_requested=true
                 WHERE tenant_id=:tenant AND id=:run_id
                   AND status IN ('queued','running','verifying','delivering')
            """), {"tenant": tenant_id, "run_id": row["id"]})
        elif action == "resume":
            await session.execute(text("""
                UPDATE agent_runs
                   SET pause_requested=false,status='queued',failure_code=NULL,
                       failure_message=NULL,completed_at=NULL
                 WHERE tenant_id=:tenant AND id=:run_id AND status='paused'
            """), {"tenant": tenant_id, "run_id": row["id"]})
            await session.execute(text("""
                UPDATE agent_goals SET status='queued',completed_at=NULL
                 WHERE tenant_id=:tenant AND id=:goal_id AND status='paused'
            """), {"tenant": tenant_id, "goal_id": row["goal_id"]})
        elif action == "cancel":
            await session.execute(text("""
                UPDATE agent_runs SET cancel_requested=true
                 WHERE tenant_id=:tenant AND id=:run_id
                   AND status NOT IN ('succeeded','partial_succeeded','failed','cancelled')
            """), {"tenant": tenant_id, "run_id": row["id"]})
        else:
            raise ValueError(f"unsupported agent run action: {action}")
        await self.append_event(
            session,
            tenant_id=tenant_id,
            goal_id=int(row["goal_id"]),
            run_id=int(row["id"]),
            event_type=f"run.{action}_requested",
            payload={"reason": reason, "requested_by": user_id},
        )
        return dict(row)

    async def approval_for_update(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        approval_uuid: str,
    ) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            SELECT a.*,a.approval_uuid::text AS approval_uuid_text,
                   r.run_uuid::text,g.goal_uuid::text
              FROM agent_approvals a
              JOIN agent_goals g
                ON g.id=a.goal_id AND g.tenant_id=a.tenant_id
              JOIN agent_runs r
                ON r.id=a.run_id AND r.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant
               AND a.approval_uuid=CAST(:approval AS uuid)
               AND g.created_by=:user
             FOR UPDATE OF a,r,g
        """), {
            "tenant": tenant_id,
            "user": user_id,
            "approval": approval_uuid,
        })).mappings().one_or_none()
        if row is None:
            return None
        result = dict(row)
        result["approval_uuid"] = result.pop("approval_uuid_text")
        return result

    async def respond_approval(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        approval: dict[str, Any],
        user_id: int,
        selected_option: str,
        user_input: Any,
    ) -> None:
        await session.execute(text("""
            UPDATE agent_approvals
               SET status=CASE WHEN :selected='cancel' THEN 'rejected' ELSE 'approved' END,
                   response=CAST(:response AS jsonb),responded_by=:user,
                   responded_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant AND id=:approval_id AND status='pending'
        """), {
            "tenant": tenant_id,
            "approval_id": approval["id"],
            "selected": selected_option,
            "response": _json({
                "selected_option": selected_option,
                "user_input": user_input,
            }),
            "user": user_id,
        })
        if selected_option == "cancel":
            await self.set_run_state(
                session,
                tenant_id=tenant_id,
                run_id=int(approval["run_id"]),
                goal_id=int(approval["goal_id"]),
                status="cancelled",
                progress_percent=0,
                current_step_id=approval.get("step_id"),
            )
            await session.execute(text("""
                UPDATE agent_plan_steps SET status='cancelled'
                 WHERE tenant_id=:tenant AND plan_id=(
                   SELECT plan_id FROM agent_runs
                    WHERE tenant_id=:tenant AND id=:run_id
                 ) AND status IN ('pending','ready','running','waiting_human')
            """), {"tenant": tenant_id, "run_id": approval["run_id"]})
        else:
            await session.execute(text("""
                UPDATE agent_plan_steps
                   SET status='ready',started_at=NULL
                 WHERE tenant_id=:tenant AND id=:step
                   AND status='waiting_human'
            """), {"tenant": tenant_id, "step": approval["step_id"]})
            await session.execute(text("""
                UPDATE agent_runs
                   SET status='queued',pause_requested=false,failure_code=NULL,
                       failure_message=NULL,completed_at=NULL
                 WHERE tenant_id=:tenant AND id=:run
                   AND status='waiting_human'
            """), {"tenant": tenant_id, "run": approval["run_id"]})
            await session.execute(text("""
                UPDATE agent_goals SET status='queued',completed_at=NULL
                 WHERE tenant_id=:tenant AND id=:goal
                   AND status='waiting_human'
            """), {"tenant": tenant_id, "goal": approval["goal_id"]})
        await self.append_event(
            session,
            tenant_id=tenant_id,
            goal_id=int(approval["goal_id"]),
            run_id=int(approval["run_id"]),
            event_type="approval.responded",
            payload={
                "approval_uuid": approval["approval_uuid"],
                "selected_option": selected_option,
                "responded_by": user_id,
            },
        )

    async def approved_step_response(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        run_id: int,
        step_id: int,
    ) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            SELECT approval_type,impact,response
              FROM agent_approvals
             WHERE tenant_id=:tenant AND run_id=:run AND step_id=:step
               AND status='approved'
             ORDER BY responded_at DESC,id DESC LIMIT 1
        """), {
            "tenant": tenant_id,
            "run": run_id,
            "step": step_id,
        })).mappings().one_or_none()
        return dict(row) if row else None

    async def fail_step(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        step_id: int,
        step_run_id: int,
        error_code: str,
        error_message: str,
    ) -> None:
        await session.execute(text("""
            UPDATE agent_step_runs
               SET status='failed',error_code=:code,error_message=:message,
                   completed_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant AND id=:step_run
        """), {
            "tenant": tenant_id,
            "step_run": step_run_id,
            "code": error_code,
            "message": error_message[:1000],
        })
        await session.execute(text("""
            UPDATE agent_plan_steps SET status='failed',completed_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant AND id=:step
        """), {"tenant": tenant_id, "step": step_id})

    async def wait_step(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        step_id: int,
        step_run_id: int,
    ) -> None:
        await session.execute(text("""
            UPDATE agent_step_runs
               SET status='cancelled',error_code='HUMAN_INPUT_REQUIRED',
                   error_message='Execution paused for a user decision',
                   completed_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant AND id=:step_run AND status='running'
        """), {
            "tenant": tenant_id,
            "step_run": step_run_id,
        })
        await session.execute(text("""
            UPDATE agent_plan_steps
               SET status='waiting_human',completed_at=NULL
             WHERE tenant_id=:tenant AND id=:step
        """), {"tenant": tenant_id, "step": step_id})

    async def pending_approval_for_step(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        run_id: int,
        step_id: int,
    ) -> dict[str, Any] | None:
        row = (await session.execute(text("""
            SELECT approval_uuid::text,approval_type,title,question,reason,
                   impact,options,recommended_option,status,created_at
              FROM agent_approvals
             WHERE tenant_id=:tenant AND run_id=:run AND step_id=:step
               AND status='pending'
             ORDER BY id DESC LIMIT 1
        """), {
            "tenant": tenant_id,
            "run": run_id,
            "step": step_id,
        })).mappings().one_or_none()
        return dict(row) if row else None

    async def queued_runs(
        self,
        session: AsyncSession,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = (await session.execute(text("""
            SELECT tenant_id,run_uuid::text
              FROM agent_runs
             WHERE status='queued'
             ORDER BY created_at,id
             LIMIT :limit
        """), {"limit": limit})).mappings().all()
        return [dict(row) for row in rows]
