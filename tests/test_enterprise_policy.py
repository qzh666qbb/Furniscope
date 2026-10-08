"""Engineering behavior with synthetic fixtures, not business ranking validation."""

import copy
import os
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from furniscope_agent.enterprise_decision import default_policy, enterprise_fit, score_opportunity
from furniscope_agent.external_toolbox import ExternalFurnitureToolbox
from furniscope_api.config import ApiSettings
from furniscope_api.database import Database, bind_tenant_session, tenant_pool_setup
from furniscope_api.errors import BusinessError
from furniscope_api.repositories.analysis_task_repository import AnalysisTaskRepository
from furniscope_api.schemas.opportunity_policy import OpportunityPolicySave, OpportunityFeedbackSave
from furniscope_api.services.opportunity_policy import OpportunityPolicyService


def snapshot(availability="yes"):
    return {
        "profile_version": 1, "captured_at": "2025-06-01",
        "profile": {"confirmed_at": "2025-01-01", "confirmed_by": 1,
                    "primary_categories": ["sofa"], "export_markets": ["US"]},
        "opportunity_policy": {**default_policy(), "required_capabilities": [
            {"capability_type": "packaging", "capability_code": "drop_test"}]},
        "capabilities": [{"capability_type": "packaging", "capability_code": "drop_test",
                          "availability": availability, "source_type": "confirmed_user"}],
    }


def fit(value):
    return enterprise_fit(value, category_code="sofa", market_country="US", taxonomy_code="packaging")


def test_explicit_capabilities_unknowns_completeness_and_frozen_dates():
    value = snapshot()
    assert fit(value)[:2] == (100, 1)
    value["profile"]["profile_completeness"] = 0
    assert fit(value)[:2] == (100, 1)  # Documentation completeness is not capacity.
    value["capabilities"][0]["valid_until"] = "2025-12-31"
    assert fit(value)[2]["status"] == "pass"  # Compare at task capture date.
    value["capabilities"][0]["valid_until"] = "2025-01-01"
    assert fit(value)[2]["status"] == "unknown"
    value = snapshot("no")
    value["capabilities"].append({"capability_type": "packaging", "capability_code": "other_test",
                                 "availability": "yes", "source_type": "confirmed_user"})
    assert fit(value)[2]["blocked"]  # Another capability does not cancel an exact prohibition.
    value["capabilities"][0]["source_type"] = "inferred"
    assert fit(value)[2]["status"] == "unknown"
    assert fit({})[0] is None


def test_constraints_require_confirmed_facts_same_units_and_supported_rule():
    value = snapshot()
    value["profile"]["constraints"] = [{"constraint_type": "unit_cost", "operator": "lte",
                                       "value": 100, "unit": "USD", "hardness": "hard"}]
    value["product_facts"] = {"factory_price": {"value": 120, "unit": "USD",
                                              "confirmation_status": "confirmed"}}
    assert fit(value)[2]["status"] == "unknown"  # Price never substitutes for unit cost.
    value["product_facts"]["unit_cost"] = value["product_facts"].pop("factory_price")
    assert fit(value)[2]["blocked"]
    value["product_facts"]["unit_cost"]["unit"] = "CNY"
    assert fit(value)[2]["status"] == "unknown"
    value["product_facts"]["unit_cost"]["unit"] = "USD"
    value["product_facts"]["unit_cost"]["value"] = 80
    assert fit(value)[2]["status"] == "pass"
    value["product_facts"]["unit_cost"]["confirmation_status"] = "unconfirmed"
    assert fit(value)[2]["status"] == "unknown"


def test_policy_validation_missing_factor_renormalization_and_no_zero_division():
    policy = default_policy()
    inputs = dict(zip(policy["weights"], [80, None, 60, 50, None]))
    score = score_opportunity(inputs, policy, 50)
    assert score["market_score"] == pytest.approx(65.33)
    assert score["adjusted_score"] == pytest.approx(55.53)
    assert score["coverage"] == pytest.approx(.75)
    policy["weights"] = dict(zip(policy["weights"], [0, 0, 0, 0, 1]))
    assert score_opportunity(inputs, policy, None)["market_score"] is None
    for weights in [{}, {"demand_heat": 1}, {**default_policy()["weights"], "demand_heat": float("nan")}]:
        with pytest.raises(ValueError, match="权重"):
            OpportunityPolicySave(expected_version=0, name="invalid", objective="custom",
                                  weights=weights, fit_strength=.3)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("FURNISCOPE_TEST_DATABASE_URL"), reason="needs isolated PostgreSQL")
