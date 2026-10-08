"""SKU identity, publication atomicity and historical routing on real PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from furniscope_api.database import bind_tenant_session
from furniscope_api.errors import BusinessError
from furniscope_api.schemas.data_imports import SkuMappingSave
from furniscope_api.services.forecast_catalog import ForecastCatalog
from furniscope_api.services.forecast_publication import ForecastPublication
from furniscope_api.services.forecast_service import ForecastService
from test_training_recovery import needs_db, setup_run
from test_enterprise_training import training_registry_deployment


async def mapping(service, session, tenant, user, items):
    current = await service.configuration(session, tenant)
    return await service.save(session, tenant, user, SkuMappingSave(
        expected_revision=current["revision"], items=items))


def test_mapping_validation_error_names_problem_skus_and_next_action():
    preview = {
        "items": [],
        "unmapped": [{"source_sku": "EXT-A", "site": "US", "product_sku": "EXT-A"}],
        "conflicts": [{"source_sku": "EXT-B", "site": "DE", "reason": "指向同一产品"}],
        "can_publish": False,
    }
    with pytest.raises(BusinessError) as caught:
        ForecastCatalog.require_valid(preview)
    error = caught.value
    assert error.code == "FORECAST_SKU_MAPPING_REQUIRED"
    assert error.status_code == 422
    assert "EXT-A@US" in error.message and "EXT-B@DE" in error.message
    assert "产品中心创建同编码产品" in error.message
    assert error.details == [preview]


@needs_db
@pytest.mark.asyncio
async def test_mapping_preflight_is_explicit_unambiguous_and_tenant_scoped(tmp_path):
    _, db, training, tenant, run = await setup_run(tmp_path)
    service = ForecastCatalog()
    try:
        async with db.session_factory() as session:
            await bind_tenant_session(session, tenant)
            user = await session.scalar(text("SELECT created_by FROM forecast_training_runs WHERE training_uuid=CAST(:u AS uuid)"), {"u": run})
            rows = [{"sku": "external", "site": "US"}]
            preview = await service.preview(session, tenant, rows)
            assert len(preview["unmapped"]) == 1 and not preview["can_publish"]
            original = await service.configuration(session, tenant)
            configured = await mapping(service, session, tenant, user, [
                {"source_sku": "external", "product_sku": "SAME-SKU"}])
            assert configured["revision"] != original["revision"]
            with pytest.raises(BusinessError, match="已更新"):
                await service.save(session, tenant, user, SkuMappingSave(
                    expected_revision=original["revision"], items=[]))
            with pytest.raises(BusinessError, match="本企业"):
                await mapping(service, session, tenant, user, [
                    {"source_sku": "foreign", "product_sku": "NOT-OWNED"}])
            with pytest.raises(BusinessError, match="一一对应"):
                await mapping(service, session, tenant, user, [
                    {"source_sku": "a", "product_sku": "SAME-SKU"},
                    {"source_sku": "A", "product_sku": "same-sku"}])
            checked = await service.preview(session, tenant, rows)
            assert checked["can_publish"] and checked["items"][0]["sku"] == "SAME-SKU"
            conflict = await service.preview(session, tenant, rows + [{"sku": "SAME-SKU", "site": "US"}])
            assert not conflict["can_publish"] and len(conflict["conflicts"]) == 1
            # Explicit mapping has precedence even when a same-name product exists.
            await session.execute(text("""
                INSERT INTO products(tenant_id,sku,name,category_code)
                VALUES(:t,'external','另一个产品','sofa')
            """), {"t": tenant})
            checked = await service.preview(session, tenant, rows)
            assert checked["items"][0]["sku"] == "SAME-SKU"
            frozen = checked["items"]
            await session.execute(text("UPDATE products SET deleted_at=now() WHERE tenant_id=:t AND sku='SAME-SKU'"), {"t": tenant})
            assert not (await service.preview(session, tenant, rows, frozen=frozen))["can_publish"]
            await session.rollback()
    finally:
        await db.close()


@needs_db
@pytest.mark.asyncio
async def test_queued_prediction_and_rollback_keep_their_original_mapping(tmp_path):
    cfg, db, training, tenant, original_run = await setup_run(tmp_path)
    service = ForecastCatalog()
    forecasts = ForecastService(cfg, training.registry)
    try:
        async with db.session_factory() as session:
            await bind_tenant_session(session, tenant)
            run = (await session.execute(text("""
                SELECT created_by,source_snapshot FROM forecast_training_runs
                 WHERE training_uuid=CAST(:u AS uuid)
            """), {"u": original_run})).mappings().one()
            user, version = run["created_by"], run["source_snapshot"]["data_versions"][0]
            await session.execute(text("UPDATE forecast_training_runs SET status='cancelled' WHERE training_uuid=CAST(:u AS uuid)"), {"u": original_run})
            product = await session.scalar(text("""
                INSERT INTO products(tenant_id,sku,name,category_code)
                VALUES(:t,'ALT','映射产品','sofa') RETURNING id
            """), {"t": tenant})
            await mapping(service, session, tenant, user, [{"source_sku": "SAME-SKU", "product_sku": "ALT"}])
            _, initial = await training.create(session, tenant_id=tenant, user_id=user,
                version_uuid=version, mode="initial", allow_history_overwrite=False, idempotency_key=uuid4().hex)
            await session.commit()
            # Editing the mapping after queue must not change the first model's binding.
            await mapping(service, session, tenant, user, [])
            await session.commit()
            await training.execute(session, tenant_id=tenant, training_uuid=initial["training_uuid"])
            first = await training_registry_deployment(session, tenant)
            assert first["route_policy"]["catalog_snapshot"][0]["sku"] == "ALT"
            assert (await forecasts.repository.list_skus(session, tenant_id=tenant, site=None, limit=100))[0]["source_sku"] == "SAME-SKU"
            _, created = await forecasts.create(session, tenant_id=tenant, user_id=user,
                idempotency_key=uuid4().hex, payload={"job_name": "冻结映射预测", "product_id": product,
                    "skus": ["ALT"], "sites": ["US"], "granularity": "day", "horizon": 7, "scenario": {}},
                response_envelope=lambda data: data)
            job = created["job_uuid"]
            await forecasts.start(session, tenant_id=tenant, user_id=user, job_uuid=job,
                idempotency_key=uuid4().hex, response_envelope=lambda data: data)
            await session.commit()
            # Rebuild switches the same source series to the product with the same name.
            _, rebuilt = await training.create(session, tenant_id=tenant, user_id=user,
                version_uuid=version, mode="rebuild", allow_history_overwrite=False, idempotency_key=uuid4().hex)
            await session.commit()
            await training.execute(session, tenant_id=tenant, training_uuid=rebuilt["training_uuid"])
            second = await training_registry_deployment(session, tenant)
            assert second["route_policy"]["catalog_snapshot"][0]["sku"] == "SAME-SKU"
            await forecasts.execute(session, tenant_id=tenant, job_uuid=job)
            assert (await forecasts.result(session, tenant_id=tenant, job_uuid=job))["metrics"]["total_forecast"] == 70
            with pytest.raises(DBAPIError, match="routing is immutable"):
                await session.execute(text("UPDATE forecast_jobs SET routing_snapshot='{}' WHERE job_uuid=CAST(:u AS uuid)"), {"u": job})
            await session.rollback()
            # Corruption cannot switch either deployment or catalog.
            runtime = training.registry.resolve(tenant_id=tenant, deployment=first)
            path = runtime.state_dir / "meta.json"
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with pytest.raises(BusinessError, match="校验失败"):
                await ForecastPublication().rollback(session, training.registry, tenant_id=tenant,
                    deployment_uuid=first["deployment_uuid"], user_id=user)
            await session.rollback()
            assert (await training_registry_deployment(session, tenant))["model_id"] == second["model_id"]
            path.write_bytes(original)
            restored, _ = await ForecastPublication().rollback(session, training.registry, tenant_id=tenant,
                deployment_uuid=first["deployment_uuid"], user_id=user)
            await session.commit()
            assert restored["catalog_pairs"] == 1
            assert (await training_registry_deployment(session, tenant))["model_id"] == first["model_id"]
            rows = (await session.execute(text("SELECT sku,attributes FROM tenant_sku_catalog WHERE tenant_id=:t"), {"t": tenant})).mappings().all()
            assert len(rows) == 1 and rows[0]["sku"] == "ALT"
            assert rows[0]["attributes"]["source_sku"] == "SAME-SKU"
    finally:
        await db.close()
