import asyncio
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from backend.furniscope_api.config import ApiSettings
from backend.furniscope_api.database import Database, bind_tenant_session
from backend.furniscope_api.schemas.enterprise import (
    EnterpriseConstraint,
    EnterpriseConstraintRecord,
)
from backend.furniscope_api.services.market_intelligence import (
    MarketIntelligenceService,
)
from scripts.seed_market_decision_baseline import (
    apply_package,
    classify_review,
    load_package,
)


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = (
    ROOT
    / "data"
    / "market_intelligence"
    / "hefeng_market_decision_baseline_v1.json"
)


def test_market_decision_package_contract_and_review_rules():
    package, package_sha = load_package(PACKAGE)

    assert len(package_sha) == 64
    assert set(package["capabilities"]) == {
        "smart_selection",
        "competitor_tracking",
        "review_mining",
        "pricing",
        "compliance",
    }
    taxonomies, sentiment = classify_review(
        "The chair is comfortable but the armrest is too narrow.",
        2,
        package["capabilities"]["review_mining"],
    )
    assert {item["code"] for item in taxonomies} == {"comfort", "dimension"}
    assert sentiment == "mixed"


def test_pricing_constraint_governance_is_read_only():
    constraint = {
        "constraint_type": "unit_cost",
        "operator": "eq",
        "value": 100.39,
        "unit": "USD",
        "hardness": "planning",
        "source_class": "planning_assumption",
        "rule_version": "market-planning-anchor-v1",
        "governance_batch_uuid": "f50feab9-3c84-4765-97a7-293cf12d716b",
    }

    record = EnterpriseConstraintRecord.model_validate(constraint)
    assert record.source_class == "planning_assumption"
    assert str(record.governance_batch_uuid) == constraint["governance_batch_uuid"]
    with pytest.raises(ValidationError):
        EnterpriseConstraint.model_validate(constraint)


