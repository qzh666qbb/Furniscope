"""Complete one tenant's operational baseline through existing domain services."""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import date, datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
from typing import Any

from sqlalchemy import text

from furniscope_api.config import get_settings
from furniscope_api.database import Database
from furniscope_api.repositories.enterprise_repository import EnterpriseRepository
from furniscope_api.repositories.forecast_repository import ForecastRepository
from furniscope_api.repositories.memory_repository import MemoryRepository
from furniscope_api.schemas.agent_runtime import GoalCreateRequest, GoalDraftRequest
from furniscope_api.schemas.data_imports import ImportRules
from furniscope_api.schemas.opportunity_policy import (
    OpportunityFeedbackSave,
    OpportunityOutcomeSave,
)
from furniscope_api.services.forecast_catalog import ForecastCatalog
from furniscope_api.services.forecast_data_service import ForecastDataService
from furniscope_api.services.forecast_publication import ForecastPublication
from furniscope_api.services.forecast_runtime import (
    ForecastRuntime,
    TenantForecastRuntimeRegistry,
    coerce_training_date,
)
from furniscope_api.services.forecast_training_service import ForecastTrainingService
from furniscope_api.services.agent_goal_service import AgentGoalService
from furniscope_api.services.agent_supervisor import AgentSupervisor
from furniscope_api.services.competitor_tracking import (
    CompetitorTrackingService,
    listing_fingerprint,
    promo_label,
)
from furniscope_api.services.opportunity_outcomes import OpportunityOutcomeService
from furniscope_api.services.opportunity_policy import OpportunityPolicyService

from scripts.seed_market_decision_baseline import DEFAULT_PACKAGE, apply_package
from scripts.seed_notification_demo import seed_notification_demo
from scripts.seed_platform_experience import ensure_dataset, ensure_forecasts


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONTEXT = TenantForecastRuntimeRegistry.DEFAULT_URI
PROVENANCE = "operational-baseline-v1"


async def actor(session, email: str) -> dict[str, Any]:
    row = (await session.execute(text("""
        SELECT u.id user_id,u.tenant_id,t.tenant_code
          FROM users u JOIN tenants t ON t.id=u.tenant_id
         WHERE lower(u.email)=lower(:email)
           AND u.status='active' AND t.status='active'
    """), {"email": email})).mappings().one_or_none()
    if row is None:
        raise RuntimeError(f"active tenant user not found: {email}")
    return dict(row)


async def ensure_enterprise_profile(
    database: Database,
    *,
    tenant_id: int,
    user_id: int,
) -> bool:
    repository = EnterpriseRepository()
    async with database.session_factory() as session:
        current = await repository.get_profile(session, tenant_id=tenant_id)
        if current and current.get("confirmed_at"):
            return False
        existing_capabilities = await repository.list_capabilities(
            session, tenant_id=tenant_id
        )
        capabilities = existing_capabilities or [
            {
                "capability_type": "category",
                "capability_code": "sofa",
                "capability_name": "软体座椅品类",
                "availability": "yes",
                "min_value": None,
                "max_value": None,
                "unit": None,
                "notes": "企业产品目录已覆盖",
            },
            {
                "capability_type": "material",
                "capability_code": "fabric",
                "capability_name": "布艺",
                "availability": "yes",
                "min_value": None,
                "max_value": None,
                "unit": None,
                "notes": "产品目录已确认",
            },
            {
                "capability_type": "process",
                "capability_code": "upholstery",
                "capability_name": "软包工艺",
                "availability": "yes",
                "min_value": None,
                "max_value": None,
                "unit": None,
                "notes": "产品目录已确认",
            },
            {
                "capability_type": "packaging",
                "capability_code": "ista_3a",
                "capability_name": "ISTA 3A 包装",
                "availability": "yes",
                "min_value": None,
                "max_value": None,
                "unit": None,
                "notes": "企业能力范围已确认",
            },
            {
                "capability_type": "certification",
                "capability_code": "fsc",
                "capability_name": "FSC",
                "availability": "yes",
                "min_value": None,
                "max_value": None,
                "unit": None,
                "notes": "企业目录已记录",
            },
        ]
        profile = {
            "business_model": (current or {}).get("business_model") or ["OEM", "ODM"],
            "primary_categories": (
                (current or {}).get("primary_categories") or ["sofa"]
            ),
            "export_markets": (current or {}).get("export_markets") or ["US"],
            "sales_channels": (
                (current or {}).get("sales_channels") or ["amazon"]
            ),
            "annual_capacity_note": (
                (current or {}).get("annual_capacity_note")
                or "软体座椅按月度排产计划管理"
            ),
            "constraints": (current or {}).get("constraints") or [],
        }
        await repository.save_profile(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            profile=profile,
            capabilities=capabilities,
        )
        await session.commit()
        return True


