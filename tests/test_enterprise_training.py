"""Synthetic behavior checks, not evidence of real enterprise forecast accuracy."""

import io
import json
import os
import shutil
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from sqlalchemy import text

from furniscope_api.config import ApiSettings
from furniscope_api.database import Database, bind_tenant_session
from furniscope_api.errors import BusinessError
from furniscope_api.schemas.data_imports import ImportRules, MappingSuggestion
from furniscope_api.services.forecast_data_service import ForecastDataService
from furniscope_api.services.forecast_runtime import ForecastRuntime, TenantForecastRuntimeRegistry
from furniscope_api.services.forecast_training_service import ForecastTrainingService
from furniscope_api.services.sales_data_cleaner import clean_table, read_table, suggest_mapping
from furniscope_forecast import tenant_engine


def rules(**kwargs):
    return ImportRules(mapping={k: k for k in ("date", "sku", "site", "sales")}, **kwargs)


def sales_records(days=196, quantity=10, start=date(2025, 1, 6)):
    return [{"date": (start + timedelta(days=i)).isoformat(), "sku": "SAME-SKU",
             "site": "US", "sales": quantity} for i in range(days)]


@pytest.mark.parametrize("suffix", ["csv", "xlsx", "json"])
def test_file_formats_preserve_sales_and_row_lineage(suffix):
    frame = pd.DataFrame(sales_records(3))
    if suffix == "csv":
        content = frame.to_csv(index=False).encode()
    elif suffix == "json":
        content = frame.to_json(orient="records").encode()
    else:
        buffer = io.BytesIO()
        frame.to_excel(buffer, index=False)
        content = buffer.getvalue()
    result = clean_table(read_table(f"sales.{suffix}", content), rules())
    assert result["quality"]["trainable"]
    assert result["quality"]["input_quantity_total"] == 30
    assert result["quality"]["output_quantity_total"] == 30
    assert [r["source_rows"] for r in result["records"]] == [[2], [3], [4]]
    assert "sales" not in suggest_mapping(["sku", "date/time", "product sales"])


def test_ambiguous_dates_duplicates_net_sales_and_gaps_require_explicit_rules():
    frame = pd.DataFrame(sales_records(3))
    frame.loc[1, "date"] = "01/07/2025"
    assert clean_table(frame, rules())["quality"]["error_count"] == 1
    duplicated = pd.concat([frame.iloc[[0]], frame.iloc[[0]]], ignore_index=True)
    assert not clean_table(duplicated, rules())["quality"]["can_confirm"]
    summed = clean_table(duplicated, rules(grain="transactions"))
    assert summed["records"][0]["sales"] == 20
    assert summed["records"][0]["source_rows"] == [2, 3]
    assert summed["quality"]["quantity_reconciled"]
    gap = pd.DataFrame(sales_records(3)).iloc[[0, 2]]
    assert not clean_table(gap, rules())["quality"]["trainable"]
    with pytest.raises(ValueError, match="补零"):
        rules(missing_dates="zero")
    filled = clean_table(gap, rules(missing_dates="zero", complete_export_confirmed=True))
    assert filled["quality"]["trainable"] and filled["quality"]["filled_rows"] == 1
    assert filled["records"][1]["source_rows"] == []
    negative = pd.DataFrame(sales_records(2, -1))
    retained = clean_table(negative, rules(sales_basis="net_units"))
    assert retained["quality"]["can_confirm"] and not retained["quality"]["trainable"]
    assert retained["records"][0]["sales"] == -1


def test_independent_engine_gate_and_actual_inference(tmp_path):
    evaluation = tenant_engine.train_artifacts(sales_records(), tmp_path, {"tenant_id": 1})
    assert evaluation["passed"], evaluation
    for grain in ("day", "week"):
        for fold in evaluation[grain]["series"][0]["folds"]:
            assert fold["train_through"] < fold["validation_from"]
    engine = tenant_engine.ForecastService(tmp_path)
    assert engine.predict("SAME-SKU", "US", days=28)["weekly_total"] == pytest.approx(280)
    assert engine.predict("SAME-SKU", "US", granularity="week", weeks=4)["weekly_total"] == pytest.approx(280)
    assert engine.predict("NEW", "US", baseline=3, days=7)["weekly_total"] == 21
    with pytest.raises(ValueError, match="情景"):
        engine.predict("SAME-SKU", "US", price=100)