async def _seed_scope(database: Database) -> tuple[int, int, str]:
    suffix = uuid4().hex[:12]
    email = f"market-baseline-{suffix}@example.invalid"
    async with database.session_factory() as session:
        tenant_id = int(
            await session.scalar(
                text(
                    """
                    INSERT INTO tenants(tenant_code,name,status,data_class)
                    VALUES(:code,'Decision Data Tenant','active','business')
                    RETURNING id
                    """
                ),
                {"code": f"MKT_{suffix.upper()}"},
            )
        )
        user_id = int(
            await session.scalar(
                text(
                    """
                    INSERT INTO users(
                      tenant_id,email,password_hash,name,role_code,status
                    ) VALUES(
                      :tenant,:email,'not-a-login-hash','Decision Data Owner',
                      'user','active'
                    )
                    RETURNING id
                    """
                ),
                {"tenant": tenant_id, "email": email},
            )
        )
        product_id = int(
            await session.scalar(
                text(
                    """
                    INSERT INTO products(
                      tenant_id,sku,name,category_code,analysis_status,created_by
                    ) VALUES(
                      :tenant,'CHAIR-BASE-01','Baseline Lounge Chair','sofa',
                      'ready',:user
                    )
                    RETURNING id
                    """
                ),
                {"tenant": tenant_id, "user": user_id},
            )
        )
        profile_id = int(
            await session.scalar(
                text(
                    """
                    INSERT INTO product_profile_versions(
                      tenant_id,product_id,version_no,schema_version,status,
                      completeness_score,confirmed_by,confirmed_at
                    ) VALUES(
                      :tenant,:product,1,'decision-baseline-v1','confirmed',
                      0.8,:user,CURRENT_TIMESTAMP
                    )
                    RETURNING id
                    """
                ),
                {
                    "tenant": tenant_id,
                    "product": product_id,
                    "user": user_id,
                },
            )
        )
        await session.execute(
            text(
                """
                UPDATE products SET current_profile_version_id=:profile
                 WHERE id=:product AND tenant_id=:tenant
                """
            ),
            {
                "profile": profile_id,
                "product": product_id,
                "tenant": tenant_id,
            },
        )
        dataset_id = int(
            await session.scalar(
                text(
                    """
                    INSERT INTO market_datasets(
                      tenant_id,name,platform,market_country,category_code,
                      data_end_date,source_type,source_name,
                      authorization_reference,status,listing_count,review_count,
                      valid_review_count,quality_report,limitations
                    ) VALUES(
                      :tenant,'Authorized Chair Market','amazon','US','sofa',
                      CURRENT_DATE,'licensed_provider','Authorized Provider',
                      'AUTH-DECISION-DATA-001','ready',20,5,5,
                      '{"data_class":"authorized_market_data"}','[]'
                    )
                    RETURNING id
                    """
                ),
                {"tenant": tenant_id},
            )
        )
        listing_ids = []
        for index in range(20):
            listing_ids.append(
                int(
                    await session.scalar(
                        text(
                            """
                            INSERT INTO market_listings(
                              tenant_id,dataset_id,platform_listing_id,title,
                              category_code,currency,sale_price,rating,
                              rating_count,review_count,captured_at
                            ) VALUES(
                              :tenant,:dataset,:external_id,:title,'sofa','USD',
                              :price,:rating,:rating_count,:review_count,
                              CURRENT_TIMESTAMP
                            )
                            RETURNING id
                            """
                        ),
                        {
                            "tenant": tenant_id,
                            "dataset": dataset_id,
                            "external_id": f"AUTH-CHAIR-{index + 1:03d}",
                            "title": f"Authorized Lounge Chair {index + 1}",
                            "price": 120 + index * 5,
                            "rating": 4.1,
                            "rating_count": 40 + index,
                            "review_count": 20 + index,
                        },
                    )
                )
            )
        contents = [
            "The chair is comfortable and the back support works well.",
            "Assembly instructions are difficult to follow.",
            "The package arrived with a damaged corner.",
            "The fabric color matches the product photo.",
            "The frame feels sturdy but the seat is too narrow.",
        ]
        for index, content in enumerate(contents):
            await session.execute(
                text(
                    """
                    INSERT INTO reviews(
                      tenant_id,dataset_id,listing_id,platform_review_id,rating,
                      content_original,language_code,sentiment,is_valid,
                      content_hash
                    ) VALUES(
                      :tenant,:dataset,:listing,:external_id,:rating,:content,
                      'en',:sentiment,TRUE,:content_hash
                    )
                    """
                ),
                {
                    "tenant": tenant_id,
                    "dataset": dataset_id,
                    "listing": listing_ids[index],
                    "external_id": f"AUTH-REVIEW-{index + 1:03d}",
                    "rating": 2 if index in {1, 2} else 4,
                    "content": content,
                    "sentiment": "negative" if index in {1, 2} else "positive",
                    "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                },
            )
        await session.commit()
    return tenant_id, dataset_id, email


@pytest.mark.skipif(
    not os.getenv("FURNISCOPE_TEST_DATABASE_URL"),
    reason="needs isolated PostgreSQL",
)
def test_market_decision_import_is_idempotent_and_tenant_scoped():
    database_url = os.environ["FURNISCOPE_TEST_DATABASE_URL"].replace(
        "postgresql://",
        "postgresql+asyncpg://",
        1,
    )
    settings = ApiSettings(
        _env_file=None,
        app_env="test",
        database_url=database_url,
    )
    database = Database(settings)

    async def scenario():
        tenant_id, dataset_id, email = await _seed_scope(database)
        async with database.session_factory() as session:
            stale_dataset_id = int(
                await session.scalar(
                    text(
                        """
                        INSERT INTO market_datasets(
                          tenant_id,name,platform,market_country,category_code,
                          data_end_date,source_type,source_name,
                          authorization_reference,status,listing_count,
                          review_count,valid_review_count,quality_report,
                          limitations,deleted_at
                        ) VALUES(
                          :tenant,'Archived Market','amazon','US','sofa',
                          CURRENT_DATE,'licensed_provider','Archived Provider',
                          'AUTH-ARCHIVED-001','ready',1,0,0,
                          '{"data_class":"authorized_market_data"}','[]',
                          CURRENT_TIMESTAMP
                        )
                        RETURNING id
                        """
                    ),
                    {"tenant": tenant_id},
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO competitor_watch_targets(
                      tenant_id,dataset_id,platform,market_country,asin,title,
                      status,compare_selected,created_by
                    ) VALUES(
                      :tenant,:dataset,'amazon','US','AUTH-CHAIR-020',
                      'Archived target','active',TRUE,
                      (SELECT id FROM users WHERE tenant_id=:tenant LIMIT 1)
                    )
                    """
                ),
                {"tenant": tenant_id, "dataset": stale_dataset_id},
            )
            await session.commit()
        first = await apply_package(database, package_path=PACKAGE, email=email)
        second = await apply_package(database, package_path=PACKAGE, email=email)

        assert first["governance_batch"]["batch_uuid"] == second["governance_batch"][
            "batch_uuid"
        ]
        assert first["scope"]["dataset_id"] == dataset_id
        assert first["overview"]["smart_selection"]["status"] == "ready"
        assert first["overview"]["competitor_tracking"]["status"] == "ready"
        assert first["overview"]["review_mining"]["status"] == "ready"
        assert first["overview"]["pricing"]["status"] == "planning_anchor"
        assert first["overview"]["compliance"]["status"] == "ready"

        async with database.session_factory() as session:
            await bind_tenant_session(session, tenant_id)
            later_task = (
                await session.execute(
                    text(
                        """
                        INSERT INTO analysis_tasks(
                          tenant_id,job_name,job_type,product_id,
                          product_profile_version_id,dataset_id,target_country,
                          target_platform,analysis_currency,status,external_stage,
                          internal_stage,progress_percent,analysis_config,
                          enterprise_profile_version,enterprise_profile_snapshot,
                          ontology_version,scoring_version,prompt_bundle_version,
                          model_route_version,idempotency_key,started_at,completed_at,
                          created_by
                        )
                        SELECT
                          tenant_id,'Later AI employee task',job_type,product_id,
                          product_profile_version_id,dataset_id,target_country,
                          target_platform,analysis_currency,status,external_stage,
                          internal_stage,progress_percent,
                          jsonb_set(analysis_config,'{source}','"ai_employee"'),
                          enterprise_profile_version,enterprise_profile_snapshot,
                          ontology_version,scoring_version,prompt_bundle_version,
                          model_route_version,:key,CURRENT_TIMESTAMP,
                          CURRENT_TIMESTAMP + INTERVAL '1 minute',created_by
                          FROM analysis_tasks
                         WHERE tenant_id=:tenant
                           AND analysis_config->>'source'='market_decision_baseline'
                        RETURNING task_uuid::text,product_id
                        """
                    ),
                    {
                        "tenant": tenant_id,
                        "key": f"market-baseline-latest-{uuid4()}",
                    },
                )
            ).mappings().one()
            service = MarketIntelligenceService()
            default_task = await service._latest_task(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                product_id=None,
            )
            explicit_product_task = await service._latest_task(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                product_id=int(later_task["product_id"]),
            )
            assert default_task["task_uuid"] == first["overview"]["scope"]["task_uuid"]
            assert explicit_product_task["task_uuid"] == later_task["task_uuid"]
            alert_page = await service.list_competitor_alerts(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                page=1,
                page_size=2,
            )
            assert alert_page["total"] == 6
            assert len(alert_page["items"]) == 2
            assert int(await session.scalar(text("""
                SELECT count(*) FROM competitor_watch_targets
                 WHERE tenant_id=:tenant AND dataset_id<>:dataset
            """), {"tenant": tenant_id, "dataset": dataset_id}) or 0) == 0
            alert_detail = await service.get_competitor_alert(
                session,
                tenant_id=tenant_id,
                alert_id=alert_page["items"][0]["alert_id"],
            )
            assert alert_detail["dataset_id"] == dataset_id
            assert alert_detail["latest_snapshot"]["snapshot_id"]
            cluster_page = await service.list_review_clusters(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                product_id=None,
                page=1,
                page_size=2,
            )
            assert cluster_page["total"] == 7
            assert len(cluster_page["items"]) == 2
            cluster_detail = await service.get_review_cluster(
                session,
                tenant_id=tenant_id,
                cluster_id=cluster_page["items"][0]["cluster_id"],
            )
            assert cluster_detail["dataset_id"] == dataset_id
            assert cluster_detail["evidence"]
            assert cluster_detail["evidence"][0]["evidence_quote"]

            own_counts = (
                await session.execute(
                    text(
                        """
                        SELECT
                          (SELECT count(*) FROM market_intelligence_batches) batches,
                          (SELECT count(*) FROM market_intelligence_lineage) lineage,
                          (SELECT count(*) FROM analysis_tasks
                            WHERE analysis_config->>'source'='market_decision_baseline')
                            tasks
                        """
                    )
                )
            ).mappings().one()
            assert dict(own_counts) == {
                "batches": 1,
                "lineage": len(first["lineage"]),
                "tasks": 1,
            }

        other_tenant, _, _ = await _seed_scope(database)
        async with database.session_factory() as session:
            await bind_tenant_session(session, other_tenant)
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM market_intelligence_batches")
                )
            ) == 0

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(database.close())