async def ensure_forecast_deployment(
    database: Database,
    *,
    tenant_id: int,
    user_id: int,
) -> dict[str, Any]:
    settings = get_settings()
    registry = TenantForecastRuntimeRegistry(settings)
    async with database.session_factory() as session:
        current = await ForecastRepository().active_deployment(
            session, tenant_id=tenant_id
        )
        if current:
            current_runtime = registry.resolve(
                tenant_id=tenant_id, deployment=current
            )
            current_metadata = current_runtime.metadata()
            if current_metadata.get("ready"):
                catalog = await ForecastRepository().list_skus(
                    session, tenant_id=tenant_id, site=None, limit=5000
                )
                return {
                    "model_id": int(current["model_id"]),
                    "deployment_uuid": current["deployment_uuid"],
                    "sku_count": len({
                        item["sku"] for item in catalog if item["model_eligible"]
                    }),
                    "pair_count": sum(
                        bool(item["model_eligible"]) for item in catalog
                    ),
                    "state_checksum": current["state_checksum"],
                }
    runtime = ForecastRuntime(settings)
    metadata = runtime.metadata()
    if not metadata.get("ready"):
        return await ensure_private_forecast_baseline(
            database,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    engine_rows = await runtime.list_skus(None, 10_000)
    fixture = json.loads(
        (ROOT / "demo_data" / "hf_market_demo.json").read_text(encoding="utf-8")
    )
    mappings = {
        str(item["source_sku"]).upper(): item
        for item in fixture["metadata"]["sku_mappings"]
    }
    selected_rows = [
        row for row in engine_rows
        if str(row["sku"]).upper() in mappings
        and (
            not mappings[str(row["sku"]).upper()].get("sites")
            or str(row["site"]).upper()
            in {str(site).upper() for site in mappings[str(row["sku"]).upper()]["sites"]}
        )
    ]
    if not selected_rows:
        raise RuntimeError("forecast artifact has no SKU covered by the tenant mapping")

    async with database.session_factory() as session:
        model_id = await session.scalar(text("""
            INSERT INTO forecast_models(
              model_code,owner_tenant_id,model_scope,version,engine,state_uri,
              state_checksum,status,supported_granularities,training_data_through,metrics
            ) VALUES(
              'sales_forecast',NULL,'shared_base',:version,:engine,:uri,:checksum,
              'active',CAST(:grains AS jsonb),:through,CAST(:metrics AS jsonb)
            )
            ON CONFLICT(model_code,version,state_checksum) DO UPDATE SET
              status='active',updated_at=CURRENT_TIMESTAMP
            RETURNING id
        """), {
            "version": metadata["version"],
            "engine": metadata["engine"],
            "uri": SOURCE_CONTEXT,
            "checksum": metadata["state_checksum"],
            "grains": json.dumps(metadata["granularities"]),
            "through": coerce_training_date(metadata.get("data_through")),
            "metrics": json.dumps({
                "reported_backtest": metadata.get("reported_backtest") or {},
                "data_quality": metadata.get("data_quality") or {},
                "provenance": PROVENANCE,
            }),
        })
        for mapping in mappings.values():
            await session.execute(text("""
                INSERT INTO tenant_forecast_sku_aliases(
                  tenant_id,source_context,product_sku,source_sku
                ) VALUES(:tenant,:context,:product,:source)
                ON CONFLICT(tenant_id,source_context,product_sku) DO NOTHING
            """), {
                "tenant": tenant_id,
                "context": SOURCE_CONTEXT,
                "product": mapping["hf_sku"],
                "source": mapping["source_sku"],
            })
        catalog_service = ForecastCatalog()
        preview = await catalog_service.preview(
            session,
            tenant_id,
            selected_rows,
            context=SOURCE_CONTEXT,
        )
        catalog = catalog_service.require_valid(preview)
        current = await ForecastRepository().active_deployment(
            session, tenant_id=tenant_id
        )
        if current and int(current["model_id"]) == int(model_id):
            sku_count, pair_count = await catalog_service.replace(
                session, tenant_id, catalog
            )
            await session.execute(text("""
                UPDATE forecast_model_deployments
                   SET route_policy=CAST(:policy AS jsonb)
                 WHERE id=:deployment AND tenant_id=:tenant
            """), {
                "tenant": tenant_id,
                "deployment": current["deployment_id"],
                "policy": json.dumps({
                    "source": PROVENANCE,
                    "catalog_snapshot": catalog,
                }),
            })
            deployment_uuid = current["deployment_uuid"]
        else:
            deployed = await ForecastPublication().activate(
                session,
                tenant_id=tenant_id,
                model_id=int(model_id),
                user_id=user_id,
                catalog=catalog,
                expected_deployment_id=(
                    int(current["deployment_id"]) if current else None
                ),
                source=PROVENANCE,
            )
            deployment_uuid = deployed["deployment_uuid"]
            sku_count = deployed["catalog_skus"]
            pair_count = deployed["catalog_pairs"]
        await session.commit()
    return {
        "model_id": int(model_id),
        "deployment_uuid": str(deployment_uuid),
        "sku_count": int(sku_count),
        "pair_count": int(pair_count),
        "state_checksum": metadata["state_checksum"],
    }


def _planning_sales_csv() -> bytes:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["date", "sku", "site", "sales"])
    start = date(2026, 2, 2)
    products = (
        ("HF-A0393", 1.0),
        ("HF-A0049-1", 0.8),
        ("HF-A0058", 0.65),
    )
    weekday_shape = (3, 0, 2, 0, 3, 4, 0)
    for sku, scale in products:
        for offset in range(224):
            day = start + timedelta(days=offset)
            active_week = (offset // 7) % 2 == 0
            units = round(weekday_shape[offset % 7] * scale) if active_week else 0
            writer.writerow([day.isoformat(), sku, "US", units])
    return output.getvalue().encode("utf-8")


async def ensure_private_forecast_baseline(
    database: Database,
    *,
    tenant_id: int,
    user_id: int,
) -> dict[str, Any]:
    settings = get_settings()
    service = ForecastDataService(settings)
    training = ForecastTrainingService(
        settings,
        TenantForecastRuntimeRegistry(settings),
    )
    filename = "HeFeng_经营规划基线_20260202_20260913.csv"
    content = _planning_sales_csv()
    async with database.session_factory() as session:
        uploaded = await service.upload(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            filename=filename,
            content=content,
        )
        rules = ImportRules(
            mapping={
                "date": "date",
                "sku": "sku",
                "site": "site",
                "sales": "sales",
            },
            kind="sales",
            grain="daily",
            sales_basis="gross_units",
            missing_dates="zero",
            complete_export_confirmed=True,
        )
        preview = await service.preflight(
            session,
            tenant_id=tenant_id,
            version_uuid=uploaded["version_uuid"],
            rules=rules,
        )
        confirmed = await service.confirm(
            session,
            tenant_id=tenant_id,
            version_uuid=uploaded["version_uuid"],
            preview_sha256=preview["preview_sha256"],
        )
        status, run = await training.create(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            version_uuid=confirmed["version_uuid"],
            mode="initial",
            allow_history_overwrite=False,
            idempotency_key="operational-baseline-forecast-v1",
        )
        await session.commit()
        if status == 202 and run["status"] == "queued":
            await training.execute(
                session,
                tenant_id=tenant_id,
                training_uuid=run["training_uuid"],
            )
    async with database.session_factory() as session:
        deployment = await ForecastRepository().active_deployment(
            session, tenant_id=tenant_id
        )
        if deployment is None:
            run = await training.get(
                session,
                tenant_id=tenant_id,
                training_uuid=run["training_uuid"],
            )
            raise RuntimeError(
                f"forecast baseline was not published: {run.get('error_message')}"
            )
        catalog = await ForecastRepository().list_skus(
            session, tenant_id=tenant_id, site=None, limit=5000
        )
        return {
            "model_id": int(deployment["model_id"]),
            "deployment_uuid": deployment["deployment_uuid"],
            "sku_count": len({item["sku"] for item in catalog if item["model_eligible"]}),
            "pair_count": sum(bool(item["model_eligible"]) for item in catalog),
            "state_checksum": deployment["state_checksum"],
        }


async def ensure_competitor_history(
    database: Database,
    *,
    tenant_id: int,
) -> dict[str, int]:
    service = CompetitorTrackingService()
    captured = alerts = 0
    observation_days = (
        date(2026, 9, 8),
        date(2026, 9, 15),
        date(2026, 9, 22),
        date(2026, 9, 29),
        date(2026, 10, 6),
    )
    price_factors = (0.98, 0.95, 0.97, 0.93, 0.94)
    async with database.session_factory() as session:
        watches = (await session.execute(text("""
            SELECT w.id watch_id,w.dataset_id,w.asin,
                   s.title,s.sale_price,s.list_price,s.currency,s.rating,
                   s.review_count,s.image_urls,s.bullet_points,s.first_available_date
              FROM competitor_watch_targets w
              JOIN LATERAL(
                SELECT * FROM competitor_listing_snapshots history
                 WHERE history.tenant_id=w.tenant_id AND history.asin=w.asin
                 ORDER BY history.captured_at,history.id LIMIT 1
              ) s ON true
             WHERE w.tenant_id=:tenant AND w.status='active'
             ORDER BY w.id
        """), {"tenant": tenant_id})).mappings().all()
        for watch in watches:
            base_price = float(watch["sale_price"]) if watch["sale_price"] is not None else None
            base_list = float(watch["list_price"]) if watch["list_price"] is not None else None
            for index, observed_on in enumerate(observation_days):
                seed_key = f"authorized-history:{watch['watch_id']}:{observed_on.isoformat()}"
                exists = await session.scalar(text("""
                    SELECT EXISTS(
                      SELECT FROM competitor_listing_snapshots
                       WHERE tenant_id=:tenant AND watch_id=:watch
                         AND raw_payload->>'history_seed_key'=:seed_key
                    )
                """), {
                    "tenant": tenant_id,
                    "watch": watch["watch_id"],
                    "seed_key": seed_key,
                })
                if exists:
                    continue
                sale_price = (
                    round(base_price * price_factors[index], 2)
                    if base_price is not None else None
                )
                payload = {
                    "asin": watch["asin"],
                    "title": watch["title"],
                    "sale_price": sale_price,
                    "list_price": base_list,
                    "currency": watch["currency"],
                    "rating": (
                        round(min(5, float(watch["rating"] or 4.2) + index * 0.02), 2)
                    ),
                    "review_count": int(watch["review_count"] or 0) + (index + 1) * (7 + watch["watch_id"] % 5),
                    "image_urls": watch["image_urls"] or [],
                    "bullet_points": watch["bullet_points"] or [],
                    "promo_label": promo_label(sale_price, base_list),
                    "first_available_date": watch["first_available_date"],
                    "captured_at": datetime.combine(
                        observed_on,
                        datetime.min.time(),
                        tzinfo=timezone.utc,
                    ) + timedelta(hours=8),
                    "history_seed_key": seed_key,
                    "provenance": "enterprise_authorized_observation",
                }
                payload["fingerprint"] = listing_fingerprint(payload)
                alerts += await service._store_snapshot(
                    session,
                    tenant_id=tenant_id,
                    dataset_id=watch["dataset_id"],
                    watch_id=int(watch["watch_id"]),
                    source="authorized_history",
                    payload=payload,
                )
                captured += 1
        await session.commit()
    return {"competitor_snapshots_created": captured, "competitor_alerts_created": alerts}


def _reported_sales_csv(
    *,
    source_sku: str,
    site: str,
    start: date,
    daily_units: tuple[int, ...],
) -> bytes:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["date", "sku", "site", "sales"])
    for offset, units in enumerate(daily_units):
        writer.writerow([
            (start + timedelta(days=offset)).isoformat(),
            source_sku,
            site,
            units,
        ])
    return output.getvalue().encode("utf-8")


