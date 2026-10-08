"""Controlled data-query planning and rendering checks."""

import json
import os
from uuid import uuid4

from pydantic import ValidationError
import pytest
from sqlalchemy import text

from furniscope_api.config import ApiSettings
from furniscope_api.database import Database, bind_tenant_session
from furniscope_api.schemas.data_imports import ImportRules
from furniscope_api.schemas.context_lifecycle import TurnCreateRequest
from furniscope_api.schemas.data_query import DataQueryPlan
from furniscope_api.services.agent_orchestrator import classify_agent_intent
from furniscope_api.services.data_query import (
    DataQueryService,
    build_data_query_plan,
    data_query_source_kind,
    is_data_query_request,
    render_data_query_answer,
)
from furniscope_api.services.forecast_data_service import ForecastDataService
from furniscope_api.services.turn_service import TurnService


def test_question_maps_to_closed_metric_plan_with_context_filters():
    plan = build_data_query_plan(
        "按月看最近90天各SKU在美国的销量",
        current_sku="HF-A0590",
        current_market="DE",
    )
    assert plan is not None
    assert plan.metrics == ["sales_units"]
    assert plan.grain == "monthly"
    assert plan.group_by == ["sku"]
    assert plan.filters.relative_days == 90
    assert plan.filters.skus == []
    assert plan.filters.sites == ["US"]


def test_current_product_is_default_scope_but_all_skus_is_explicit():
    current = build_data_query_plan(
        "这个产品2025年1月的日均销量是多少",
        current_sku="hf-a0590",
        current_market="US",
    )
    assert current is not None
    assert current.metrics == ["average_daily_sales"]
    assert current.filters.skus == ["HF-A0590"]
    assert current.filters.date_from.isoformat() == "2025-01-01"
    assert current.filters.date_to.isoformat() == "2025-01-31"
    all_skus = build_data_query_plan(
        "所有SKU销量",
        current_sku="HF-A0590",
        current_market="US",
    )
    assert all_skus is not None and all_skus.filters.skus == []


def test_future_forecast_never_falls_into_historical_query():
    assert not is_data_query_request("预测下个月销量")
    assert classify_agent_intent("预测下个月销量") == "forecast"
    assert classify_agent_intent("最近30天销量") == "data_query"
    assert classify_agent_intent("根据认证手册回答") == "rag"
    assert classify_agent_intent("开始分析美国市场竞品") == "workflow"


def test_query_plan_rejects_cross_family_metrics_and_unknown_fields():
    with pytest.raises(ValidationError):
        DataQueryPlan(metrics=["sales_units", "inventory_units"])
    with pytest.raises(ValidationError):
        DataQueryPlan.model_validate({
            "metrics": ["sales_units"],
            "raw_sql": "DROP TABLE sales_facts_daily",
        })


def test_data_query_source_kind_is_a_stable_metric_family():
    assert data_query_source_kind(DataQueryPlan(metrics=["sales_units"])) == "sales"
    assert data_query_source_kind(DataQueryPlan(metrics=["stockout_days"])) == "inventory"


def test_rendered_answer_discloses_version_checksum_and_limit():
    answer = render_data_query_answer({
        "rows": [{"sales_units": 120}],
        "columns": [{
            "key": "sales_units",
            "label": "销量",
            "type": "metric",
            "unit": "件",
        }],
        "source_version": {"version_uuid": "12345678-1234-1234-1234-123456789012"},
        "result_sha256": "a" * 64,
        "limited": True,
        "limitations": [],
    })
    assert "120件" in answer
    assert "展示上限" in answer
    assert "12345678" in answer
    assert "aaaaaaaaaaaa" in answer


