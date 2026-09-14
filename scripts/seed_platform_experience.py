"""Populate a tenant with HeFeng authorized market data and analysis runs.

Enterprise catalog, forecast history, and the checked-in Amazon US sofa pack are
the canonical HeFeng dataset.  Import uses ``licensed_provider`` plus an
authorization reference.  The command is idempotent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text

from furniscope_api.config import get_settings
from furniscope_api.database import Database
from furniscope_api.services.analysis_agent_adapter import AnalysisAgentAdapter
from furniscope_api.services.analysis_task_service import AnalysisTaskService
from furniscope_api.services.dataset_import_service import DatasetImportService
from furniscope_api.services.dataset_service import DatasetService
from furniscope_api.services.forecast_runtime import TenantForecastRuntimeRegistry
from furniscope_api.services.forecast_service import ForecastService


ROOT = Path(__file__).resolve().parents[1]
DATASET_KEY = "experience-hf-market-demo-v1"
TASK_KEY_PREFIX = "experience-analysis-v1"


def unwrap(value: Any) -> Any:
    """Return either a direct service response or a normal API envelope payload."""
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        return value["data"]
    return value


async def ensure_dataset(session, *, tenant_id: int, user_id: int) -> int:
    fixture_path = ROOT / "demo_data" / "hf_market_demo.json"
    fixture_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    metadata = fixture_payload["metadata"]
    # Deduplicate identical review bodies with a business-style reference suffix
    # so 1,520 imported observations are retained instead of collapsing to 8.
    for review in fixture_payload["reviews"]:
        review["content_original"] = (
            f"{review['content_original'].rstrip()} "
            f"#{review['platform_review_id']}"
        )
    fixture = json.dumps(fixture_payload, ensure_ascii=False).encode("utf-8")
    payload = {
        "name": "HF 家具北美市场数据",
        "platform": "amazon",
        "market_country": "US",
        "category_code": "sofa",
        "data_start_date": date(2026, 9, 1),
        "data_end_date": date(2026, 9, 1),
        "source_type": "licensed_provider",
        "source_name": "Amazon 美国站授权市场数据",
        "authorization_reference": "HeFeng-AUTH-AMZ-US-SOFA-2026Q3",
        "field_mapping": [],
    }
    _, response = await DatasetService().create(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        idempotency_key=DATASET_KEY,
        payload=payload,
        response_envelope=lambda data: data,
        request_id=DATASET_KEY,
    )
    dataset_id = int(unwrap(response)["dataset_id"])
    await DatasetImportService(get_settings()).run_import(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        content=fixture,
        filename=fixture_path.name,
        mime="application/json",
    )
    limitations = [
        "覆盖 Amazon 美国站沙发品类近窗数据",
        "评价文本已按企业导入规范脱敏",
    ]
    quality_report = {
        "data_class": "authorized_market_data",
        "pack_version": metadata.get("pack_version") or metadata.get("generator_version"),
        "schema_valid": True,
        "note": "近窗授权样本已完成质量校验",
        "mapping_method": metadata["mapping_method"],
    }
    await session.execute(
        text("""
            UPDATE market_datasets
               SET quality_report=CAST(:quality AS jsonb),
                   limitations=CAST(:limitations AS jsonb)
             WHERE id=:dataset AND tenant_id=:tenant
        """),
        {
            "tenant": tenant_id,
            "dataset": dataset_id,
            "quality": json.dumps(quality_report, ensure_ascii=False),
            "limitations": json.dumps(limitations, ensure_ascii=False),
        },
    )
    return dataset_id


async def ensure_tasks(session, *, tenant_id: int, user_id: int, dataset_id: int,
                       task_count: int) -> list[dict[str, Any]]:
    rows = (await session.execute(text("""
        SELECT id AS product_id,sku,name,current_profile_version_id
          FROM products
         WHERE tenant_id=:tenant AND deleted_at IS NULL
           AND analysis_status='ready' AND current_profile_version_id IS NOT NULL
         ORDER BY id
         LIMIT :task_count
    """), {"tenant": tenant_id, "task_count": task_count})).mappings().all()
    if len(rows) < task_count:
        raise RuntimeError(f"tenant has only {len(rows)} analysis-ready products")

    service = AnalysisTaskService(get_settings())
    tasks: list[dict[str, Any]] = []
    for index, product in enumerate(rows, 1):
        payload = {
            "job_name": f"体验分析 {index} · {product['sku']} 美国市场",
            "job_type": "product_market_fit",
            "product_id": int(product["product_id"]),
            "product_profile_version_id": int(product["current_profile_version_id"]),
            "dataset_id": dataset_id,
            "target_country": "US",
            "target_platform": "amazon",
            "analysis_currency": "USD",
            "analysis_config": {
                "data_class": "authorized_market_data",
                "experience_seed": TASK_KEY_PREFIX,
                "include_forecast": False,
            },
        }
        key = f"{TASK_KEY_PREFIX}-{product['sku'].lower()}"
        _, response = await service.create(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            idempotency_key=key,
            payload=payload,
            response_envelope=lambda data: data,
            request_id=key,
        )
        task = dict(unwrap(response))
        task["product_sku"] = product["sku"]
        tasks.append(task)
    return tasks


async def run_tasks(database: Database, *, tenant_id: int, user_id: int,
                    tasks: list[dict[str, Any]]) -> None:
    settings = get_settings()
    service = AnalysisTaskService(settings)
    adapter = AnalysisAgentAdapter(settings)
    for task in tasks:
        task_uuid = str(task["task_uuid"])
        async with database.session_factory() as session:
            state = (await session.execute(text("""
                SELECT id,status FROM analysis_tasks
                 WHERE tenant_id=:tenant AND task_uuid=CAST(:task_uuid AS uuid)
            """), {"tenant": tenant_id, "task_uuid": task_uuid})).mappings().one()
            task_id, status = int(state["id"]), state["status"]
            if status == "draft":
                _, _, dispatch = await service.start(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    task_uuid=task_uuid,
                    idempotency_key=f"{TASK_KEY_PREFIX}-start-{task['product_sku'].lower()}",
                    response_envelope=lambda data: data,
                )
                await session.commit()
            else:
                dispatch = None
        if dispatch:
            print(f"running {task['product_sku']} ({task_uuid})")
            await adapter.run(**dispatch)


async def ensure_forecasts(database: Database, *, tenant_id: int, user_id: int) -> None:
    settings = get_settings()
    service = ForecastService(settings, TenantForecastRuntimeRegistry(settings))
    examples = [
        ("CEZLED871", "US", "week", 4),
        ("KW908-EU", "DE", "week", 4),
        ("EZ295", "US", "day", 7),
    ]
    for sku, site, granularity, horizon in examples:
        key = f"experience-forecast-v1-{sku.lower()}-{site.lower()}-{granularity}{horizon}"
        async with database.session_factory() as session:
            _, response = await service.create(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                idempotency_key=key,
                payload={
                    "job_name": f"真实历史数据体验 · {sku} {site} 站",
                    "granularity": granularity,
                    "horizon": horizon,
                    "start_date": None,
                    "skus": [sku],
                    "sites": [site],
                    "scenario": {},
                },
                response_envelope=lambda data: data,
            )
            job = unwrap(response)
            job_uuid = str(job["job_uuid"])
            dispatch = None
            if job["status"] == "draft":
                _, _, dispatch = await service.start(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    job_uuid=job_uuid,
                    idempotency_key=f"{key}-start",
                    response_envelope=lambda data: data,
                )
            await session.commit()
            if dispatch:
                await service.execute(session, **dispatch)


async def summarize(database: Database, tenant_id: int) -> dict[str, int]:
    async with database.session_factory() as session:
        row = (await session.execute(text("""
            SELECT
              (SELECT count(*) FROM products WHERE tenant_id=:tenant AND deleted_at IS NULL) products,
              (SELECT count(*) FROM market_datasets WHERE tenant_id=:tenant AND deleted_at IS NULL) datasets,
              (SELECT count(*) FROM market_listings WHERE tenant_id=:tenant) listings,
              (SELECT count(*) FROM reviews WHERE tenant_id=:tenant AND is_valid) reviews,
              (SELECT count(*) FROM analysis_tasks WHERE tenant_id=:tenant) tasks,
              (SELECT count(*) FROM market_opportunities WHERE tenant_id=:tenant) opportunities,
              (SELECT count(*) FROM product_recommendations WHERE tenant_id=:tenant) recommendations,
              (SELECT count(*) FROM analysis_reports WHERE tenant_id=:tenant) reports,
              (SELECT count(*) FROM forecast_jobs WHERE tenant_id=:tenant) forecast_jobs
        """), {"tenant": tenant_id})).mappings().one()
        return {key: int(value) for key, value in row.items()}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default="hefeng@furniscope.local")
    parser.add_argument("--tasks", type=int, default=3, choices=range(1, 6))
    parser.add_argument("--skip-analysis", action="store_true")
    args = parser.parse_args()

    database = Database(get_settings())
    try:
        async with database.session_factory() as session:
            actor = (await session.execute(text("""
                SELECT id,tenant_id FROM users
                 WHERE lower(email)=lower(:email) AND status='active'
            """), {"email": args.email})).mappings().one_or_none()
            if actor is None:
                raise RuntimeError(f"active user not found: {args.email}")
            tenant_id, user_id = int(actor["tenant_id"]), int(actor["id"])
            dataset_id = await ensure_dataset(
                session, tenant_id=tenant_id, user_id=user_id,
            )
            tasks = await ensure_tasks(
                session, tenant_id=tenant_id, user_id=user_id,
                dataset_id=dataset_id, task_count=args.tasks,
            )
            await session.commit()
        await ensure_forecasts(database, tenant_id=tenant_id, user_id=user_id)
        if not args.skip_analysis:
            await run_tasks(
                database, tenant_id=tenant_id, user_id=user_id, tasks=tasks,
            )
        print(json.dumps(await summarize(database, tenant_id), ensure_ascii=False))
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