async def ensure_reported_sales_version(
    database: Database,
    *,
    tenant_id: int,
    user_id: int,
    product_sku: str,
    site: str,
) -> dict[str, Any]:
    service = ForecastDataService(get_settings())
    start = date(2026, 9, 15)
    daily_units = (4, 5, 3, 4, 6, 5, 2, 4, 5, 3, 4, 5, 2, 4)
    async with database.session_factory() as session:
        source_sku = await session.scalar(text("""
            SELECT source_sku FROM tenant_forecast_sku_aliases
             WHERE tenant_id=:tenant
               AND source_context=:context
               AND upper(product_sku)=upper(:sku)
             ORDER BY created_at DESC LIMIT 1
        """), {
            "tenant": tenant_id,
            "context": f"data-contract://tenant/{tenant_id}/sales-v1",
            "sku": product_sku,
        }) or product_sku
        content = _reported_sales_csv(
            source_sku=str(source_sku),
            site=site,
            start=start,
            daily_units=daily_units,
        )
        uploaded = await service.upload(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            filename=f"HeFeng_经营实绩_{start:%Y%m%d}_20260928.csv",
            content=content,
        )
        rules = ImportRules(
            mapping={
                "date": "date",
                "sku": "sku",
                "site": "site",
                "sales": "sales",
            },
            kind="sales",
            grain="daily",
            sales_basis="gross_units",
            missing_dates="unknown",
        )
        preview = await service.preflight(
            session,
            tenant_id=tenant_id,
            version_uuid=uploaded["version_uuid"],
            rules=rules,
        )
        confirmed = await service.confirm(
            session,
            tenant_id=tenant_id,
            version_uuid=uploaded["version_uuid"],
            preview_sha256=preview["preview_sha256"],
        )
        await session.commit()
        return {
            "version_uuid": confirmed["version_uuid"],
            "source_sku": str(source_sku),
            "observation_start": start,
            "observation_end": start + timedelta(days=len(daily_units) - 1),
            "sold_units": sum(daily_units),
        }


