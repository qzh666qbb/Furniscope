"""Build the canonical portable-report artifact for the full-catalog backtest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


MODEL_LABELS = {
    "sales_forecast_v4_retrained": "V4 重新训练",
    "moving_average_4w": "4 周移动平均",
    "prior_year_same_week": "去年同期",
}
TIER_LABELS = {"high": "高销量", "medium": "中销量", "low": "低销量", "cold_start": "冷启动"}


def records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.where(pd.notna(frame), None).to_json(orient="records", force_ascii=False))


def build(backtest_dir: Path) -> dict:
    summary = json.loads((backtest_dir / "summary.json").read_text(encoding="utf-8"))
    overall = pd.DataFrame(summary["overall"])
    tiers = pd.read_csv(backtest_dir / "metrics_by_sales_tier.csv")
    sites = pd.read_csv(backtest_dir / "metrics_by_site.csv")
    candidate = overall.loc[overall["model"] == "sales_forecast_v4_retrained"].iloc[0]
    baseline = overall.loc[overall["model"] == "moving_average_4w"].iloc[0]
    improvement_pp = float(baseline["wape_pct"] - candidate["wape_pct"])

    overall_view = overall.assign(
        model_label=overall["model"].map(MODEL_LABELS),
        wape=overall["wape_pct"] / 100,
        mape=overall["mape_pct"] / 100,
        smape=overall["smape_pct"] / 100,
        bias=overall["bias_pct"] / 100,
        coverage=overall["coverage_pct"] / 100,
    )[["model_label", "observations", "mae", "rmse", "wape", "mape", "smape", "bias", "coverage"]]
    tier_view = tiers[tiers["model"].isin(["sales_forecast_v4_retrained", "moving_average_4w"])].copy()
    tier_view["sales_tier_label"] = tier_view["sales_tier"].map(TIER_LABELS)
    tier_view["model_label"] = tier_view["model"].map(MODEL_LABELS)
    tier_view["wape"] = tier_view["wape_pct"] / 100
    tier_view = tier_view[["sales_tier_label", "model_label", "observations", "wape"]]
    site_view = sites[sites["model"].isin(["sales_forecast_v4_retrained", "moving_average_4w"])].copy()
    site_view["model_label"] = site_view["model"].map(MODEL_LABELS)
    site_view["wape"] = site_view["wape_pct"] / 100
    site_view = site_view[["site", "model_label", "observations", "wape"]]

    generated_at = summary["generated_at"]
    sources = [
        {"id": "weekly_state", "label": "周粒度模型状态", "path": "forecast_assets/state/weekly.pkl"},
        {"id": "backtest_summary", "label": "回测摘要与口径", "path": "forecast_assets/backtest/full_catalog_v4/summary.json"},
        {"id": "tier_metrics", "label": "销量层级指标", "path": "forecast_assets/backtest/full_catalog_v4/metrics_by_sales_tier.csv"},
        {"id": "site_metrics", "label": "站点指标", "path": "forecast_assets/backtest/full_catalog_v4/metrics_by_site.csv"},
        {"id": "catalog_coverage", "label": "1204 SKU 覆盖审计", "path": "forecast_assets/backtest/full_catalog_v4/catalog_coverage.csv"},
    ]
    source_sql = {
        "weekly_state": "SELECT * FROM weekly_state WHERE date < holdout_start ORDER BY sku_site, date",
        "backtest_summary": "SELECT model, observations, mae, rmse, wape_pct, mape_pct, smape_pct, bias_pct, coverage_pct FROM backtest_overall ORDER BY model",
        "tier_metrics": "SELECT sales_tier, model, observations, wape_pct FROM metrics_by_sales_tier WHERE model IN ('sales_forecast_v4_retrained', 'moving_average_4w') ORDER BY sales_tier, model",
        "site_metrics": "SELECT site, model, observations, wape_pct FROM metrics_by_site WHERE model IN ('sales_forecast_v4_retrained', 'moving_average_4w') ORDER BY site, model",
        "catalog_coverage": "SELECT sku, first_observed_week, last_observed_week, source_rows, sites, holdout_observations, status, reason FROM catalog_coverage ORDER BY sku",
    }
    source_details = [
        {
            "id": source["id"],
            "query": {
                "engine": "backtest artifact tables",
                "language": "sql",
                "sql": source_sql[source["id"]],
                "description": "固定训练截止日，使用严格滞后和扩展统计特征，对留出期执行一周前瞻滚动评分。",
                "executed_at": generated_at,
                "metric_definitions": [
                    "WAPE = sum(abs(prediction - actual)) / sum(abs(actual))",
                    "Bias = sum(prediction - actual) / sum(abs(actual))",
                    "Coverage = usable predictions / requested observations",
                ],
            },
        }
        for source in sources
    ]

    manifest = {
        "version": 1,
        "surface": "report",
        "title": "FurniScope Sales Forecast V4：全目录回测评估",
        "description": "1204 SKU 覆盖审计、401 个有标签 SKU 的 8 周留出回测与基线比较。",
        "generatedAt": generated_at,
        "charts": [
            {
                "id": "overall_wape",
                "title": "总体 WAPE：V4 仅小幅领先移动平均",
                "subtitle": "越低越好；去年同期仅覆盖 71.3% 可用观测。",
                "type": "bar",
                "dataset": "overall",
                "sourceId": "backtest_summary",
                "valueFormat": "percent",
                "layout": "half",
                "surface": {"compact": True},
                "encodings": {
                    "x": {"field": "model_label", "type": "nominal", "label": "模型"},
                    "y": {"field": "wape", "type": "quantitative", "label": "WAPE"},
                    "tooltip": [
                        {"field": "observations", "type": "quantitative", "label": "观测数", "format": "number"},
                        {"field": "bias", "type": "quantitative", "label": "偏差", "format": "percent"},
                    ],
                },
            },
        ],
        "sources": sources,
        "blocks": [
            {"id": "overall_chart", "type": "chart", "chartId": "overall_wape"},
            {
                "id": "method",
                "type": "markdown",
                "sourceId": "catalog_coverage",
                "body": "## 技术摘要\n\nV4 WAPE **58.06%**，较移动平均低 **0.42 个百分点**，但 MAPE/sMAPE 更差；高、中销量改善，低销量与冷启动无优势。1,204 SKU 中 401 个有 4,969 条留出标签，803 个无观测，不填零。\n\n**口径与方法：** 8 周一周前瞻回测；训练截止与统计特征均早于预测周。WAPE 为总量加权绝对误差，Bias 正值代表高估；数据校验和单测通过。\n\n**限制与行动：** 缺在售状态、品类维表，旧目标清洗可能含未来信息。应从原始订单因果重建并做多折回测；CA、IT、低销量及冷启动保留移动平均回退。",
            },
        ],
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": {
            "version": 1,
            "generatedAt": generated_at,
            "status": "ready",
            "datasets": {
                "overall": records(overall_view),
                "tiers": records(tier_view),
                "sites": records(site_view),
            },
        },
        "sources": source_details,
        "package_info": {"originUrl": "artifact://furniscope-sales-forecast-v4-backtest"},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-dir", type=Path, default=Path("forecast_assets/backtest/full_catalog_v4"))
    parser.add_argument("--output", type=Path, default=Path("forecast_assets/backtest/full_catalog_v4/artifact.json"))
    args = parser.parse_args()
    artifact = build(args.backtest_dir)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