def test_no_validation_labels_reach_fitting_and_failures_do_not_write_models(tmp_path, monkeypatch):
    original = tenant_engine.fit
    fitted_through = []

    def spy(frame, grain, encoders):
        fitted_through.append((grain, frame.date.max()))
        return original(frame, grain, encoders)

    monkeypatch.setattr(tenant_engine, "fit", spy)
    records = sales_records()
    for row in records[-28:]:
        row["sales"] = 1000
    evaluation = tenant_engine.train_artifacts(records, tmp_path, {})
    assert not evaluation["passed"]
    assert len(fitted_through) == 6  # Three folds per grain; no rejected full-data refit.
    for grain in ("day", "week"):
        for (_, through), fold in zip([item for item in fitted_through if item[0] == grain],
                                       evaluation[grain]["series"][0]["folds"]):
            assert through.date().isoformat() < fold["validation_from"]
    assert not (tmp_path / "day_model.pkl").exists()
    short = tenant_engine.train_artifacts(sales_records(30), tmp_path, {})
    assert not short["passed"] and "历史不足" in short["reasons"][0]
    zero = tenant_engine.train_artifacts(sales_records(quantity=0), tmp_path, {})
    assert not zero["passed"] and zero["day"]["wape"] is None


def test_content_checksum_is_stable_when_copied_and_detects_changes(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    tenant_engine.train_artifacts(sales_records(), state, {})
    shutil.copyfile(tenant_engine.__file__, state / "forecast.py")
    cfg = ApiSettings(database_url="postgresql+asyncpg://localhost/test", app_env="test")
    original = ForecastRuntime(cfg, state_dir=state)
    digest = original.fingerprint()
    duplicate = tmp_path / "copy"
    shutil.copytree(state, duplicate)
    assert ForecastRuntime(cfg, state_dir=duplicate).fingerprint() == digest
    (state / "forecast.py").write_bytes((state / "forecast.py").read_bytes() + b"\n")
    assert original.fingerprint() != digest


def test_raw_duplicate_headers_and_overflow_are_quality_errors():
    with pytest.raises(BusinessError, match="表头重复"):
        read_table("sales.csv", b"date,sku,site,sales,sales\n2025-01-01,A,US,1,2")
    buffer = io.BytesIO()
    pd.DataFrame([["2025-01-01", "A", "US", 1, 2]],
                 columns=["date", "sku", "site", "sales", "sales"]).to_excel(buffer, index=False)
    with pytest.raises(BusinessError, match="表头重复"):
        read_table("sales.xlsx", buffer.getvalue())
    with pytest.raises(BusinessError, match="字段重复"):
        read_table("sales.json", b'[{"sales":1,"sales":2}]')
    result = clean_table(pd.DataFrame(sales_records(2, 1e308)), rules())
    assert result["quality"]["error_count"] == 1
    assert not result["quality"]["trainable"]
    json.dumps(result, allow_nan=False)


@pytest.mark.asyncio
async def test_mapping_assistant_only_sends_headers_and_never_applies_suggestions(tmp_path, monkeypatch):
    service = ForecastDataService(ApiSettings(database_url="postgresql+asyncpg://localhost/test",
                                              app_env="test", demo_storage_root=str(tmp_path)))
    async def source(*_args, **_kwargs):
        return {"columns_info": ["Date", "Stock Code", "Units Shipped"]}
    monkeypatch.setattr(service, "row", source)

    class Client:
        async def structured(self, *, messages, output_type):
            assert json.loads(messages[-1]["content"]) == {"columns": ["Date", "Stock Code", "Units Shipped"]}
            assert output_type is MappingSuggestion
            return MappingSuggestion(mapping={"sku": "Stock Code", "sales": "Units Shipped",
                                               "site": "invented column", "date": "Stock Code"})
    result = await service.mapping_suggestion(None, tenant_id=1, version_uuid="unused", model_client=Client())
    assert result["mapping"] == {"date": "Date", "sku": "Stock Code", "sales": "Units Shipped"}
    assert result["model_status"] == "suggested"
    assert not list(tmp_path.rglob("*"))  # Suggestions do not rewrite a version or its files.


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("FURNISCOPE_TEST_DATABASE_URL"), reason="needs isolated PostgreSQL")
async def test_restricted_training_gaps_tamper_cas_and_release_failure(tmp_path, monkeypatch):
    dsn = os.environ["FURNISCOPE_TEST_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    cfg = ApiSettings(database_url=dsn, app_env="test", demo_storage_root=str(tmp_path / "raw"),
                      forecast_artifact_root=str(tmp_path / "models"))
    db = Database(cfg)
    registry = TenantForecastRuntimeRegistry(cfg)
    data, training = ForecastDataService(cfg), ForecastTrainingService(cfg, registry)
    async with db.session_factory() as admin:
        tenant = await admin.scalar(text("""
            INSERT INTO tenants(tenant_code,name,status) VALUES(:code,'受限训练','active') RETURNING id
        """), {"code": f"RT_{uuid4().hex[:16].upper()}"})
        user = await admin.scalar(text("""
            INSERT INTO users(tenant_id,email,password_hash,name,role_code,status)
            VALUES(:t,:email,'not-a-login','测试','user','active') RETURNING id
        """), {"t": tenant, "email": f"{uuid4().hex}@example.invalid"})
        await admin.execute(text("""
            INSERT INTO products(tenant_id,created_by,sku,name,category_code)
            VALUES(:t,:u,'SAME-SKU','测试沙发','sofa')
        """), {"t": tenant, "u": user})
        await admin.commit()
    try:
        async with db.session_factory() as session:
            await bind_tenant_session(session, tenant)

            async def intake(records):
                item = await data.upload(session, tenant_id=tenant, user_id=user, filename="data.csv",
                                         content=pd.DataFrame(records).to_csv(index=False).encode())
                preview = await data.preflight(session, tenant_id=tenant,
                                               version_uuid=item["version_uuid"], rules=rules())
                return await data.confirm(session, tenant_id=tenant,
                                          version_uuid=item["version_uuid"],
                                          preview_sha256=preview["preview_sha256"])

            async def queue(item, mode="rebuild"):
                _, run = await training.create(session, tenant_id=tenant, user_id=user,
                    version_uuid=item["version_uuid"], mode=mode, allow_history_overwrite=False,
                    idempotency_key=uuid4().hex)
                await session.commit()
                return run["training_uuid"]

            item = await intake(sales_records())
            run = await queue(item, "initial")
            await training.execute(session, tenant_id=tenant, training_uuid=run)
            assert (await training.get(session, tenant_id=tenant, training_uuid=run))["status"] == "succeeded"
            good = await training_registry_deployment(session, tenant)
            changed = await data.revise(session, tenant_id=tenant, user_id=user,
                                        version_uuid=item["version_uuid"])
            assert changed["parent_version_id"] == item["id"]
            gap = await intake(sales_records(28, start=date(2025, 8, 1)))
            preview = await training.preview(session, tenant_id=tenant, version_uuid=gap["version_uuid"],
                                             mode="append")
            assert preview["missing_days"] == 11
            with pytest.raises(BusinessError, match="日期缺口"):
                await queue(gap, "append")

            run = await queue(item)
            # Frozen baseline CAS fails even if the replacement points to the SAME model.
            await session.execute(text("UPDATE forecast_model_deployments SET status='inactive' WHERE tenant_id=:t"),
                                  {"t": tenant})
            await session.execute(text("INSERT INTO forecast_model_deployments(tenant_id,model_id) VALUES(:t,:m)"),
                                  {"t": tenant, "m": good["model_id"]})
            await session.commit()
            newer = await training_registry_deployment(session, tenant)
            with pytest.raises(ValueError, match="部署已变化"):
                await training.execute(session, tenant_id=tenant, training_uuid=run)
            assert (await training_registry_deployment(session, tenant))["deployment_id"] == newer["deployment_id"]
            assert not (Path(cfg.forecast_artifact_root) / str(tenant) / run).exists()

            run = await queue(item)
            original_rename = Path.rename
            def failed_rename(path, target):
                if path.name.startswith(".staging-"):
                    raise OSError("injected disk failure")
                return original_rename(path, target)
            with monkeypatch.context() as patch:
                patch.setattr(Path, "rename", failed_rename)
                with pytest.raises(OSError, match="disk failure"):
                    await training.execute(session, tenant_id=tenant, training_uuid=run)
            assert (await training_registry_deployment(session, tenant))["deployment_id"] == newer["deployment_id"]
            assert not list((Path(cfg.forecast_artifact_root) / str(tenant)).glob(".staging-*"))

            run = await queue(item)
            raw = await data.row(session, tenant, item["version_uuid"])
            canonical = data.storage.root / raw["canonical_storage_key"]
            canonical.write_bytes(canonical.read_bytes() + b" ")
            with pytest.raises(BusinessError, match="校验失败"):
                await training.execute(session, tenant_id=tenant, training_uuid=run)
            assert (await training.get(session, tenant_id=tenant, training_uuid=run))["status"] == "failed"
            assert (await training_registry_deployment(session, tenant))["deployment_id"] == newer["deployment_id"]
    finally:
        await db.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.getenv("FURNISCOPE_TEST_DATABASE_URL"), reason="needs isolated PostgreSQL")