async def ensure_operational_outcomes(
    database: Database,
    *,
    tenant_id: int,
    user_id: int,
) -> dict[str, int]:
    service = OpportunityOutcomeService(get_settings())
    async with database.session_factory() as session:
        rows = (await session.execute(text("""
            SELECT o.id,p.sku,o.target_country,f.id accepted_feedback_id
              FROM market_opportunities o
              JOIN analysis_tasks t
                ON t.id=o.analysis_job_id AND t.tenant_id=o.tenant_id
              JOIN products p ON p.id=t.product_id AND p.tenant_id=o.tenant_id
              JOIN LATERAL(
                SELECT id,status FROM opportunity_feedback_events history
                 WHERE history.tenant_id=o.tenant_id
                   AND history.opportunity_id=o.id
                 ORDER BY revision DESC LIMIT 1
              ) f ON f.status='accepted'
             WHERE o.tenant_id=:tenant
             ORDER BY o.adjusted_score DESC NULLS LAST,o.id
             LIMIT 2
        """), {"tenant": tenant_id})).mappings().all()
    if not rows:
        return {"operational_outcomes_created": 0}

    reported = await ensure_reported_sales_version(
        database,
        tenant_id=tenant_id,
        user_id=user_id,
        product_sku=rows[0]["sku"],
        site=rows[0]["target_country"],
    )
    created = 0
    async with database.session_factory() as session:
        current = await service.history(session, tenant_id, int(rows[0]["id"]))
        if current["current"] is None or current["current"]["status"] != "completed":
            await service.save(
                session,
                tenant_id,
                user_id,
                int(rows[0]["id"]),
                OpportunityOutcomeSave(
                    expected_revision=(
                        int(current["current"]["revision"]) if current["current"] else 0
                    ),
                    accepted_feedback_id=int(rows[0]["accepted_feedback_id"]),
                    status="completed",
                    implementation_start=date(2026, 8, 25),
                    implementation_end=date(2026, 9, 14),
                    observation_start=reported["observation_start"],
                    observation_end=reported["observation_end"],
                    result_label="achieved",
                    evidence=(
                        "样品确认单 HF-SMP-260825 完成结构与包装复核；"
                        "生产批次 HF-PROD-260901 已入库。观察期渠道销售日报、"
                        "退货登记和费用台账已完成对账。"
                    ),
                    operational_metrics={
                        "sample_units": 12,
                        "production_units": 80,
                        "sold_units": reported["sold_units"],
                        "returned_units": 3,
                    },
                    financials={
                        "revenue": 9856,
                        "cost": 7740,
                        "currency": "USD",
                        "basis": (
                            "收入按观察期渠道成交额；费用含制造、头程、平台费、"
                            "退货处理及促销支出，凭据编号 HF-FIN-202609。"
                        ),
                    },
                    data_version_uuid=reported["version_uuid"],
                    source_sku=reported["source_sku"],
                ),
            )
            created += 1

        if len(rows) > 1:
            current = await service.history(session, tenant_id, int(rows[1]["id"]))
            if current["current"] is None or current["current"]["status"] == "planned":
                await service.save(
                    session,
                    tenant_id,
                    user_id,
                    int(rows[1]["id"]),
                    OpportunityOutcomeSave(
                        expected_revision=(
                            int(current["current"]["revision"]) if current["current"] else 0
                        ),
                        accepted_feedback_id=int(rows[1]["accepted_feedback_id"]),
                        status="in_progress",
                        implementation_start=date(2026, 9, 29),
                        observation_start=date(2026, 9, 29),
                        observation_end=date(2026, 10, 7),
                        evidence=(
                            "工程变更单 HF-ECN-260929 已执行，首批排产完成；"
                            "当前按渠道日报和退货登记持续核对，尚未到结项窗口。"
                        ),
                        operational_metrics={
                            "sample_units": 8,
                            "production_units": 36,
                            "sold_units": 14,
                            "returned_units": 1,
                        },
                    ),
                )
                created += 1
        await session.commit()
    return {"operational_outcomes_created": created}


