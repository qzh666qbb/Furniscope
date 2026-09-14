"""Leakage-controlled, full-catalog weekly backtest for Sales Forecast V4.

The evaluation trains a fresh global ensemble strictly before the holdout window,
then scores each holdout week using only lagged/expanding history. It compares the
ensemble with a four-week moving average and a prior-year baseline.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from xgboost import XGBRegressor

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.furniscope_forecast.forecast import WEEK_FEATURES


def metric_row(frame: pd.DataFrame, prediction: str, model: str) -> dict:
    usable = frame[["weekly_sales", prediction]].dropna()
    actual = usable["weekly_sales"].to_numpy(float)
    predicted = np.clip(usable[prediction].to_numpy(float), 0, None)
    error = predicted - actual
    nonzero = actual > 0
    denominator = np.abs(actual).sum()
    return {
        "model": model,
        "observations": int(len(usable)),
        "actual_units": float(actual.sum()),
        "mae": float(np.abs(error).mean()) if len(error) else None,
        "rmse": float(np.sqrt(np.mean(error ** 2))) if len(error) else None,
        "wape_pct": float(np.abs(error).sum() / denominator * 100) if denominator else None,
        "mape_pct": float(np.mean(np.abs(error[nonzero]) / actual[nonzero]) * 100) if nonzero.any() else None,
        "smape_pct": float(np.mean(2 * np.abs(error) / np.maximum(np.abs(actual) + np.abs(predicted), .1)) * 100) if len(error) else None,
        "bias_pct": float(error.sum() / denominator * 100) if denominator else None,
        "coverage_pct": float(len(usable) / len(frame) * 100) if len(frame) else 0.0,
    }


def grouped_metrics(frame: pd.DataFrame, dimensions: list[str]) -> pd.DataFrame:
    rows = []
    for keys, group in frame.groupby(dimensions, observed=True, dropna=False):
        key_values = keys if isinstance(keys, tuple) else (keys,)
        context = dict(zip(dimensions, key_values, strict=True))
        for prediction, model in (
            ("ensemble_prediction", "sales_forecast_v4_retrained"),
            ("moving_average_4w", "moving_average_4w"),
            ("prior_year", "prior_year_same_week"),
        ):
            rows.append(context | metric_row(group, prediction, model))
    return pd.DataFrame(rows)


def safe_features(frame: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    result = frame.sort_values(["sku_site", "date"]).copy()
    groups = result.groupby("sku_site", observed=True, sort=False)
    result["sku_mean"] = groups["weekly_sales"].transform(lambda s: s.shift(1).expanding().mean())
    result["sku_std"] = groups["weekly_sales"].transform(lambda s: s.shift(1).expanding().std())
    result["sku_price"] = groups["avg_price"].transform(lambda s: s.shift(1).expanding().mean())
    result["avg_price"] = result["price_lag1"].fillna(result["sku_price"])
    result["price_relative"] = (result["avg_price"] / result["sku_price"].replace(0, np.nan)).clip(.5, 2).fillna(1)
    result["avg_discount"] = 0.0
    result["has_discount"] = 0.0
    prior_inventory = groups["inventory"].shift(1).fillna(0).clip(lower=0)
    result["inventory"] = prior_inventory
    result["in_stock"] = (prior_inventory > 0).astype(int)
    result["stock_weeks"] = (prior_inventory / result["roll_4w_mean"].replace(0, np.nan)).clip(0, 52).fillna(0)

    history = result[result["date"] < cutoff].groupby("sku_site", observed=True).size()
    known_pairs = set(history[history >= 20].index)
    result.loc[~result["sku_site"].isin(known_pairs), "sku_site_id"] = -1
    return result


def run(state_dir: Path, output_dir: Path, test_weeks: int) -> dict:
    source = state_dir / "weekly.pkl"
    with source.open("rb") as handle:
        weekly = pickle.load(handle)
    if not isinstance(weekly, pd.DataFrame):
        raise TypeError("weekly.pkl must contain a pandas DataFrame")
    required = {"sku", "site", "date", "weekly_sales", "sku_site", *WEEK_FEATURES}
    missing = sorted(required - set(weekly.columns))
    if missing:
        raise ValueError(f"weekly state is missing columns: {missing}")
    if weekly.duplicated(["sku", "site", "date"]).any():
        raise ValueError("weekly state violates sku/site/week uniqueness")
    if weekly["weekly_sales"].isna().any() or (weekly["weekly_sales"] < 0).any():
        raise ValueError("weekly_sales must be complete and non-negative")

    last_week = pd.Timestamp(weekly["date"].max())
    cutoff = last_week - pd.Timedelta(weeks=test_weeks - 1)
    featured = safe_features(weekly, cutoff)
    train = featured[featured["date"] < cutoff].copy()
    test = featured[(featured["date"] >= cutoff) & (featured["date"] <= last_week)].copy()
    history = train.groupby("sku_site", observed=True).size()
    eligible_pairs = history[history >= 20].index
    train = train[train["sku_site"].isin(eligible_pairs)]
    features = [name for name in WEEK_FEATURES if name in train and train[name].notna().mean() > .3]
    train = train.dropna(subset=[name for name in features if name.startswith("lag_")][:2]).sort_values("date")
    if train.empty or test.empty:
        raise ValueError("training or holdout window is empty")

    x_train = train[features].fillna(0)
    target = train["weekly_sales"]
    sample_count = len(train)
    weights = np.power(.998, np.arange(sample_count - 1, -1, -1))
    weights = weights / weights.sum() * sample_count
    xgb = XGBRegressor(
        n_estimators=500, max_depth=6, learning_rate=.03, subsample=.7,
        colsample_bytree=.7, min_child_weight=10, reg_alpha=1, reg_lambda=3,
        random_state=42, verbosity=0, n_jobs=-1,
    )
    lgbm = LGBMRegressor(
        n_estimators=500, max_depth=6, learning_rate=.03, subsample=.7,
        colsample_bytree=.7, min_child_weight=10, reg_alpha=1, reg_lambda=3,
        random_state=42, verbose=-1, n_jobs=-1,
    )
    xgb.fit(x_train, target, sample_weight=weights)
    lgbm.fit(x_train, target, sample_weight=weights)
    x_test = test[features].fillna(0)
    test["ensemble_prediction"] = np.clip(.5 * xgb.predict(x_test) + .5 * lgbm.predict(x_test), 0, None)
    test["moving_average_4w"] = test["roll_4w_mean"]
    test["prior_year"] = test["lag_52w"].where(test["lag_52w"].notna())

    sku_history = train.groupby("sku", observed=True)["weekly_sales"].agg(["mean", "size"])
    ranked = sku_history["mean"].rank(method="average", pct=True)
    sku_history["sales_tier"] = pd.cut(ranked, [0, 1/3, 2/3, 1], labels=["low", "medium", "high"], include_lowest=True)
    test = test.merge(sku_history[["size", "sales_tier"]].rename(columns={"size": "training_rows"}), left_on="sku", right_index=True, how="left")
    test["sales_tier"] = test["sales_tier"].astype("string").fillna("cold_start")
    test["training_rows"] = test["training_rows"].fillna(0).astype(int)

    overall = grouped_metrics(test.assign(scope="all"), ["scope"])
    by_tier = grouped_metrics(test, ["sales_tier"])
    by_site = grouped_metrics(test, ["site"])
    per_sku = grouped_metrics(test, ["sku"])
    detail_columns = [
        "date", "sku", "site", "sales_tier", "training_rows", "weekly_sales",
        "ensemble_prediction", "moving_average_4w", "prior_year",
    ]
    output_dir.mkdir(parents=True, exist_ok=True)
    test[detail_columns].to_csv(output_dir / "holdout_predictions.csv", index=False)
    by_tier.to_csv(output_dir / "metrics_by_sales_tier.csv", index=False)
    by_site.to_csv(output_dir / "metrics_by_site.csv", index=False)
    per_sku.to_csv(output_dir / "metrics_by_sku.csv", index=False)
    all_skus = set(weekly["sku"].astype(str).unique())
    scored_skus = set(test["sku"].astype(str).unique())
    catalog_coverage = (
        weekly.assign(sku=weekly["sku"].astype(str))
        .groupby("sku", observed=True)
        .agg(
            first_observed_week=("date", "min"),
            last_observed_week=("date", "max"),
            source_rows=("date", "size"),
            sites=("site", "nunique"),
        )
        .reset_index()
    )
    holdout_counts = test.assign(sku=test["sku"].astype(str)).groupby("sku", observed=True).size()
    catalog_coverage["holdout_observations"] = (
        catalog_coverage["sku"].map(holdout_counts).fillna(0).astype(int)
    )
    catalog_coverage["status"] = np.where(
        catalog_coverage["holdout_observations"] > 0, "scored", "unscored"
    )
    catalog_coverage["reason"] = np.where(
        catalog_coverage["holdout_observations"] > 0,
        "holdout_labels_available",
        "no_observations_in_holdout_window",
    )
    catalog_coverage.to_csv(output_dir / "catalog_coverage.csv", index=False)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "fixed-training-window plus rolling one-week-ahead holdout scoring",
        "holdout_start": str(cutoff.date()), "holdout_end": str(last_week.date()),
        "test_weeks": test_weeks, "features": features,
        "source": {"path": "forecast_assets/state/weekly.pkl", "sha256": source_sha,
                   "rows": int(len(weekly)), "sku_site_pairs": int(weekly["sku_site"].nunique())},
        "catalog_skus": len(all_skus), "scored_skus": len(scored_skus),
        "unscored_sku_count": len(all_skus - scored_skus),
        "catalog_coverage_pct": float(len(scored_skus) / len(all_skus) * 100),
        "unscored_reason_counts": {
            "no_observations_in_holdout_window": len(all_skus - scored_skus),
        },
        "holdout_observations": int(len(test)), "training_observations": int(len(train)),
        "overall": overall.replace({np.nan: None}).to_dict(orient="records"),
        "data_quality": {
            "duplicate_grain_rows": 0, "missing_target_rows": 0,
            "negative_target_rows": 0,
            "prior_year_coverage_pct": metric_row(test, "prior_year", "prior_year")["coverage_pct"],
        },
        "limitations": [
            "One-week-ahead holdout predictions use actual history from earlier holdout weeks; this is not an eight-week recursive forecast.",
            "Product category is absent from the serialized weekly state, so category-level metrics cannot be computed without a governed SKU-category dimension.",
            "Cold-start SKUs without holdout observations are reported as unscored rather than assigned fabricated accuracy.",
            "The serialized target history was produced by an earlier preprocessing pipeline that used centered outlier smoothing; future evaluations should rebuild targets from raw orders with causal cleaning.",
        ],
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def update_model_card(model_card_path: Path, summary: dict, output_dir: Path) -> None:
    card = json.loads(model_card_path.read_text(encoding="utf-8"))
    metrics = {row["model"]: row for row in summary["overall"]}
    candidate = metrics["sales_forecast_v4_retrained"]
    moving_average = metrics["moving_average_4w"]
    card["recomputed_backtest"] = {
        "generated_at": summary["generated_at"],
        "holdout": {
            "start": summary["holdout_start"],
            "end": summary["holdout_end"],
            "weeks": summary["test_weeks"],
            "method": summary["method"],
        },
        "catalog_audit": {
            "catalog_skus": summary["catalog_skus"],
            "scored_skus": summary["scored_skus"],
            "unscored_skus": summary["unscored_sku_count"],
            "scored_catalog_pct": summary["catalog_coverage_pct"],
            "holdout_observations": summary["holdout_observations"],
        },
        "candidate_metrics": candidate,
        "baseline_comparison": {
            "moving_average_4w": moving_average,
            "prior_year_same_week": metrics["prior_year_same_week"],
            "wape_improvement_vs_moving_average_percentage_points": (
                moving_average["wape_pct"] - candidate["wape_pct"]
            ),
        },
        "artifacts": {
            "summary": str((output_dir / "summary.json").as_posix()),
            "catalog_coverage": str((output_dir / "catalog_coverage.csv").as_posix()),
            "predictions": str((output_dir / "holdout_predictions.csv").as_posix()),
            "by_sales_tier": str((output_dir / "metrics_by_sales_tier.csv").as_posix()),
            "by_site": str((output_dir / "metrics_by_site.csv").as_posix()),
            "by_sku": str((output_dir / "metrics_by_sku.csv").as_posix()),
        },
        "qualification": (
            "Candidate performance is comparable to the four-week moving-average baseline overall; "
            "do not claim broad superiority until raw-order causal preprocessing and active-catalog labels are available."
        ),
    }
    model_card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, default=Path("forecast_assets/state"))
    parser.add_argument("--output-dir", type=Path, default=Path("forecast_assets/backtest/full_catalog_v4"))
    parser.add_argument("--test-weeks", type=int, default=8)
    parser.add_argument("--model-card", type=Path, default=Path("forecast_assets/MODEL_CARD.json"))
    args = parser.parse_args()
    if not 4 <= args.test_weeks <= 26:
        raise SystemExit("--test-weeks must be between 4 and 26")
    summary = run(args.state_dir, args.output_dir, args.test_weeks)
    update_model_card(args.model_card, summary, args.output_dir)
    print(json.dumps({key: summary[key] for key in (
        "catalog_skus", "scored_skus", "holdout_observations", "holdout_start", "holdout_end", "overall",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