@pytest.mark.skipif(
    not os.getenv("FURNISCOPE_TEST_DATABASE_URL"),
    reason="needs isolated PostgreSQL",
)
@pytest.mark.asyncio
async def test_projection_query_audit_and_cross_tenant_isolation(tmp_path):
    suffix = uuid4().hex[:10]
    settings = ApiSettings(
        _env_file=None,
        app_env="test",
        database_url=os.environ["FURNISCOPE_TEST_DATABASE_URL"].replace(
            "postgresql://",
            "postgresql+asyncpg://",
        ),
        demo_storage_root=str(tmp_path),
    )
    database = Database(settings)
    try:
        async with database.session_factory() as session:
            tenant_id = int(await session.scalar(text("""
                INSERT INTO furniscope.tenants(tenant_code,name,status)
                VALUES(:code,'Data query test','active') RETURNING id
            """), {"code": f"DQ_{suffix.upper()}"}))
            user_id = int(await session.scalar(text("""
                INSERT INTO furniscope.users(
                  tenant_id,email,password_hash,name,role_code,status
                ) VALUES(
                  :tenant,:email,'test-hash','Data query user','user','active'
                ) RETURNING id
            """), {
                "tenant": tenant_id,
                "email": f"dq-{suffix}@example.com",
            }))
            workspace_uuid = str(uuid4())
            workspace_id = int(await session.scalar(text("""
                INSERT INTO furniscope.analysis_workspaces(
                  tenant_id,workspace_uuid,name,source,created_by
                ) VALUES(
                  :tenant,CAST(:workspace_uuid AS uuid),
                  'Data query workspace','test',:user
                )
                RETURNING id
            """), {
                "tenant": tenant_id,
                "workspace_uuid": workspace_uuid,
                "user": user_id,
            }))
            other_tenant = int(await session.scalar(text("""
                INSERT INTO furniscope.tenants(tenant_code,name,status)
                VALUES(:code,'Other data tenant','active') RETURNING id
            """), {"code": f"DQO_{suffix.upper()}"}))
            other_user = int(await session.scalar(text("""
                INSERT INTO furniscope.users(
                  tenant_id,email,password_hash,name,role_code,status
                ) VALUES(
                  :tenant,:email,'test-hash','Other query user','user','active'
                ) RETURNING id
            """), {
                "tenant": other_tenant,
                "email": f"dq-other-{suffix}@example.com",
            }))
            await session.commit()
            await bind_tenant_session(session, tenant_id)
            records = [
                {"date": "2025-01-01", "sku": "HF-A0590", "site": "US", "sales": 10},
                {"date": "2025-01-02", "sku": "HF-A0590", "site": "US", "sales": 20},
                {"date": "2025-01-03", "sku": "HF-A0590", "site": "US", "sales": 30},
            ]
            data = ForecastDataService(settings)
            uploaded = await data.upload(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                filename="sales.json",
                content=json.dumps(records).encode(),
            )
            rules = ImportRules(mapping={
                "date": "date",
                "sku": "sku",
                "site": "site",
                "sales": "sales",
            })
            preview = await data.preflight(
                session,
                tenant_id=tenant_id,
                version_uuid=uploaded["version_uuid"],
                rules=rules,
            )
            await data.confirm(
                session,
                tenant_id=tenant_id,
                version_uuid=uploaded["version_uuid"],
                preview_sha256=preview["preview_sha256"],
            )
            service = DataQueryService(settings)
            first_projection = await service.project_version(
                session,
                tenant_id=tenant_id,
                version_uuid=uploaded["version_uuid"],
            )
            repeated_projection = await service.project_version(
                session,
                tenant_id=tenant_id,
                version_uuid=uploaded["version_uuid"],
            )
            assert first_projection["projection_sha256"] == repeated_projection["projection_sha256"]
            result = await service.execute(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=workspace_id,
                plan=DataQueryPlan.model_validate({
                    "metrics": ["sales_units", "average_daily_sales"],
                    "grain": "daily",
                    "filters": {"skus": ["HF-A0590"], "sites": ["US"]},
                }),
            )
            await session.commit()
            assert [row["sales_units"] for row in result["rows"]] == [10, 20, 30]
            assert result["source_version"]["canonical_sha256"] == preview["canonical_sha256"]
            audited = await service.get(
                session,
                tenant_id=tenant_id,
                query_uuid=result["query_uuid"],
            )
            assert audited and audited["result_sha256"] == result["result_sha256"]
            assert await session.scalar(text(
                "SELECT count(*) FROM sales_facts_daily"
            )) == 3
            turn_service = TurnService(settings)
            body = TurnCreateRequest(
                client_turn_id=uuid4(),
                question="最近3天销量是多少",
                memory_mode="temporary",
            )
            turn, created = await turn_service.reserve(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_uuid=workspace_uuid,
                idempotency_key=f"data-query-turn-{suffix}",
                body=body,
            )
            assert created
            await session.commit()
            turn_result = await turn_service.answer(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_uuid=workspace_uuid,
                turn_uuid=turn["turn_uuid"],
                body=body,
            )
            await session.commit()
            assert turn_result["tool_results"][0]["tool"] == "data_query"
            assert turn_result["tool_results"][0]["status"] == "succeeded"
            assert turn_result["citations"][0]["source_type"] == "data_query"
            assert turn_result["context_sources"][0]["type"] in {
                "current_user_input",
                "data_query",
            }

        async with database.session_factory() as other_session:
            await bind_tenant_session(other_session, other_tenant)
            assert await DataQueryService(settings).get(
                other_session,
                tenant_id=other_tenant,
                query_uuid=result["query_uuid"],
            ) is None
            assert await other_session.scalar(text(
                "SELECT count(*) FROM sales_facts_daily"
            )) == 0
            assert other_user > 0
    finally:
        await database.close()
