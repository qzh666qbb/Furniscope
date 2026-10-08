"""Synthetic routing/causality checks, not real-enterprise accuracy evidence."""

import shutil
from datetime import date

import pytest

from furniscope_api.config import ApiSettings
from furniscope_api.schemas.forecasts import ForecastPairSummary
from furniscope_api.services.forecast_runtime import ForecastRuntime
from furniscope_forecast import tenant_engine
from test_enterprise_training import sales_records


def series(sku, days=196, quantity=10, start=date(2025, 1, 6)):
    return [{**row, "sku": sku} for row in sales_records(days, quantity, start)]


def test_mature_sparse_new_and_zero_share_one_artifact_without_fabricated_accuracy(tmp_path):
    sparse = series("SPARSE")
    for i, row in enumerate(sparse):
        row["sales"] = 70 if i % 7 == 0 else 0
    rows = (series("MATURE") + sparse + series("NEW", 3, 3, date(2025, 7, 18))
            + series("ZERO", quantity=0))
    result = tenant_engine.train_artifacts(rows, tmp_path, {})
    assert result["passed"], result["reasons"]
    day = {row["sku"]: row for row in result["day"]["series"]}
    assert day["SPARSE"]["segment"] == "sparse"
    assert day["SPARSE"]["method"] == "intermittent_mean"
    assert day["SPARSE"]["wape"] > 1
    assert day["SPARSE"]["validation_scope"] == "window_total"
    assert day["NEW"]["validation_status"] == day["ZERO"]["validation_status"] == "unvalidated"
    assert result["day"]["coverage"] == 0.5
    engine = tenant_engine.ForecastService(tmp_path)
    assert len(engine.list_skus()) == 4
    assert next(item for item in engine.list_skus() if item[0] == "NEW")[2] == 0
    for grain in ("day", "week"):
        new = engine.predict("NEW", "US", granularity=grain, days=7, weeks=1)
        assert new["weekly_total"] == 21
        assert new["confidence_interval"] == [None, None]
        assert new["reliability"] == "D"
        assert new["validation_status"] == "unvalidated"
    full = engine.predict("SPARSE", "US", days=28)
    assert full["weekly_total"] == 280
    assert full["validation_scope"] == "window_total"
    partial = engine.predict("SPARSE", "US", days=7)
    assert partial["confidence_interval"] == [None, None]
    assert partial["validation_status"] == "unvalidated"
    with pytest.raises(ValueError, match="基础日销量"):
        engine.predict("NO-HISTORY", "US")


def test_small_bad_sku_cannot_hide_under_a_large_good_sku(tmp_path):
    bad = series("SMALL", quantity=1)
    for row in bad[-28:]:
        row["sales"] = 100
    result = tenant_engine.train_artifacts(series("LARGE", quantity=100000) + bad, tmp_path, {})
    assert result["day"]["wape"] < 0.01  # Aggregate looks excellent.
    assert result["day"]["worst_sku_wape"] > 0.6
    assert not result["passed"]
    assert any("SMALL/US" in reason for reason in result["reasons"])
    assert not (tmp_path / "day_model.pkl").exists()


def test_each_sku_uses_its_own_cutoff_and_only_earlier_labels(tmp_path, monkeypatch):
    recorded, original = [], tenant_engine.fit

    def spy(frame, grain, encoders):
        assert frame.sku.nunique() == 1
        recorded.append((frame.sku.iloc[0], grain, frame.date.max().date().isoformat()))
        return original(frame, grain, encoders)

    monkeypatch.setattr(tenant_engine, "fit", spy)
    result = tenant_engine.train_artifacts(
        series("EARLY") + series("LATE", start=date(2026, 1, 5)), tmp_path, {})
    assert result["passed"], result["reasons"]
    for grain in ("day", "week"):
        for row in result[grain]["series"]:
            fits = [through for sku, g, through in recorded if sku == row["sku"] and g == grain]
            assert len(fits) == 4
            for through, fold in zip(fits[:3], row["folds"]):
                assert through == fold["train_through"] < fold["validation_from"]
            assert fits[-1] == row["folds"][-1]["validation_through"]


@pytest.mark.asyncio
async def test_runtime_exposes_unvalidated_baselines_and_reference_transfers(tmp_path):
    result = tenant_engine.train_artifacts(series("MATURE") + series("NEW", 3, 3), tmp_path, {})
    assert result["passed"]
    shutil.copyfile(tenant_engine.__file__, tmp_path / "forecast.py")
    cfg = ApiSettings(database_url="postgresql+asyncpg://localhost/test", app_env="test")
    runtime = ForecastRuntime(cfg, state_dir=tmp_path)
    for sku, scenario in [("NEW", {}), ("NEW", {"reference_sku": "MATURE"}),
                          ("MATURE", {"baseline": 2})]:
        output = await runtime.predict(dict(skus=[sku], sites=["US"], granularity="week",
                                            horizon=1, scenario_config=scenario))
        summary = ForecastPairSummary.model_validate(output["summaries"][0])
        assert summary.validation_status == "unvalidated"
        assert summary.lower is None and summary.upper is None
        assert all(p["lower"] is None and p["upper"] is None for p in output["points"])
        assert output["metrics"]["safety_stock"] is None
        assert output["metrics"]["recommended_production"] is None
    output = await runtime.predict(dict(skus=["MATURE"], sites=["US"], granularity="day",
                                        horizon=7, scenario_config={}))
    assert output["summaries"][0]["validation_status"] == "validated"
    assert output["metrics"]["recommended_production"] == 70
    extended = await runtime.predict(dict(skus=["MATURE"], sites=["US"], granularity="day",
                                          horizon=29, scenario_config={}))
    assert extended["summaries"][0]["reliability"] == "C"
    assert extended["metrics"]["safety_stock"] is None
