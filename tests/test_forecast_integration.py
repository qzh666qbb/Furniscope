import json
import os
from datetime import date
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1/test")

from backend.furniscope_api.config import ApiSettings
from backend.furniscope_api.schemas.forecasts import ForecastJobCreateRequest
from backend.furniscope_api.services.forecast_runtime import (
    ForecastRuntime,
    TenantForecastRuntimeRegistry,
    coerce_training_date,
)
from backend.furniscope_api.services.forecast_model_replacement_service import ForecastModelReplacementService
from backend.furniscope_api.errors import BusinessError


def settings(engine_root: Path, state_dir: Path) -> ApiSettings:
    return ApiSettings(
        database_url="postgresql+asyncpg://test:test@127.0.0.1/test",
        app_env="test", forecast_engine_root=str(engine_root),
        forecast_state_dir=str(state_dir), forecast_artifact_root=str(state_dir.parent / "tenants"),
        forecast_model_version="test-v4",
    )


def build_fake_engine(tmp_path: Path) -> tuple[Path, Path]:
    engine_root, state_dir = tmp_path / "engine", tmp_path / "state"
    engine_root.mkdir(); state_dir.mkdir()
    (engine_root / "forecast.py").write_text("""
class ForecastService:
    def __init__(self, state_dir): pass
    def status(self):
        return {'initialized': True, 'day_model': True, 'week_model': True}
    def list_skus(self, site=None):
        return [['FS-320', site or 'US', 52]]
    def predict(self, sku, site, **kwargs):
        return {'weekly_total': 12.0, 'daily_avg': 6.0,
                'confidence_interval': (10.0, 15.0), 'reliability': 'A',
                'predictions': [
                    {'date': '2026-09-01', 'sales': 5.0, 'lower': 4.0, 'upper': 6.0},
                    {'date': '2026-09-02', 'sales': 7.0, 'lower': 6.0, 'upper': 9.0},
                ]}
""", encoding="utf-8")
    (state_dir / "meta.json").write_text(json.dumps(
        {"initialized": True, "last_date": "2026-06-30", "n_skus": 1}), encoding="utf-8")
    for name in ["daily.pkl", "weekly.pkl", "day_model.pkl", "week_model.pkl", "encoders.pkl"]:
        (state_dir / name).write_bytes(b"fixture")
    return engine_root, state_dir


@pytest.mark.asyncio
async def test_runtime_normalizes_existing_v4_engine(tmp_path: Path) -> None:
    engine_root, state_dir = build_fake_engine(tmp_path)
    runtime = ForecastRuntime(settings(engine_root, state_dir))
    assert runtime.metadata()["ready"] is True
    assert await runtime.list_skus("US", 1) == [
        {"sku": "FS-320", "site": "US", "history_weeks": 52}]
    output = await runtime.predict({
        "skus": ["FS-320"], "sites": ["US"], "granularity": "day",
        "horizon": 2, "start_date": None, "scenario_config": {},
    })
    assert output["metrics"]["total_forecast"] == 12.0
    assert output["metrics"]["safety_stock"] == 3
    assert len(output["points"]) == 2

    weekly_output = await runtime.predict({
        "skus": ["FS-320"], "sites": ["US"], "granularity": "week",
        "horizon": 2, "start_date": None, "scenario_config": {},
    })
    assert all(point["lower"] is not None and point["upper"] is not None
               for point in weekly_output["points"])
    assert sum(point["lower"] for point in weekly_output["points"]) == 10.0
    assert sum(point["upper"] for point in weekly_output["points"]) == 15.0


@pytest.mark.asyncio
async def test_runtime_prefers_tenant_engine_and_retrains_it(tmp_path: Path) -> None:
    engine_root, state_dir = build_fake_engine(tmp_path)
    (state_dir / "forecast.py").write_text("""
from pathlib import Path
class ForecastService:
    def __init__(self, state_dir): self.state_dir = Path(state_dir)
    def status(self):
        return {'initialized': True, 'day_model': True, 'week_model': True}
    def train(self, granularity=None):
        (self.state_dir / 'tenant-engine-trained').write_text('yes')
    def list_skus(self, site=None):
        return [['TENANT-SKU', site or 'US', 60]]
""", encoding="utf-8")
    (state_dir / "model_manifest.json").write_text(
        json.dumps({"algorithm": "lightgbm"}), encoding="utf-8")
    runtime = ForecastRuntime(settings(engine_root, state_dir))
    await runtime.retrain()
    assert (state_dir / "tenant-engine-trained").read_text() == "yes"
    assert runtime.metadata()["engine"] == "lightgbm"
    assert (await runtime.list_skus(None, 1))[0]["sku"] == "TENANT-SKU"


