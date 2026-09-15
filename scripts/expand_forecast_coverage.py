"""Create real forecast jobs for HeFeng SKUs that still lack persisted results."""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import date

from sqlalchemy import text

from backend.furniscope_api.config import ApiSettings
from backend.furniscope_api.database import Database
from backend.furniscope_api.services.forecast_runtime import TenantForecastRuntimeRegistry
from backend.furniscope_api.services.forecast_service import ForecastService


async def expand(tenant_id: int, batch_size: int) -> None:
    settings = ApiSettings()
    database = Database(settings)
    service = ForecastService(settings, TenantForecastRuntimeRegistry(settings))
    try:
        async with database.session_factory() as session:
            user_id = int((await session.execute(text("""
                SELECT id FROM users
                 WHERE tenant_id=:tenant_id AND status='active'
                 ORDER BY id LIMIT 1
            """), {"tenant_id": tenant_id})).scalar_one())
            missing = (await session.execute(text("""
                SELECT c.sku, c.site
                  FROM tenant_sku_catalog c
                 WHERE c.tenant_id=:tenant_id
                   AND c.lifecycle_status='active'
                   AND c.model_eligible
                   AND NOT EXISTS (
                     SELECT 1
                       FROM forecast_results r
                       JOIN forecast_jobs j ON j.id=r.job_id AND j.tenant_id=c.tenant_id
                      WHERE r.sku=c.sku AND r.site=c.site AND j.status='succeeded'
                   )
                 ORDER BY c.site, c.history_weeks DESC, c.sku
            """), {"tenant_id": tenant_id})).mappings().all()
            if not missing:
                print(f"tenant {tenant_id} already has results for every eligible SKU/site")
                return
            grouped: dict[str, list[str]] = defaultdict(list)
            for row in missing:
                grouped[str(row["site"])].append(str(row["sku"]))
            created = 0
            for site, skus in grouped.items():
                for offset in range(0, len(skus), batch_size):
                    batch = skus[offset:offset + batch_size]
                    payload = {
                        "job_name": f"{site} 覆盖补齐 {len(batch)} SKU",
                        "granularity": "week",
                        "horizon": 4,
                        "start_date": date(2026, 7, 6).isoformat(),
                        "skus": batch,
                        "sites": [site],
                        "scenario": {},
                        "product_id": None,
                        "analysis_task_uuid": None,
                    }
                    suffix = f"coverage-{tenant_id}-{site}-{offset}"
                    _, job = await service.create(
                        session, tenant_id=tenant_id, user_id=user_id,
                        idempotency_key=f"coverage-create-{suffix}", payload=payload,
                        response_envelope=lambda data: data,
                    )
                    await session.commit()
                    _, _, dispatch = await service.start(
                        session, tenant_id=tenant_id, user_id=user_id,
                        job_uuid=job["job_uuid"], idempotency_key=f"coverage-start-{suffix}",
                        response_envelope=lambda data: data,
                    )
                    await session.commit()
                    if dispatch is None:
                        raise RuntimeError(f"forecast job {job['job_uuid']} was not dispatched")
                    await service.execute(session, **dispatch)
                    created += 1
                    print(f"completed {site} {offset + 1}-{offset + len(batch)}/{len(skus)}")
            remaining = int((await session.execute(text("""
                SELECT count(*)
                  FROM tenant_sku_catalog c
                 WHERE c.tenant_id=:tenant_id AND c.model_eligible
                   AND NOT EXISTS (
                     SELECT 1 FROM forecast_results r
                       JOIN forecast_jobs j ON j.id=r.job_id AND j.tenant_id=c.tenant_id
                      WHERE r.sku=c.sku AND r.site=c.site AND j.status='succeeded')
            """), {"tenant_id": tenant_id})).scalar_one())
            covered = int((await session.execute(text("""
                SELECT count(DISTINCT r.sku)
                  FROM forecast_results r
                  JOIN forecast_jobs j ON j.id=r.job_id
                 WHERE j.tenant_id=:tenant_id AND j.status='succeeded'
            """), {"tenant_id": tenant_id})).scalar_one())
            print(json_summary(created, covered, remaining))
    finally:
        await database.close()


def json_summary(jobs: int, covered_skus: int, remaining_pairs: int) -> str:
    import json
    return json.dumps({
        "jobs_created": jobs,
        "covered_skus": covered_skus,
        "remaining_pairs": remaining_pairs,
    }, ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=50)
    args = parser.parse_args()
    asyncio.run(expand(args.tenant_id, args.batch_size))


if __name__ == "__main__":
    main()
