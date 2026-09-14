"""Populate one tenant's forecast history with actual persisted model results.

This helper is intentionally idempotent: it only creates enough completed jobs
to reach the requested tenant total and executes every job through the same
ForecastService/runtime path used by the HTTP API.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from sqlalchemy import text

from backend.furniscope_api.config import ApiSettings
from backend.furniscope_api.database import Database
from backend.furniscope_api.services.forecast_runtime import TenantForecastRuntimeRegistry
from backend.furniscope_api.services.forecast_service import ForecastService


async def seed(tenant_id: int, target_total: int) -> None:
    settings = ApiSettings()
    database = Database(settings)
    service = ForecastService(settings, TenantForecastRuntimeRegistry(settings))
    try:
        async with database.session_factory() as session:
            current = int((await session.execute(
                text("SELECT count(*) FROM forecast_jobs WHERE tenant_id=:tenant_id"),
                {"tenant_id": tenant_id},
            )).scalar_one())
            needed = max(0, target_total - current)
            if not needed:
                print(f"tenant {tenant_id} already has {current} forecast jobs")
                return

            user_id = (await session.execute(text("""
                SELECT id FROM users
                 WHERE tenant_id=:tenant_id AND status='active'
                 ORDER BY id LIMIT 1
            """), {"tenant_id": tenant_id})).scalar_one()
            candidates = (await session.execute(text("""
                SELECT sku,site FROM tenant_sku_catalog
                 WHERE tenant_id=:tenant_id AND lifecycle_status='active' AND model_eligible
                 ORDER BY history_weeks DESC,sku,site
                 LIMIT :limit
            """), {"tenant_id": tenant_id, "limit": needed})).mappings().all()
            if len(candidates) < needed:
                raise RuntimeError(f"only {len(candidates)} eligible SKU/site pairs are available")

            for index, candidate in enumerate(candidates, start=1):
                granularity = "week" if index % 3 == 0 else "day"
                horizon = 4 + index % 5 if granularity == "week" else 7 + index % 8
                start_date = date(2026, 7, 1) + timedelta(days=index % 21)
                sku, site = str(candidate["sku"]), str(candidate["site"])
                payload = {
                    "job_name": f"{sku} · {site} 销量预测",
                    "granularity": granularity,
                    "horizon": horizon,
                    "start_date": start_date.isoformat(),
                    "skus": [sku],
                    "sites": [site],
                    "scenario": {},
                    "product_id": None,
                    "analysis_task_uuid": None,
                }
                suffix = f"real-history-{tenant_id}-{current + index}"
                _, created = await service.create(
                    session, tenant_id=tenant_id, user_id=int(user_id),
                    idempotency_key=f"seed-create-{suffix}", payload=payload,
                    response_envelope=lambda data: data,
                )
                await session.commit()
                _, _, dispatch = await service.start(
                    session, tenant_id=tenant_id, user_id=int(user_id),
                    job_uuid=created["job_uuid"], idempotency_key=f"seed-start-{suffix}",
                    response_envelope=lambda data: data,
                )
                await session.commit()
                if dispatch is None:
                    raise RuntimeError(f"forecast job {created['job_uuid']} was not dispatched")
                await service.execute(session, **dispatch)

                # Make the imported history useful to browse while preserving the
                # actual model output produced above.
                age_days = needed - index
                await session.execute(text("""
                    UPDATE forecast_jobs
                       SET created_at=created_at-(:age_days * interval '1 day'),
                           started_at=started_at-(:age_days * interval '1 day'),
                           completed_at=completed_at-(:age_days * interval '1 day'),
                           updated_at=updated_at-(:age_days * interval '1 day')
                     WHERE tenant_id=:tenant_id AND job_uuid=CAST(:job_uuid AS uuid)
                """), {"tenant_id": tenant_id, "job_uuid": created["job_uuid"],
                         "age_days": age_days})
                await session.commit()
                print(f"completed {current + index}/{target_total}: {sku} {site} {granularity}")
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", type=int, default=1)
    parser.add_argument("--target-total", type=int, default=28)
    args = parser.parse_args()
    asyncio.run(seed(args.tenant_id, args.target_total))


if __name__ == "__main__":
    main()