def test_model_replacement_rejects_invalid_parameter_document(tmp_path: Path) -> None:
    engine_root, state_dir = build_fake_engine(tmp_path)
    cfg = settings(engine_root, state_dir)
    service = ForecastModelReplacementService(
        cfg, TenantForecastRuntimeRegistry(cfg))
    with pytest.raises(BusinessError, match="JSON"):
        service._validate(
            code_filename="forecast.py",
            code_content=b"class ForecastService: pass",
            parameters_filename="params.json", parameters_content=b"[]")


def test_forecast_contract_normalizes_and_bounds_scope() -> None:
    request = ForecastJobCreateRequest(
        job_name="test", granularity="week", horizon=4,
        skus=[" FS-320 ", "FS-320"], sites=["us", "US"], scenario={})
    assert request.skus == ["FS-320"]
    assert request.sites == ["US"]
    with pytest.raises(ValueError):
        ForecastJobCreateRequest(job_name="bad", granularity="week", horizon=53,
                                 skus=["FS-320"], sites=["US"], scenario={})


def test_forecast_contract_accepts_complete_business_scenario() -> None:
    request = ForecastJobCreateRequest(
        job_name="promotion plan", granularity="day", horizon=14,
        start_date="2026-10-01", skus=["FS-320"], sites=["de", "FR"],
        scenario={
            "price": 499.9, "discount": 0.15, "inventory": 180,
            "is_promotion": True, "promotion_impact": 1.5,
            "baseline": 3.2, "reference_sku": "FS-REF",
        },
    )
    assert request.start_date == date(2026, 10, 1)
    assert request.sites == ["DE", "FR"]
    assert request.scenario.promotion_impact == 1.5
    assert request.scenario.reference_sku == "FS-REF"


def test_tenant_runtime_registry_rejects_cross_tenant_artifact(tmp_path: Path) -> None:
    engine_root, state_dir = build_fake_engine(tmp_path)
    registry = TenantForecastRuntimeRegistry(settings(engine_root, state_dir))
    deployment = {
        "model_uuid": "m1", "state_checksum": "checksum",
        "state_uri": "server-managed://tenant/22/model-a", "version": "v4",
        "model_scope": "tenant_private", "owner_tenant_id": 22,
    }
    with pytest.raises(RuntimeError, match="does not belong"):
        registry.resolve(tenant_id=21, deployment=deployment)


def test_tenant_runtime_registry_allows_explicit_shared_deployment(tmp_path: Path) -> None:
    engine_root, state_dir = build_fake_engine(tmp_path)
    registry = TenantForecastRuntimeRegistry(settings(engine_root, state_dir))
    runtime = registry.resolve(tenant_id=21, deployment={
        "model_uuid": "shared", "state_checksum": "checksum",
        "state_uri": "server-managed://default", "version": "v4",
        "model_scope": "shared_base", "owner_tenant_id": None,
    })
    assert runtime.metadata()["ready"] is True


@pytest.mark.asyncio
async def test_repository_real_model_assets_load_and_predict() -> None:
    """Regression for pandas/pickle compatibility using the delivered V4 assets."""
    project_root = Path(__file__).resolve().parents[1]
    runtime = ForecastRuntime(settings(
        project_root / "backend" / "furniscope_forecast",
        project_root / "forecast_assets" / "state",
    ))
    metadata = runtime.metadata()
    if not metadata.get("ready"):
        pytest.skip("forecast_assets/state is not present in CI")
    assert metadata["ready"] is True
    assert metadata["sku_count"] == 1204
    rows = await runtime.list_skus(None, 1)
    assert rows and rows[0]["sku"] and rows[0]["site"]
    output = await runtime.predict({
        "skus": [rows[0]["sku"]], "sites": [rows[0]["site"]],
        "granularity": "week", "horizon": 1, "start_date": None,
        "scenario_config": {},
    })
    assert output["metrics"]["pair_count"] == 1
    assert output["metrics"]["recommended_production"] >= 0
    assert len(output["points"]) == 1