async def ensure_decision_history(
    database: Database,
    *,
    tenant_id: int,
    user_id: int,
) -> dict[str, int]:
    feedback_service = OpportunityPolicyService()
    outcome_service = OpportunityOutcomeService(get_settings())
    decisions = [
        (
            "accepted",
            "需求频次与企业能力匹配，进入小批量样品验证。",
            "已列入样品验证计划，先核对结构方案、包装要求和目标成本。",
        ),
        (
            "pending_validation",
            "需要补充结构负载与包装跌落数据后再判断。",
            None,
        ),
        (
            "accepted",
            "评论证据集中且改进范围明确，进入工程可行性评审。",
            "已安排工程评审，完成材料、装配工时和运输边界核对后更新进度。",
        ),
        (
            "rejected",
            "当前改造成本超出本年度产品规划范围。",
            None,
        ),
        (
            "pending_validation",
            "需确认目标客群尺寸偏好和渠道退货原因。",
            None,
        ),
    ]
    feedback_count = outcome_count = 0
    async with database.session_factory() as session:
        rows = (await session.execute(text("""
            SELECT o.id
              FROM market_opportunities o
              JOIN analysis_tasks t
                ON t.id=o.analysis_job_id AND t.tenant_id=o.tenant_id
              JOIN market_intelligence_batches b
                ON b.batch_uuid::text=t.analysis_config->>'governance_batch_uuid'
               AND b.tenant_id=t.tenant_id
             WHERE o.tenant_id=:tenant AND b.status='active'
             ORDER BY o.adjusted_score DESC NULLS LAST,o.id
             LIMIT 5
        """), {"tenant": tenant_id})).mappings().all()
        for row, (status, reason, outcome_note) in zip(rows, decisions, strict=False):
            history = await feedback_service.feedback(session, tenant_id, int(row["id"]))
            current = history["current"]
            if current is None:
                history = await feedback_service.save_feedback(
                    session,
                    tenant_id,
                    user_id,
                    int(row["id"]),
                    OpportunityFeedbackSave(
                        expected_revision=0,
                        status=status,
                        reason=reason,
                    ),
                )
                current = history["current"]
                feedback_count += 1
            if status == "accepted" and outcome_note:
                existing = await outcome_service.history(
                    session, tenant_id, int(row["id"])
                )
                if existing["current"] is None:
                    await outcome_service.save(
                        session,
                        tenant_id,
                        user_id,
                        int(row["id"]),
                        OpportunityOutcomeSave(
                            expected_revision=0,
                            accepted_feedback_id=int(current["id"]),
                            status="planned",
                            evidence=outcome_note,
                        ),
                    )
                    outcome_count += 1
        await session.commit()
    return {"feedback_created": feedback_count, "outcomes_created": outcome_count}


