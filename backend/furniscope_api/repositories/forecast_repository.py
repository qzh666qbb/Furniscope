"""Tenant-scoped persistence for forecast jobs and immutable results."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ForecastRepository:
    async def active_deployment(self, session: AsyncSession, *, tenant_id: int) -> dict[str, Any] | None:
        result = await session.execute(text("""
            SELECT d.id AS deployment_id,d.deployment_uuid::text,d.route_policy,
                   m.id AS model_id,m.model_uuid::text,m.model_code,m.version,m.engine,
                   m.state_uri,m.state_checksum,m.status AS model_status,
                   m.owner_tenant_id,m.model_scope,m.training_data_through,m.metrics
              FROM forecast_model_deployments d
              JOIN forecast_models m ON m.id=d.model_id
             WHERE d.tenant_id=:tenant_id AND d.scenario_code='sales_forecast'
               AND d.status='active' AND m.status='active'
               AND ((m.model_scope='shared_base' AND m.owner_tenant_id IS NULL)
                    OR (m.model_scope='tenant_private' AND m.owner_tenant_id=:tenant_id))
             ORDER BY d.deployed_at DESC LIMIT 1
        """), {"tenant_id": tenant_id})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list_skus(self, session: AsyncSession, *, tenant_id: int,
                        site: str | None, limit: int) -> list[dict[str, Any]]:
        result = await session.execute(text("""
            SELECT * FROM (
                SELECT p.id AS product_id,p.sku,c.site,p.category_code,
                       'active' AS lifecycle_status,'product_center' AS label_status,
                       COALESCE(c.history_weeks,0) AS history_weeks,
                       COALESCE(c.model_eligible,false) AS model_eligible,
                       NULLIF(c.attributes->>'source_sku','') AS source_sku,
                       CASE
                         WHEN COALESCE(c.model_eligible,false) THEN 'v4'
                         ELSE 'baseline_required'
                       END AS forecast_strategy
                  FROM products p
                  JOIN tenant_sku_catalog c
                    ON c.tenant_id=p.tenant_id
                   AND upper(trim(c.sku))=upper(trim(p.sku))
                   AND c.lifecycle_status='active'
                 WHERE p.tenant_id=:tenant_id AND p.deleted_at IS NULL
                   AND (CAST(:site AS varchar) IS NULL OR c.site=:site)
                UNION ALL
                SELECT p.id AS product_id,p.sku,'US' AS site,p.category_code,
                       'active' AS lifecycle_status,'product_center' AS label_status,
                       0 AS history_weeks,false AS model_eligible,
                       NULL AS source_sku,'baseline_required' AS forecast_strategy
                  FROM products p
                 WHERE p.tenant_id=:tenant_id AND p.deleted_at IS NULL
                   AND (CAST(:site AS varchar) IS NULL OR :site='US')
                   AND NOT EXISTS (
                       SELECT 1 FROM tenant_sku_catalog c
                        WHERE c.tenant_id=p.tenant_id AND c.lifecycle_status='active'
                          AND upper(trim(c.sku))=upper(trim(p.sku))
                   )
            ) catalog_rows
             ORDER BY sku,site LIMIT :limit
        """), {"tenant_id": tenant_id, "site": site, "limit": limit})
        return [dict(row) for row in result.mappings()]

    async def eligible_sites_by_sku(self, session: AsyncSession, *, tenant_id: int,
                                    skus: list[str]) -> dict[str, set[str]]:
        result = await session.execute(text("""
            SELECT upper(trim(sku)) AS sku_key, site
              FROM tenant_sku_catalog
             WHERE tenant_id=:tenant_id AND lifecycle_status='active' AND model_eligible
               AND upper(trim(sku)) = ANY(
                   SELECT upper(trim(value)) FROM unnest(CAST(:skus AS text[])) AS value)
        """), {"tenant_id": tenant_id, "skus": skus})
        sites: dict[str, set[str]] = {}
        for row in result.mappings():
            sites.setdefault(row["sku_key"], set()).add(row["site"])
        return sites

    async def validate_sku_scope(self, session: AsyncSession, *, tenant_id: int,
                                 skus: list[str], sites: list[str]) -> list[dict[str, Any]]:
        result = await session.execute(text("""
            SELECT requested.sku,requested.site,p.id AS product_id,
                   CASE WHEN p.id IS NOT NULL THEN 'active' END AS lifecycle_status,
                   COALESCE(c.history_weeks,0) AS history_weeks,
                   COALESCE(c.model_eligible,false) AS model_eligible,
                   COALESCE(c.category_code,p.category_code) AS category_code,
                   NULLIF(c.attributes->>'source_sku','') AS source_sku
              FROM unnest(CAST(:skus AS text[])) requested_sku(sku)
              CROSS JOIN unnest(CAST(:sites AS text[])) requested_site(site)
              CROSS JOIN LATERAL (SELECT requested_sku.sku,requested_site.site) requested
              LEFT JOIN products p
                ON p.tenant_id=:tenant_id AND p.deleted_at IS NULL
               AND upper(trim(p.sku))=upper(trim(requested.sku))
              LEFT JOIN tenant_sku_catalog c
                ON c.tenant_id=:tenant_id AND c.site=requested.site
               AND upper(trim(c.sku))=upper(trim(requested.sku))
             ORDER BY requested.sku,requested.site
        """), {"tenant_id": tenant_id, "skus": skus, "sites": sites})
        return [dict(row) for row in result.mappings()]

    async def update_model_checksum(self, session: AsyncSession, *, model_id: int, checksum: str) -> None:
        await session.execute(text("""
            UPDATE forecast_models
               SET state_checksum=:checksum, updated_at=CURRENT_TIMESTAMP
             WHERE id=:model_id
        """), {"model_id": model_id, "checksum": checksum})

    async def replace_catalog_from_engine(self, session: AsyncSession, *, tenant_id: int,
                                          sku_rows: list[dict[str, Any]]) -> tuple[int, int]:
        """Keep only catalog rows that map onto this tenant's products; drop the rest."""
        aliases = (await session.execute(text("""
            SELECT product_sku, source_sku,
                   upper(trim(product_sku)) AS product_key, upper(trim(source_sku)) AS source_key
              FROM forecast_sku_aliases
        """))).mappings().all()
        source_to_product = {row["source_key"]: dict(row) for row in aliases}
        products = (await session.execute(text("""
            SELECT sku, upper(trim(sku)) AS sku_key, category_code
              FROM products WHERE tenant_id=:tenant_id AND deleted_at IS NULL
        """), {"tenant_id": tenant_id})).mappings().all()
        products_by_key = {row["sku_key"]: dict(row) for row in products}
        product_to_source = {row["product_key"]: row["source_sku"] for row in aliases}
        mapped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in sku_rows:
            engine_sku = str(row["sku"]).strip()
            site = str(row["site"]).strip().upper()
            product = products_by_key.get(engine_sku.upper())
            source_sku = engine_sku
            if product is None:
                alias = source_to_product.get(engine_sku.upper())
                product = products_by_key.get((alias or {}).get("product_key") or "")
                if product is None:
                    continue
                source_sku = alias["source_sku"]
            else:
                source_sku = product_to_source.get(product["sku_key"], engine_sku)
            key = (product["sku"], site)
            if key in seen:
                continue
            seen.add(key)
            mapped.append({
                "sku": product["sku"], "site": site,
                "weeks": int(row.get("history_weeks") or 0),
                "category_code": product["category_code"],
                "source_sku": source_sku,
            })
        await session.execute(text("DELETE FROM tenant_sku_catalog WHERE tenant_id=:tenant_id"),
                              {"tenant_id": tenant_id})
        for item in mapped:
            await session.execute(text("""
                INSERT INTO tenant_sku_catalog
                  (tenant_id,sku,site,category_code,lifecycle_status,label_status,
                   history_weeks,model_eligible,attributes)
                VALUES(:tenant_id,:sku,:site,:category_code,'active','complete',
                       :weeks,true,CAST(:attributes AS jsonb))
            """), {
                "tenant_id": tenant_id, "sku": item["sku"], "site": item["site"],
                "category_code": item["category_code"], "weeks": item["weeks"],
                "attributes": json.dumps({"source_sku": item["source_sku"]}),
            })
        return len({item["sku"] for item in mapped}), len(mapped)

    async def resolve_scope(self, session: AsyncSession, *, tenant_id: int,
                            product_id: int | None, analysis_task_uuid: str | None) -> dict[str, int | None] | None:
        result = await session.execute(text("""
            SELECT p.id AS product_id,a.id AS analysis_task_id
              FROM (SELECT 1) seed
              LEFT JOIN products p ON p.id=:product_id AND p.tenant_id=:tenant_id
              LEFT JOIN analysis_tasks a ON a.task_uuid=CAST(:task_uuid AS uuid)
                   AND a.tenant_id=:tenant_id
        """), {"tenant_id": tenant_id, "product_id": product_id,
                "task_uuid": analysis_task_uuid})
        row = result.mappings().one()
        if product_id is not None and row["product_id"] is None:
            return None
        if analysis_task_uuid is not None and row["analysis_task_id"] is None:
            return None
        return dict(row)

    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     idempotency_key: str, payload: dict[str, Any], input_hash: str,
                     scope: dict[str, int | None]) -> dict[str, Any]:
        result = await session.execute(text("""
            INSERT INTO forecast_jobs
              (tenant_id,created_by,product_id,analysis_task_id,job_name,granularity,
               horizon,start_date,skus,sites,scenario_config,input_hash,idempotency_key)
            VALUES
              (:tenant_id,:user_id,:product_id,:analysis_task_id,:job_name,:granularity,
               :horizon,CAST(CAST(:start_date AS varchar) AS date),CAST(:skus AS jsonb),CAST(:sites AS jsonb),
               CAST(:scenario AS jsonb),:input_hash,:idempotency_key)
            RETURNING id,job_uuid::text,job_name,status,granularity,horizon,skus,sites,
                      progress_percent,created_at,started_at,completed_at
        """), {
            "tenant_id": tenant_id, "user_id": user_id, **scope,
            "job_name": payload["job_name"], "granularity": payload["granularity"],
            "horizon": payload["horizon"], "start_date": payload.get("start_date"),
            "skus": json.dumps(payload["skus"]), "sites": json.dumps(payload["sites"]),
            "scenario": json.dumps(payload["scenario_config"]), "input_hash": input_hash,
            "idempotency_key": idempotency_key,
        })
        return dict(result.mappings().one())

    async def get(self, session: AsyncSession, *, tenant_id: int, job_uuid: str,
                  for_update: bool = False) -> dict[str, Any] | None:
        lock = " FOR UPDATE OF j" if for_update else ""
        result = await session.execute(text("""
            SELECT j.id,j.model_id,j.deployment_id,j.job_uuid::text,j.job_name,j.status,j.granularity,j.horizon,
                   j.start_date,j.skus,j.sites,j.scenario_config,j.input_hash,
                   j.progress_percent,j.failure_code,j.failure_message,j.created_at,
                   j.started_at,j.completed_at,m.version AS model_version,
                   m.model_uuid::text,m.state_uri,m.state_checksum,m.owner_tenant_id,m.model_scope
              FROM forecast_jobs j LEFT JOIN forecast_models m ON m.id=j.model_id
             WHERE j.tenant_id=:tenant_id AND j.job_uuid=CAST(:job_uuid AS uuid)
        """ + lock), {"tenant_id": tenant_id, "job_uuid": job_uuid})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list(self, session: AsyncSession, *, tenant_id: int, limit: int,
                   offset: int, q: str | None = None, status: str | None = None,
                   granularity: str | None = None) -> tuple[list[dict[str, Any]], int]:
        params = {"tenant_id": tenant_id, "limit": limit, "offset": offset,
                  "pattern": f"%{q.strip()}%" if q and q.strip() else None,
                  "status": status, "granularity": granularity}
        where = """
             WHERE j.tenant_id=:tenant_id
               AND NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(j.skus) requested(sku)
                      LEFT JOIN products p
                        ON p.tenant_id=j.tenant_id AND p.deleted_at IS NULL
                       AND upper(trim(p.sku))=upper(trim(requested.sku))
                     WHERE p.id IS NULL
               )
               AND (CAST(:pattern AS varchar) IS NULL OR j.job_name ILIKE :pattern
                    OR j.skus::text ILIKE :pattern OR j.sites::text ILIKE :pattern)
               AND (CAST(:status AS varchar) IS NULL OR j.status=:status)
               AND (CAST(:granularity AS varchar) IS NULL OR j.granularity=:granularity)
        """
        result = await session.execute(text("""
            SELECT j.job_uuid::text,j.job_name,j.status,j.granularity,j.horizon,j.skus,j.sites,
                   j.progress_percent,j.failure_code,j.failure_message,j.created_at,
                   j.started_at,j.completed_at,m.version AS model_version
              FROM forecast_jobs j LEFT JOIN forecast_models m ON m.id=j.model_id
        """ + where + " ORDER BY j.created_at DESC LIMIT :limit OFFSET :offset"), params)
        count = await session.execute(text("SELECT count(*) FROM forecast_jobs j" + where), params)
        return [dict(row) for row in result.mappings()], int(count.scalar_one())

    async def queue(self, session: AsyncSession, *, tenant_id: int, job_id: int,
                    model_id: int, deployment_id: int) -> None:
        await session.execute(text("""
            UPDATE forecast_jobs SET status='queued',model_id=:model_id,
                   deployment_id=:deployment_id,progress_percent=5,updated_at=now()
             WHERE id=:job_id AND tenant_id=:tenant_id AND status='draft'
        """), {"tenant_id": tenant_id, "job_id": job_id, "model_id": model_id,
                "deployment_id": deployment_id})

    async def begin_run(self, session: AsyncSession, *, tenant_id: int,
                        job: dict[str, Any]) -> tuple[int, str]:
        if job.get("model_id") is None:
            raise RuntimeError("Forecast job has no bound tenant deployment")
        model_id = int(job["model_id"])
        run = await session.execute(text("""
            INSERT INTO forecast_runs(tenant_id,job_id,model_id,deployment_id,input_hash)
            VALUES(:tenant_id,:job_id,:model_id,:deployment_id,:input_hash)
            RETURNING id,run_uuid::text
        """), {"tenant_id": tenant_id, "job_id": job["id"], "model_id": model_id,
                "deployment_id": job["deployment_id"], "input_hash": job["input_hash"]})
        row = run.mappings().one()
        await session.execute(text("""
            UPDATE forecast_jobs SET status='running',progress_percent=20,
                   started_at=COALESCE(started_at,now()),updated_at=now()
             WHERE id=:job_id AND tenant_id=:tenant_id
        """), {"tenant_id": tenant_id, "job_id": job["id"], "model_id": model_id})
        return int(row["id"]), str(row["run_uuid"])

    async def complete(self, session: AsyncSession, *, tenant_id: int, job: dict[str, Any],
                       run_id: int, output: dict[str, Any]) -> None:
        reliability = {(s["sku"], s["site"]): s["reliability"] for s in output["summaries"]}
        for point in output["points"]:
            start = date.fromisoformat(point["bucket_start"])
            end = start if job["granularity"] == "day" else start + timedelta(days=6)
            await session.execute(text("""
                INSERT INTO forecast_results
                  (tenant_id,job_id,run_id,sku,site,bucket_start,bucket_end,predicted_sales,
                   lower_bound,upper_bound,reliability)
                VALUES(:tenant_id,:job_id,:run_id,:sku,:site,:bucket_start,:bucket_end,
                       :predicted,:lower,:upper,:reliability)
            """), {"tenant_id": tenant_id, "job_id": job["id"], "run_id": run_id,
                    "sku": point["sku"], "site": point["site"], "bucket_start": start,
                    "bucket_end": end, "predicted": point["predicted_sales"],
                    "lower": point["lower"], "upper": point["upper"],
                    "reliability": reliability[(point["sku"], point["site"])]})
        metrics = {**output["metrics"], "summaries": output["summaries"]}
        params = {"tenant_id": tenant_id, "job_id": job["id"], "run_id": run_id,
                  "metrics": json.dumps(metrics)}
        await session.execute(text("""
            UPDATE forecast_runs SET status='succeeded',metrics=CAST(:metrics AS jsonb),completed_at=now()
             WHERE id=:run_id AND tenant_id=:tenant_id
        """), params)
        await session.execute(text("""
            UPDATE forecast_jobs SET status='succeeded',progress_percent=100,completed_at=now(),updated_at=now()
             WHERE id=:job_id AND tenant_id=:tenant_id
        """), params)

    async def fail(self, session: AsyncSession, *, tenant_id: int, job_id: int,
                   run_id: int | None, code: str, message: str) -> None:
        if run_id is not None:
            await session.execute(text("""
                UPDATE forecast_runs SET status='failed',error_code=:code,error_message=:message,
                       completed_at=now() WHERE id=:run_id AND tenant_id=:tenant_id
            """), {"tenant_id": tenant_id, "run_id": run_id, "code": code, "message": message})
        await session.execute(text("""
            UPDATE forecast_jobs SET status='failed',failure_code=:code,failure_message=:message,
                   completed_at=now(),updated_at=now() WHERE id=:job_id AND tenant_id=:tenant_id
        """), {"tenant_id": tenant_id, "job_id": job_id, "code": code, "message": message})

    async def result(self, session: AsyncSession, *, tenant_id: int,
                     job_uuid: str) -> dict[str, Any] | None:
        job = await self.get(session, tenant_id=tenant_id, job_uuid=job_uuid)
        if job is None:
            return None
        run_result = await session.execute(text("""
            SELECT r.id,r.run_uuid::text,r.metrics,m.version,m.engine,m.state_checksum,
                   m.training_data_through
              FROM forecast_runs r JOIN forecast_models m ON m.id=r.model_id
             WHERE r.tenant_id=:tenant_id AND r.job_id=:job_id AND r.status='succeeded'
             ORDER BY r.completed_at DESC LIMIT 1
        """), {"tenant_id": tenant_id, "job_id": job["id"]})
        run = run_result.mappings().one_or_none()
        if run is None:
            return {"job": job, "run": None, "points": []}
        points = await session.execute(text("""
            SELECT sku,site,bucket_start,bucket_end,predicted_sales::float8,
                   lower_bound::float8 AS lower,upper_bound::float8 AS upper,reliability
              FROM forecast_results
             WHERE tenant_id=:tenant_id AND job_id=:job_id AND run_id=:run_id
             ORDER BY bucket_start,sku,site
        """), {"tenant_id": tenant_id, "job_id": job["id"], "run_id": run["id"]})
        return {"job": job, "run": dict(run),
                "points": [dict(row) for row in points.mappings()]}
