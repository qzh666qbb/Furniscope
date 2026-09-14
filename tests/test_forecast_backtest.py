from __future__ import annotations

import pandas as pd
import pytest

from scripts.run_forecast_backtest import metric_row, safe_features


def test_metric_row_reports_accuracy_bias_and_missing_prediction_coverage() -> None:
    frame = pd.DataFrame(
        {
            "weekly_sales": [10.0, 0.0, 20.0, 5.0],
            "prediction": [8.0, 2.0, 24.0, None],
        }
    )

    result = metric_row(frame, "prediction", "candidate")

    assert result["observations"] == 3
    assert result["coverage_pct"] == pytest.approx(75.0)
    assert result["wape_pct"] == pytest.approx(26.6666667)
    assert result["bias_pct"] == pytest.approx(13.3333333)


def test_safe_features_uses_only_prior_rows_for_sku_statistics() -> None:
    frame = pd.DataFrame(
        {
            "sku_site": ["sku-1-us"] * 3,
            "date": pd.to_datetime(["2026-01-05", "2026-01-12", "2026-01-19"]),
            "weekly_sales": [10.0, 20.0, 1000.0],
            "avg_price": [100.0, 120.0, 999.0],
            "price_lag1": [None, 100.0, 120.0],
            "inventory": [50.0, 40.0, 30.0],
            "roll_4w_mean": [10.0, 15.0, 20.0],
            "sku_site_id": [1, 1, 1],
        }
    )

    result = safe_features(frame, pd.Timestamp("2026-01-19"))

    assert pd.isna(result.loc[0, "sku_mean"])
    assert result.loc[1, "sku_mean"] == pytest.approx(10.0)
    assert result.loc[2, "sku_mean"] == pytest.approx(15.0)
    assert result.loc[2, "sku_price"] == pytest.approx(110.0)
    assert result.loc[2, "inventory"] == pytest.approx(40.0)