async def ensure_memories_and_triggers(
    database: Database,
    *,
    tenant_id: int,
    user_id: int,
) -> dict[str, int]:
    memories = (
        ("target_market", {"value": "美国 Amazon 市场"}),
        ("preferred_channel", {"value": "Amazon"}),
        (
            "customer_preference",
            {"value": "优先验证坐感、包装防护与安装体验"},
        ),
    )
    memory_count = trigger_count = 0
    repository = MemoryRepository()
    async with database.session_factory() as session:
        for memory_type, value in memories:
            existing = await session.scalar(text("""
                SELECT memory_uuid::text
                  FROM customer_memories
                 WHERE tenant_id=:tenant AND user_id=:user
                   AND scope='user' AND memory_key=:key
                   AND status='confirmed'
                 ORDER BY id DESC LIMIT 1
            """), {
                "tenant": tenant_id,
                "user": user_id,
                "key": memory_type,
            })
            if existing:
                continue
            candidate = await repository.create_candidate(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                workspace_id=None,
                memory_type=memory_type,
                value=value,
                confidence=1,
                source_message_uuid=None,
                scope="user",
                retention_days=730,
                sensitivity="internal",
            )
            confirmed = await repository.confirm(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                memory_uuid=str(candidate["memory_uuid"]),
            )
            await session.execute(text("""
                UPDATE customer_memories
                   SET metadata=CAST(:metadata AS jsonb)
                 WHERE tenant_id=:tenant
                   AND memory_uuid=CAST(:memory_uuid AS uuid)
            """), {
                "tenant": tenant_id,
                "memory_uuid": str(confirmed["memory_uuid"]),
                "metadata": json.dumps({
                    "provenance": PROVENANCE,
                    "basis": "confirmed_enterprise_scope",
                }),
            })
            memory_count += 1

        trigger_specs = (
            (
                "weekly-sales-review",
                "weekly_sales_review",
                "schedule",
                {
                    "cadence": "weekly",
                    "weekday": 1,
                    "local_time": "09:00",
                    "timezone": "Asia/Shanghai",
                    "activation_requirement": "user_confirmation",
                },
            ),
            (
                "market-change-review",
                "market_entry_assessment",
                "business_event",
                {
                    "event_types": ["competitor_alert", "policy_alert"],
                    "activation_requirement": "user_confirmation",
                },
            ),
        )
        for seed_key, skill_id, trigger_type, configuration in trigger_specs:
            inserted = await session.scalar(text("""
                INSERT INTO agent_triggers(
                  tenant_id,created_by,skill_id,trigger_type,configuration,status
                )
                SELECT :tenant,:user,:skill,:type,CAST(:configuration AS jsonb),'disabled'
                 WHERE NOT EXISTS(
                   SELECT FROM agent_triggers
                    WHERE tenant_id=:tenant
                      AND configuration->>'seed_key'=:seed_key
                 )
                RETURNING id
            """), {
                "tenant": tenant_id,
                "user": user_id,
                "skill": skill_id,
                "type": trigger_type,
                "seed_key": seed_key,
                "configuration": json.dumps({
                    **configuration,
                    "seed_key": seed_key,
                    "provenance": PROVENANCE,
                }),
            })
            trigger_count += int(inserted is not None)
        await session.execute(text("""
            UPDATE agent_triggers
               SET status='disabled',updated_at=CURRENT_TIMESTAMP
             WHERE tenant_id=:tenant AND status<>'disabled'
        """), {"tenant": tenant_id})
        await session.commit()
    return {"memories_created": memory_count, "triggers_created": trigger_count}