async def test_policy_snapshot_scoring_feedback_and_tenant_boundaries():
    dsn = os.environ["FURNISCOPE_TEST_DATABASE_URL"]
    db = Database(ApiSettings(database_url=dsn.replace("postgresql://", "postgresql+asyncpg://"), app_env="test"))
    service = OpportunityPolicyService()
    identities = []
    try:
        async with db.session_factory() as admin:
            for _ in range(2):
                tenant = await admin.scalar(text("""
                    INSERT INTO tenants(tenant_code,name,status) VALUES(:code,'策略测试','active') RETURNING id
                """), {"code": f"EP_{uuid4().hex[:16].upper()}"})
                user = await admin.scalar(text("""
                    INSERT INTO users(tenant_id,email,password_hash,name,role_code,status)
                    VALUES(:t,:email,'not-a-login','测试','user','active') RETURNING id
                """), {"t": tenant, "email": f"{uuid4().hex}@example.invalid"})
                identities.append((tenant, user))
            await admin.commit()
        tenant, user = identities[0]
        async with db.session_factory() as session:
            await bind_tenant_session(session, tenant)
            body = OpportunityPolicySave(expected_version=0, name="包装先行", objective="custom",
                weights=default_policy()["weights"], fit_strength=.4,
                required_capabilities=[{"capability_type": "packaging", "capability_code": "drop_test"}])
            policy = await service.save(session, tenant, user, body)
            assert policy["version"] == 1
            assert (await service.current(session, identities[1][0]))["version"] == 0
            await session.execute(text("""
                INSERT INTO enterprise_profiles(tenant_id,primary_categories,export_markets,confirmed_by,confirmed_at)
                VALUES(:t,'["sofa"]','["US"]',:u,now())
            """), {"t": tenant, "u": user})
            await session.execute(text("""
                INSERT INTO manufacturing_capabilities(tenant_id,capability_type,capability_code,capability_name,
                  availability,source_type) VALUES(:t,'packaging','drop_test','跌落测试','no','confirmed_user')
            """), {"t": tenant})
            product = await session.scalar(text("""
                INSERT INTO products(tenant_id,sku,name,category_code) VALUES(:t,'SOFA','测试','sofa') RETURNING id
            """), {"t": tenant})
            profile = await session.scalar(text("""
                INSERT INTO product_profile_versions(tenant_id,product_id,version_no,schema_version,
                  status,confirmed_by,confirmed_at) VALUES(:t,:p,1,'test','confirmed',:u,now()) RETURNING id
            """), {"t": tenant, "p": product, "u": user})
            await session.execute(text("""
                INSERT INTO product_attributes(tenant_id,profile_version_id,attribute_code,value,unit,source_type,
                  confidence,confirmation_status) VALUES(:t,:p,'factory_price','100','CNY','user_input',1,'confirmed')
            """), {"t": tenant, "p": profile})
            dataset = await session.scalar(text("""
                INSERT INTO market_datasets(tenant_id,name,platform,market_country,category_code,data_end_date,
                  source_type,source_name,status) VALUES(:t,'测试','amazon','US','sofa','2025-06-01',
                  'demo_synthetic','synthetic engineering fixture','ready') RETURNING id
            """), {"t": tenant})
            task = await AnalysisTaskRepository().create(session, tenant_id=tenant, user_id=user,
                idempotency_key=uuid4().hex, payload={
                    "job_name": "隔离策略测试", "job_type": "product_market_fit", "product_id": product,
                    "product_profile_version_id": profile, "dataset_id": dataset, "target_country": "US",
                    "target_platform": "amazon", "analysis_currency": "USD", "analysis_config": {}},
                versions=dict.fromkeys(["ontology_version", "scoring_version", "prompt_bundle_version", "model_route_version"], "test"))
            snap = await session.scalar(text("SELECT enterprise_profile_snapshot FROM analysis_tasks WHERE id=:t"), {"t": task["id"]})
            original = copy.deepcopy(snap)
            await service.save(session, tenant, user, body.model_copy(update={"expected_version": 1, "fit_strength": .9}))
            with pytest.raises(BusinessError, match="策略已被修改"):
                await service.save(session, tenant, user, body)
            assert snap["opportunity_policy"]["version"] == 1
            await session.execute(text("""
                INSERT INTO insight_clusters(tenant_id,analysis_job_id,cluster_code,taxonomy_code,name,summary,
                  sentiment_distribution,aspect_count,review_count,listing_count,mention_rate,importance_score,
                  cluster_confidence,representative_aspect_ids)
                VALUES(:t,:task,'C1','packaging','包装','工程测试','{}',1,1,1,1,100,1,'[1]')
            """), {"t": tenant, "task": task["id"]})
            # Intentionally no member evidence: unmet_need must remain missing.
            await session.execute(text("""
                INSERT INTO market_metrics(tenant_id,analysis_job_id,metric_code,dimension_type,
                  metric_value,unit,sample_size,confidence,formula_version)
                VALUES(:t,:task,'median_sale_price','market',200,'USD',20,1,'test')
            """), {"t": tenant, "task": task["id"]})
            await session.commit()
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, setup=tenant_pool_setup(tenant))
        try:
            state = {"tenant_id": tenant, "task_id": task["id"], "product_id": product,
                     "product_profile_version": profile, "target_market": {"country": "US", "platform": "amazon"},
                     "enterprise_profile_snapshot": snap, "valid_review_ids": list(range(20)), "valid_listing_ids": [1]}
            result = await ExternalFurnitureToolbox(pool).execute("opportunity_scoring", state)
            opportunity = result.output_ref["opportunity_ids"][0]
            await ExternalFurnitureToolbox(pool).execute("strategy_generate", state, {"opportunity_id": opportunity})
        finally:
            await pool.close()
        async with db.session_factory() as session:
            await bind_tenant_session(session, tenant)
            row = (await session.execute(text("SELECT * FROM market_opportunities WHERE id=:o"),
                                         {"o": opportunity})).mappings().one()
            assert row["recommendation_level"] == "capability_gap"
            assert row["profit_space_score"] is None  # CNY factory cost cannot compare to USD retail.
            assert row["unmet_need_score"] is None
            assert row["policy_snapshot"]["version"] == 1
            rec = (await session.execute(text("SELECT * FROM product_recommendations WHERE opportunity_id=:o"),
                                        {"o": opportunity})).mappings().one()
            assert rec["priority"] == "low" and "暂缓" in rec["recommended_action"]
            assert rec["cost_impact_min"] is None
            assert await session.scalar(text("SELECT enterprise_profile_snapshot FROM analysis_tasks WHERE id=:t"),
                                        {"t": task["id"]}) == original
            feedback = await service.save_feedback(session, tenant, user, opportunity,
                OpportunityFeedbackSave(expected_revision=0, status="rejected", reason="本厂无法完成跌落测试"))
            assert feedback["current"]["score_snapshot"]["policy_snapshot"]["version"] == 1
            await session.commit()
            feedback = await service.save_feedback(session, tenant, user, opportunity,
                OpportunityFeedbackSave(expected_revision=1, status="pending_validation", reason="等待合作方提供测试证明"))
            assert len(feedback["history"]) == 2
            with pytest.raises(BusinessError, match="处理结果已更新"):
                await service.save_feedback(session, tenant, user, opportunity,
                    OpportunityFeedbackSave(expected_revision=0, status="accepted", reason="过期请求"))
            await session.commit()
            with pytest.raises(DBAPIError, match="snapshot is immutable"):
                await session.execute(text("UPDATE analysis_tasks SET enterprise_profile_snapshot='{}' WHERE id=:id"),
                                      {"id": task["id"]})
            await session.rollback()
            with pytest.raises(DBAPIError):
                await session.execute(text("UPDATE enterprise_opportunity_policies SET config='{}' WHERE tenant_id=:t"),
                                      {"t": tenant})
            await session.rollback()
            assert len((await service.export(session, tenant))["items"]) == 2
        async with db.session_factory() as session:
            other, other_user = identities[1]
            await bind_tenant_session(session, other)
            assert (await service.export(session, other))["items"] == []
            with pytest.raises(BusinessError, match="不可访问"):
                await service.save_feedback(session, other, other_user, opportunity,
                    OpportunityFeedbackSave(expected_revision=0, status="accepted", reason="跨企业请求"))
    finally:
        await db.close()
