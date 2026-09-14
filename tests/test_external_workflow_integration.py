from __future__ import annotations

import hashlib
import json
import os
from uuid import uuid4

import asyncpg
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.furniscope_agent.demo_support import demo_initial_state
from backend.furniscope_agent.external_toolbox import ExternalFurnitureToolbox
from backend.furniscope_agent.graph import FurniScopeAgentEngine, build_graph
from backend.furniscope_agent.repository import PostgresWorkflowRepository


TEST_DSN = os.getenv("FURNISCOPE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="FURNISCOPE_TEST_DATABASE_URL is not configured")


@pytest.mark.asyncio
async def test_authorized_external_workflow_persists_evidence_backed_report():
    pool = await asyncpg.create_pool(TEST_DSN, min_size=1, max_size=8)
    suffix = uuid4().hex[:10]
    try:
        async with pool.acquire() as conn, conn.transaction():
            tenant_id = await conn.fetchval(
                "INSERT INTO furniscope.tenants(tenant_code,name,status) VALUES($1,'External test tenant','active') RETURNING id",
                f"EXT_{suffix.upper()}",
            )
            user_id = await conn.fetchval(
                """INSERT INTO furniscope.users(tenant_id,email,password_hash,name,role_code,status)
                   VALUES($1,$2,'not-a-login-secret','External tester','user','active') RETURNING id""",
                tenant_id, f"external-{suffix}@example.invalid",
            )
            product_id = await conn.fetchval(
                """INSERT INTO furniscope.products(tenant_id,sku,name,category_code,analysis_status,created_by)
                   VALUES($1,$2,'Oak lounge chair','chair','ready',$3) RETURNING id""",
                tenant_id, f"CHAIR-{suffix}", user_id,
            )
            profile_id = await conn.fetchval(
                """INSERT INTO furniscope.product_profile_versions
                   (tenant_id,product_id,version_no,schema_version,status,completeness_score,source_summary,confirmed_by,confirmed_at)
                   VALUES($1,$2,1,'test-v1','confirmed',1,'{"source":"authorized test fixture"}',$3,now()) RETURNING id""",
                tenant_id, product_id, user_id,
            )
            await conn.execute("UPDATE furniscope.products SET current_profile_version_id=$2 WHERE id=$1", product_id, profile_id)
            for code, value in (("material", "oak wood"), ("style", "modern"),
                                ("function", "lounge chair"), ("factory_price", "110"),
                                ("moq", "100")):
                await conn.execute(
                    """INSERT INTO furniscope.product_attributes
                       (tenant_id,profile_version_id,attribute_code,value,source_type,confidence,confirmation_status)
                       VALUES($1,$2,$3,to_jsonb($4::text),'confirmed_structured',.95,'confirmed')""",
                    tenant_id, profile_id, code, value,
                )
            await conn.execute(
                """INSERT INTO furniscope.enterprise_profiles
                   (tenant_id,business_model,primary_categories,export_markets,annual_capacity_note,
                    constraints,profile_completeness,profile_version,confirmed_by,confirmed_at)
                   VALUES($1,'["ODM"]','["chair"]','["US"]','12000 pcs/year',
                     '[{"constraint_type":"unit_cost","operator":"between","value":{"min":80,"max":150},"unit":"USD","hardness":"soft"}]',
                     1,2,$2,now())""",
                tenant_id, user_id,
            )
            capability_rows = (
                ("category", "chair", "扶手椅", None, None, None),
                ("material", "solid_wood", "实木", None, None, None),
                ("process", "upholstery", "软包", None, None, None),
                ("certification", "bsci", "BSCI", None, None, None),
                ("customization", "size", "尺寸定制", None, None, None),
                ("delivery", "moq_range", "MOQ 范围", 50, 200, "pcs"),
            )
            for cap_type, code, name, minimum, maximum, unit in capability_rows:
                await conn.execute(
                    """INSERT INTO furniscope.manufacturing_capabilities
                       (tenant_id,capability_type,capability_code,capability_name,availability,
                        min_value,max_value,unit,source_type,confidence,created_by)
                       VALUES($1,$2,$3,$4,'yes',$5,$6,$7,'confirmed_user',1,$8)""",
                    tenant_id, cap_type, code, name, minimum, maximum, unit, user_id,
                )
            enterprise_snapshot = {
                "profile_version": 2,
                "captured_at": "2026-09-13T00:00:00Z",
                "profile": {
                    "primary_categories": ["chair"], "export_markets": ["US"],
                    "profile_completeness": 1,
                    "constraints": [{"constraint_type": "unit_cost", "operator": "between",
                                     "value": {"min": 80, "max": 150}, "unit": "USD",
                                     "hardness": "soft"}],
                },
                "capabilities": [
                    {"capability_type": cap_type, "capability_code": code,
                     "capability_name": name, "availability": "yes",
                     "min_value": minimum, "max_value": maximum, "unit": unit}
                    for cap_type, code, name, minimum, maximum, unit in capability_rows
                ],
            }
            dataset_id = await conn.fetchval(
                """INSERT INTO furniscope.market_datasets
                   (tenant_id,name,platform,market_country,category_code,data_start_date,data_end_date,
                    source_type,source_name,authorization_reference,status,listing_count,review_count,valid_review_count,quality_score)
                   VALUES($1,'Licensed chair sample','amazon','US','chair',CURRENT_DATE-90,CURRENT_DATE,
                    'licensed_provider','licensed-test-provider','contract-test-001','ready',3,6,6,92) RETURNING id""",
                tenant_id,
            )
            listing_ids = []
            for index, (title, price, rating) in enumerate((
                ("Modern oak lounge chair", 189, 4.5),
                ("Wood reading chair with cushion", 159, 4.1),
                ("Modern accent chair", 129, 3.8),
            ), 1):
                listing_ids.append(await conn.fetchval(
                    """INSERT INTO furniscope.market_listings
                       (tenant_id,dataset_id,platform_listing_id,title,category_code,currency,sale_price,
                        rating,review_count,captured_at,normalized_attributes)
                       VALUES($1,$2,$3,$4,'chair','USD',$5,$6,2,now(),$7::jsonb) RETURNING id""",
                    tenant_id, dataset_id, f"LIST-{suffix}-{index}", title, price, rating,
                    '{"material":{"value":"wood"},"style":{"value":"modern"}}',
                ))
            review_texts = (
                "Very comfortable cushion and sturdy oak frame.",
                "Assembly was easy and the instructions were clear.",
                "The package was damaged and one wooden leg was broken.",
                "Good size for a reading corner and looks beautiful.",
                "The seat is too firm and not comfortable for long use.",
                "Great material quality but the box had a bad smell.",
            )
            for index, text in enumerate(review_texts):
                await conn.execute(
                    """INSERT INTO furniscope.reviews
                       (tenant_id,dataset_id,listing_id,platform_review_id,rating,content_original,
                        language_code,reviewed_at,is_valid,content_hash)
                       VALUES($1,$2,$3,$4,$5,$6,'en',CURRENT_DATE-($7::int*15),true,$8)""",
                    tenant_id, dataset_id, listing_ids[index % 3], f"REV-{suffix}-{index}",
                    2 if "damaged" in text or "not comfortable" in text else 5,
                    text, index, hashlib.sha256(text.encode()).hexdigest(),
                )
            task = await conn.fetchrow(
                """INSERT INTO furniscope.analysis_tasks
                   (tenant_id,job_name,product_id,product_profile_version_id,dataset_id,target_country,target_platform,
                    analysis_currency,status,external_stage,internal_stage,progress_percent,analysis_config,
                    ontology_version,scoring_version,prompt_bundle_version,model_route_version,idempotency_key,created_by,
                    enterprise_profile_version,enterprise_profile_snapshot)
                   VALUES($1,'Authorized external workflow',$2,$3,$4,'US','amazon','USD','queued',
                    'understanding_product','task_initializing',0,'{}','taxonomy-v1','opportunity-score-v2','prompt-v1','route-v1',$5,$6,2,$7::jsonb)
                   RETURNING id,task_uuid""",
                tenant_id, product_id, profile_id, dataset_id, f"external-{suffix}", user_id,
                json.dumps(enterprise_snapshot),
            )

        state = demo_initial_state()
        state.update(
            task_id=task["id"], task_uuid=str(task["task_uuid"]), tenant_id=tenant_id,
            product_id=product_id, product_profile_version=profile_id, dataset_id=dataset_id,
            target_market={"country": "US", "platform": "amazon", "currency": "USD"},
            version_bundle={"ontology_version": "taxonomy-v1", "scoring_version": "opportunity-score-v2", "prompt_bundle_version": "prompt-v1", "model_route_version": "route-v1"},
            enterprise_profile_snapshot=enterprise_snapshot,
        )
        repository = PostgresWorkflowRepository(pool)
        graph = build_graph(repository, ExternalFurnitureToolbox(pool), checkpointer=InMemorySaver())
        result = await FurniScopeAgentEngine(graph, repository).run(state)

        assert result["status"] == "succeeded"
        persisted = await pool.fetchrow(
            """SELECT t.status,r.report_uuid,r.data_scope_snapshot->>'data_class' data_class,
                      r.enterprise_profile_snapshot->>'profile_version' profile_version,
                      (SELECT count(*) FROM furniscope.competitor_matches WHERE analysis_job_id=t.id) competitors,
                      (SELECT count(*) FROM furniscope.review_aspects WHERE analysis_job_id=t.id) aspects,
                      (SELECT count(*) FROM furniscope.insight_clusters WHERE analysis_job_id=t.id) clusters,
                      (SELECT count(*) FROM furniscope.market_opportunities WHERE analysis_job_id=t.id) opportunities,
                      (SELECT count(*) FROM furniscope.evidence_links WHERE analysis_job_id=t.id) evidence
                 FROM furniscope.analysis_tasks t JOIN furniscope.analysis_reports r ON r.analysis_job_id=t.id
                WHERE t.id=$1""",
            task["id"],
        )
        assert persisted["status"] == "succeeded"
        assert persisted["data_class"] == "authorized_market_data"
        assert persisted["competitors"] == 3
        assert persisted["aspects"] == 6
        assert persisted["clusters"] >= 2
        assert persisted["opportunities"] >= 1
        assert persisted["evidence"] >= persisted["opportunities"]
        assert persisted["profile_version"] == "2"
    finally:
        await pool.close()