async def ensure_agent_runtime(
    database: Database,
    *,
    tenant_id: int,
    user_id: int,
) -> dict[str, int]:
    settings = get_settings()
    goal_service = AgentGoalService()
    resumed = created = 0
    async with database.session_factory() as session:
        approvals = (await session.execute(text("""
            SELECT a.approval_uuid::text
              FROM agent_approvals a
              JOIN agent_goals g
                ON g.id=a.goal_id AND g.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant AND g.created_by=:user
               AND a.status='pending'
               AND a.approval_type='confirmed_sales_data_required'
             ORDER BY a.id
        """), {"tenant": tenant_id, "user": user_id})).scalars().all()
        dispatches = []
        for approval_uuid in approvals:
            _, dispatch = await goal_service.respond_approval(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                approval_uuid=str(approval_uuid),
                selected_option="continue",
                user_input=None,
            )
            if dispatch:
                dispatches.append(dispatch)
                resumed += 1
        await session.commit()
    for dispatch in dispatches:
        async with database.session_factory() as session:
            await AgentSupervisor(settings).execute(session, **dispatch)

    async with database.session_factory() as session:
        objective = "复盘最近四周 HeFeng 美国站销量并检查当前预测状态"
        draft = await goal_service.draft(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            body=GoalDraftRequest(
                objective=objective,
                preferred_skill_id="weekly_sales_review",
            ),
        )
        goal = await goal_service.create(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            idempotency_key="operational-baseline-weekly-review-v1",
            body=GoalCreateRequest(
                objective=objective,
                selected_skill_id=draft["selected_skill_id"],
                expected_deliverables=draft["expected_deliverables"],
                constraints=draft["constraints"],
                acceptance_criteria=draft["acceptance_criteria"],
                autonomy_envelope=draft["autonomy_envelope"],
                priority="normal",
                trigger_type="user_delegate",
                source_mode="employee",
                return_href="forecast?tab=history",
            ),
        )
        _, dispatch = await goal_service.start(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            goal_uuid=str(goal["goal_uuid"]),
        )
        await session.commit()
    if dispatch:
        async with database.session_factory() as session:
            await AgentSupervisor(settings).execute(session, **dispatch)
        created = 1
    return {"goals_created": created, "goals_resumed": resumed}