async def test_real_database_first_training_isolation_append_and_failed_release(tmp_path):
    dsn = os.environ["FURNISCOPE_TEST_DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    cfg = ApiSettings(database_url=dsn, app_env="test", demo_storage_root=str(tmp_path / "raw"),
                      forecast_artifact_root=str(tmp_path / "models"),
                      forecast_state_dir=str(tmp_path / "untrusted-shared"))
    database = Database(cfg)
    registry = TenantForecastRuntimeRegistry(cfg)
    data = ForecastDataService(cfg)
    training = ForecastTrainingService(cfg, registry)
    # A poisoned shared history must never be read by first/append training.
    shared = Path(cfg.forecast_state_dir)
    shared.mkdir()
    (shared / "daily.pkl").write_bytes(b"SHOULD NEVER BE LOADED")

    async with database.session_factory() as session:
        tenant_ids = []
        users = []
        for i in range(2):
            tenant = (await session.execute(text("""
                INSERT INTO tenants(tenant_code,name,status) VALUES(:code,'训练测试','active') RETURNING id
            """), {"code": f"ET_{uuid4().hex[:16].upper()}"})).scalar_one()
            user = (await session.execute(text("""
                INSERT INTO users(tenant_id,email,password_hash,name,role_code,status)
                VALUES(:tenant,:email,'not-a-login','测试','user','active') RETURNING id
            """), {"tenant": tenant, "email": f"{uuid4().hex}@example.invalid"})).scalar_one()
            await session.execute(text("""
                INSERT INTO products(tenant_id,created_by,sku,name,category_code)
                VALUES(:tenant,:user,'SAME-SKU','测试沙发','sofa')
            """), {"tenant": tenant, "user": user})
            tenant_ids.append(tenant)
            users.append(user)
        await session.commit()

        async def intake(index, records):
            item = await data.upload(session, tenant_id=tenant_ids[index], user_id=users[index],
                                     filename="sales.csv", content=pd.DataFrame(records).to_csv(index=False).encode())
            preview = await data.preflight(session, tenant_id=tenant_ids[index],
                                           version_uuid=item["version_uuid"], rules=rules())
            with pytest.raises(BusinessError, match="预检已变化"):
                await data.confirm(session, tenant_id=tenant_ids[index],
                                   version_uuid=item["version_uuid"], preview_sha256="0" * 64)
            confirmed = await data.confirm(session, tenant_id=tenant_ids[index],
                                           version_uuid=item["version_uuid"],
                                           preview_sha256=preview["preview_sha256"])
            await session.commit()
            return confirmed

        first = await intake(0, sales_records())
        second = await intake(1, sales_records(quantity=40))
        with pytest.raises(BusinessError, match="不可访问"):
            await data.row(session, tenant_ids[1], first["version_uuid"])
        for i, item in enumerate([first, second]):
            status, run = await training.create(
                session, tenant_id=tenant_ids[i], user_id=users[i],
                version_uuid=item["version_uuid"], mode="initial", allow_history_overwrite=False,
                idempotency_key=f"initial-{i}")
            assert status == 202
            await session.commit()
            await training.execute(session, tenant_id=tenant_ids[i], training_uuid=run["training_uuid"])
            result = await training.get(session, tenant_id=tenant_ids[i], training_uuid=run["training_uuid"])
            assert result["status"] == "succeeded", result
            deployment = await training_registry_deployment(session, tenant_ids[i])
            runtime = registry.resolve(tenant_id=tenant_ids[i], deployment=deployment)
            prediction = await runtime.predict(dict(skus=["SAME-SKU"], sites=["US"], granularity="day",
                                                   horizon=7, scenario_config={}))
            assert prediction["metrics"]["total_forecast"] == (70 if i == 0 else 280)
            manifest = json.loads((runtime.state_dir / "model_manifest.json").read_text())
            assert manifest["lineage"]["tenant_id"] == tenant_ids[i]
            assert len(manifest["lineage"]["sources"]) == 1

        original = await training_registry_deployment(session, tenant_ids[0])
        append_data = await intake(0, sales_records(28, 10, date(2025, 7, 21)))
        _, appended = await training.create(
            session, tenant_id=tenant_ids[0], user_id=users[0],
            version_uuid=append_data["version_uuid"], mode="append", allow_history_overwrite=False,
            idempotency_key="append")
        await session.commit()
        await training.execute(session, tenant_id=tenant_ids[0], training_uuid=appended["training_uuid"])
        append_result = await training.get(session, tenant_id=tenant_ids[0],
                                           training_uuid=appended["training_uuid"])
        assert append_result["status"] == "succeeded", append_result
        assert len(append_result["data_versions"]) == 2
        good = await training_registry_deployment(session, tenant_ids[0])
        assert good["model_id"] != original["model_id"]

        changed = await intake(0, sales_records(196, 1000))
        preview = await training.preview(session, tenant_id=tenant_ids[0],
                                         version_uuid=changed["version_uuid"], mode="append")
        assert preview["overwritten_rows"] == 196
        with pytest.raises(BusinessError, match="修改196"):
            await training.create(session, tenant_id=tenant_ids[0], user_id=users[0],
                                  version_uuid=changed["version_uuid"], mode="append",
                                  allow_history_overwrite=False, idempotency_key="overwrite")
        accepted_status, accepted = await training.create(
            session, tenant_id=tenant_ids[0], user_id=users[0],
            version_uuid=changed["version_uuid"], mode="append",
            allow_history_overwrite=True, idempotency_key="overwrite-confirmed")
        assert accepted_status == 202
        assert accepted["status"] == "queued"
        await session.rollback()
        insufficient = await intake(0, sales_records(10, 10))
        _, failed = await training.create(session, tenant_id=tenant_ids[0], user_id=users[0],
                                          version_uuid=insufficient["version_uuid"], mode="rebuild",
                                          allow_history_overwrite=False, idempotency_key="short")
        await session.commit()
        await training.execute(session, tenant_id=tenant_ids[0], training_uuid=failed["training_uuid"])
        assert (await training.get(session, tenant_id=tenant_ids[0],
                                   training_uuid=failed["training_uuid"]))["status"] == "rejected"
        assert (await training_registry_deployment(session, tenant_ids[0]))["model_id"] == good["model_id"]
    await database.close()


async def training_registry_deployment(session, tenant_id):
    from furniscope_api.repositories.forecast_repository import ForecastRepository
    return await ForecastRepository().active_deployment(session, tenant_id=tenant_id)