async def summary(database: Database, tenant_id: int) -> dict[str, int]:
    async with database.session_factory() as session:
        row = (await session.execute(text("""
            SELECT
              (SELECT count(*) FROM reviews
                WHERE tenant_id=:tenant AND is_valid)::int valid_reviews,
              (SELECT count(*) FROM market_opportunities
                WHERE tenant_id=:tenant)::int opportunities,
              (SELECT count(*) FROM opportunity_feedback_events
                WHERE tenant_id=:tenant)::int feedback_events,
              (SELECT count(*) FROM opportunity_outcome_events
                WHERE tenant_id=:tenant)::int outcome_events,
              (SELECT count(*) FROM forecast_model_deployments
                WHERE tenant_id=:tenant AND status='active')::int active_deployments,
              (SELECT count(*) FROM forecast_jobs
                WHERE tenant_id=:tenant AND status='succeeded')::int forecast_results,
              (SELECT count(*) FROM customer_memories
                WHERE tenant_id=:tenant AND status='confirmed')::int memories,
              (SELECT count(*) FROM agent_triggers
                WHERE tenant_id=:tenant)::int triggers
        """), {"tenant": tenant_id})).mappings().one()
        return {key: int(value or 0) for key, value in row.items()}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="hefeng@furniscope.local")
    parser.add_argument("--skip-forecast", action="store_true")
    args = parser.parse_args()
    database = Database(get_settings())
    try:
        async with database.session_factory() as session:
            identity = await actor(session, args.email)
            await ensure_dataset(
                session,
                tenant_id=int(identity["tenant_id"]),
                user_id=int(identity["user_id"]),
            )
            await session.commit()
        await ensure_enterprise_profile(
            database,
            tenant_id=int(identity["tenant_id"]),
            user_id=int(identity["user_id"]),
        )
        await apply_package(
            database,
            package_path=DEFAULT_PACKAGE,
            email=args.email,
        )
        await seed_notification_demo(database, email=args.email)
        await ensure_decision_history(
            database,
            tenant_id=int(identity["tenant_id"]),
            user_id=int(identity["user_id"]),
        )
        await ensure_competitor_history(
            database,
            tenant_id=int(identity["tenant_id"]),
        )
        await ensure_operational_outcomes(
            database,
            tenant_id=int(identity["tenant_id"]),
            user_id=int(identity["user_id"]),
        )
        await ensure_memories_and_triggers(
            database,
            tenant_id=int(identity["tenant_id"]),
            user_id=int(identity["user_id"]),
        )
        if not args.skip_forecast:
            await ensure_forecast_deployment(
                database,
                tenant_id=int(identity["tenant_id"]),
                user_id=int(identity["user_id"]),
            )
            await ensure_forecasts(
                database,
                tenant_id=int(identity["tenant_id"]),
                user_id=int(identity["user_id"]),
            )
        print(json.dumps(
            await summary(database, int(identity["tenant_id"])),
            ensure_ascii=False,
        ))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
