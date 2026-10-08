"""Independent enterprise XGBoost engine; only confirmed canonical sales enter.

This file is copied into each published artifact and loaded through the existing
ForecastService protocol. Evaluation and live inference use the same recursive
forecast function. No packaged model or cross-tenant history is loaded.
"""

from __future__ import annotations

import json
import math
import pickle
import platform
from datetime import timedelta
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

ENGINE_VERSION = "tenant-xgb-v2"
PARAMETERS = dict(n_estimators=100, max_depth=3, learning_rate=0.05,
                  objective="reg:squarederror", random_state=42, n_jobs=1)
GATE = {"version": "sku-rolling-v2", "max_wape": 0.60, "max_abs_bias": 0.35,
        "baseline_tolerance": 1.05, "absolute_wape_tolerance": 0.005,
        "min_daily_history": 140, "min_weekly_history": 20,
        "day_horizon": 28, "week_horizon": 4, "folds": 3, "sparse_zero_share": 0.5,
        "sparse_validation_scope": "window_total"}


def _key(sku, site):
    return json.dumps([str(sku), str(site)], ensure_ascii=False)


def prepare(records):
    daily = pd.DataFrame(records)[["date", "sku", "site", "sales"]].copy()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["sales"] = daily["sales"].astype(float)
    daily = daily.sort_values(["sku", "site", "date"]).reset_index(drop=True)
    if daily.duplicated(["sku", "site", "date"]).any():
        raise ValueError("标准数据仍有重复日期")
    if not np.isfinite(daily.sales).all() or (daily.sales < 0).any():
        raise ValueError("训练需要有限且非负的销量")
    weekly_rows = []
    for (sku, site), group in daily.groupby(["sku", "site"]):
        if len(group) != (group.date.max() - group.date.min()).days + 1:
            raise ValueError(f"{sku}/{site} 存在未知日期缺口")
        calendar = group.assign(week=group.date - pd.to_timedelta(group.date.dt.weekday, unit="D"))
        for when, week in calendar.groupby("week"):
            if len(week) == 7:  # Partial calendar weeks are not silently treated as full weeks.
                weekly_rows.append(dict(date=when, sku=sku, site=site, sales=float(week.sales.sum())))
    weekly = pd.DataFrame(weekly_rows, columns=["date", "sku", "site", "sales"])
    return daily, weekly


def _features(history, when, pair, grain):
    lags = (1, 7, 14, 28) if grain == "day" else (1, 2, 3, 4)
    values = list(history)
    window = 7 if grain == "day" else 4
    return [pair, when.dayofweek, when.month, when.dayofyear,
            *[values[-lag] for lag in lags],
            float(np.mean(values[-window:])), float(np.mean(values[-max(lags):]))]


def fit(frame, grain, encoders):
    X, y = [], []
    warmup = 28 if grain == "day" else 4
    for (sku, site), group in frame.groupby(["sku", "site"], sort=True):
        group = group.sort_values("date")
        values = group.sales.tolist()
        for i in range(warmup, len(group)):
            X.append(_features(values[:i], group.iloc[i].date, encoders[_key(sku, site)], grain))
            y.append(values[i])
    if not X:
        raise ValueError("历史不足，无法构建因果训练特征")
    model = XGBRegressor(**PARAMETERS)
    model.fit(np.asarray(X), np.asarray(y))
    return model


def forecast(model, history, start, count, pair, grain):
    """Recursively predict all intermediate dates using only historical labels."""
    values = list(history)
    step = timedelta(days=1 if grain == "day" else 7)
    result = []
    for i in range(count):
        when = start + i * step
        value = max(0.0, float(model.predict(np.asarray([
            _features(values, when, pair, grain)]))[0]))
        if not math.isfinite(value):
            raise ValueError("模型产生非有限预测")
        result.append(value)
        values.append(value)
    return result


def _metrics(actual, predicted):
    actual, predicted = np.asarray(actual), np.asarray(predicted)
    denominator = float(np.abs(actual).sum())
    return {
        "samples": len(actual), "mae": float(np.abs(actual - predicted).mean()) if len(actual) else None,
        "wape": float(np.abs(actual - predicted).sum() / denominator) if denominator else None,
        "bias": float((predicted - actual).sum() / denominator) if denominator else None,
        "actual_total": float(actual.sum()), "predicted_total": float(predicted.sum()),
    }


def _baseline(history, grain, method):
    window = (28 if method == "intermittent_mean" else 7) if grain == "day" else 4
    return float(np.mean(list(history)[-window:]))


def _gate(metric, baselines, method):
    reasons = []
    if metric["wape"] is None:
        # A genuinely zero interval may be predicted as zero, but never gets a WAPE.
        if metric["predicted_total"] != 0:
            reasons.append("验证期销量为0但预测非零，无法通过相对误差门槛")
    else:
        # Intermittent demand is tested for planning-window volume, not exact timing.
        value = abs(metric["bias"]) if method == "intermittent_mean" else metric["wape"]
        best = min(abs(m["bias"]) if method == "intermittent_mean" else m["wape"]
                   for m in baselines.values() if m["wape"] is not None)
        if method != "intermittent_mean" and value > GATE["max_wape"]:
            reasons.append("WAPE超过工程门槛")
        if value > best * GATE["baseline_tolerance"] + GATE["absolute_wape_tolerance"]:
            reasons.append("未达到基础预测对照门槛")
        if abs(metric["bias"]) > GATE["max_abs_bias"]:
            reasons.append("系统性偏差超过门槛")
    return reasons


def evaluate(frame, grain, pairs=None):
    """Three expanding windows per SKU. No other SKU's future can enter fitting.

    Method selection uses only the prefix preceding ALL validation windows.
    Short series are explicitly unvalidated and cannot qualify a deployment.
    """
    horizon = GATE[f"{grain}_horizon"]
    minimum = GATE["min_daily_history" if grain == "day" else "min_weekly_history"]
    pairs = pairs or list(frame.groupby(["sku", "site"]).groups)
    totals = {name: [] for name in ("actual", "predicted", "last_value", "moving_average")}
    series, reasons = [], []
    for sku, site in pairs:
        group = frame[(frame.sku == sku) & (frame.site == site)].sort_values("date")
        row = {"sku": sku, "site": site, "history_periods": len(group), "minimum_periods": minimum,
               "segment": "new", "method": "recent_mean", "validation_status": "unvalidated",
               "validation_scope": None,
               "passed": None, "folds": [], "mae": None, "wape": None, "bias": None,
               "reasons": ["历史不足，使用近期均值；尚未验证精度"]}
        if len(group) < minimum or not group.sales.any():
            if len(group) and not group.sales.any():
                row.update(segment="dormant", reasons=["全零历史，WAPE无定义；尚未验证需求恢复"])
            series.append(row)
            continue
        initial = group.iloc[:-horizon * GATE["folds"]]
        sparse = float((initial.sales == 0).mean()) >= GATE["sparse_zero_share"]
        method = "intermittent_mean" if sparse else "xgboost"
        row.update(segment="sparse" if sparse else "mature", method=method, reasons=[],
                   validation_scope="window_total" if sparse else "period_values")
        pair_values = {name: [] for name in totals}
        for fold in range(GATE["folds"]):
            stop = len(group) - horizon * (GATE["folds"] - fold)
            train, valid = group.iloc[:stop], group.iloc[stop:stop + horizon]
            if method == "xgboost":
                model = fit(train, grain, {_key(sku, site): 0})
                pred = forecast(model, train.sales.tolist(), valid.iloc[0].date, horizon, 0, grain)
            else:
                pred = [_baseline(train.sales, grain, method)] * horizon
            values = {
                "actual": valid.sales.tolist(), "predicted": pred,
                "last_value": [float(train.sales.iloc[-1])] * horizon,
                "moving_average": [_baseline(train.sales, grain, "recent_mean")] * horizon,
            }
            metric = _metrics(values["actual"], pred)
            baselines = {name: _metrics(values["actual"], values[name])
                         for name in ("last_value", "moving_average")}
            failures = _gate(metric, baselines, method)
            result = {**metric, "fold": fold + 1, "baselines": baselines,
                      "train_through": train.date.max().date().isoformat(),
                      "validation_from": valid.date.min().date().isoformat(),
                      "validation_through": valid.date.max().date().isoformat(),
                      "passed": not failures, "reasons": failures}
            row["folds"].append(result)
            row["reasons"].extend(f"窗口{fold + 1}：{reason}" for reason in failures)
            for name in totals:
                pair_values[name].extend(values[name])
                totals[name].extend(values[name])
        metric = _metrics(pair_values["actual"], pair_values["predicted"])
        if metric["wape"] is None:
            row["reasons"].append("全部验证期销量为0，不能确认预测精度")
        row.update(**metric, passed=not row["reasons"],
                   validation_status="validated" if not row["reasons"] else "failed",
                   volume_mae=float(np.mean([abs(f["actual_total"] - f["predicted_total"])
                                             for f in row["folds"]])))
        series.append(row)
        reasons.extend(f"{sku}/{site} {reason}" for reason in row["reasons"])
    evaluated = [row for row in series if row["folds"]]
    if not evaluated:
        reasons.append(f"{grain} 历史不足或全零：至少一个SKU须有{minimum}个周期并通过三个验证窗口")
    metric = _metrics(totals["actual"], totals["predicted"])
    wapes = [row["wape"] for row in evaluated if row["wape"] is not None]
    return {**metric, "baselines": {name: _metrics(totals["actual"], totals[name])
            for name in ("last_value", "moving_average")},
            "coverage": len(evaluated) / max(len(series), 1), "series_count": len(series),
            "evaluated_series": len(evaluated), "unvalidated_series": len(series) - len(evaluated),
            "macro_wape": float(np.mean(wapes)) if wapes else None,
            "worst_sku_wape": max(wapes) if wapes else None, "series": series,
            "horizon": horizon, "passed": not reasons, "reasons": reasons}


def train_artifacts(records, state_dir, lineage):
    state_dir = Path(state_dir)
    daily, weekly = prepare(records)
    pairs = list(daily.groupby(["sku", "site"]).groups)
    evaluation = {"gate": GATE, "day": evaluate(daily, "day", pairs),
                  "week": evaluate(weekly, "week", pairs)}
    evaluation["passed"] = evaluation["day"]["passed"] and evaluation["week"]["passed"]
    evaluation["reasons"] = evaluation["day"]["reasons"] + evaluation["week"]["reasons"]
    if not evaluation["passed"]:
        return evaluation
    encoders = {_key(*pair): 0 for pair in pairs}
    # Full-data refit occurs only AFTER the independent held-out evaluation.
    models = {}
    for grain, frame in (("day", daily), ("week", weekly)):
        models[grain] = {}
        for row in evaluation[grain]["series"]:
            if row["method"] == "xgboost":
                group = frame[(frame.sku == row["sku"]) & (frame.site == row["site"])]
                models[grain][_key(row["sku"], row["site"])] = fit(group, grain, encoders)
    state_dir.mkdir(parents=True, exist_ok=True)
    for filename, obj in [("daily.pkl", daily), ("weekly.pkl", weekly), ("encoders.pkl", encoders),
                          ("day_model.pkl", models["day"]), ("week_model.pkl", models["week"])]:
        with (state_dir / filename).open("wb") as handle:
            pickle.dump(obj, handle)
    (state_dir / "meta.json").write_text(json.dumps({
        "initialized": True, "last_date": daily.date.max().date().isoformat(),
        "n_skus": daily.sku.nunique(), "engine": ENGINE_VERSION,
    }))
    (state_dir / "model_manifest.json").write_text(json.dumps({
        "algorithm": ENGINE_VERSION, "lineage": lineage, "evaluation": evaluation,
        "parameters": PARAMETERS, "scenario_support": ["baseline", "reference_sku"],
        "environment": {"python": platform.python_version(),
                        **{name: version(name) for name in ("xgboost", "pandas", "numpy", "scikit-learn")}},
    }))
    return evaluation


class ForecastService:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.meta = json.loads((self.state_dir / "meta.json").read_text())
        self.manifest = json.loads((self.state_dir / "model_manifest.json").read_text())
        for name in ("daily", "weekly", "day_model", "week_model", "encoders"):
            with (self.state_dir / f"{name}.pkl").open("rb") as handle:
                setattr(self, name, pickle.load(handle))

    def status(self):
        return {"initialized": True, "day_model": True, "week_model": True}

    def list_skus(self, site=None):
        frame = self.daily if site is None else self.daily[self.daily.site == site]
        return [(sku, row_site, len(self.weekly[(self.weekly.sku == sku) &
                                               (self.weekly.site == row_site)]))
                for (sku, row_site), _group in
                frame.groupby(["sku", "site"], sort=True)]

    def predict(self, sku, site, *, granularity="day", days=7, weeks=1,
                start_date=None, baseline=None, **scenario):
        unsupported = [key for key, value in scenario.items()
                       if value is not None and value is not False]
        if unsupported:
            raise ValueError("本企业模型尚未校准价格/库存/促销情景，请清空情景参数")
        grain = granularity
        if grain not in {"day", "week"}:
            raise ValueError("不支持的预测粒度")
        route = next((row for row in self.manifest["evaluation"][grain]["series"]
                      if row["sku"] == sku and row["site"] == site), None)
        day_history = self.daily[(self.daily.sku == sku) & (self.daily.site == site)].sort_values("date")
        history = (self.daily if grain == "day" else self.weekly)
        history = history[(history.sku == sku) & (history.site == site)].sort_values("date")
        step = timedelta(days=1 if grain == "day" else 7)
        if history.empty:
            if baseline is None and day_history.empty:
                raise ValueError("SKU没有企业训练历史，请提供明确的基础日销量")
            end = day_history.date.max() if not day_history.empty else pd.Timestamp(self.meta["last_date"])
            default_start = end + timedelta(days=1)
            if grain == "week":
                default_start = end + timedelta(days=(7 - end.dayofweek) % 7 or 7)
        else:
            default_start = history.iloc[-1].date + step
        start = pd.Timestamp(start_date) if start_date else default_start
        if start < default_start or (grain == "week" and start.dayofweek != 0):
            raise ValueError("预测开始日期须晚于训练数据；周预测从周一开始")
        gap = (start - default_start).days // (1 if grain == "day" else 7)
        count = days if grain == "day" else weeks
        if gap + count > (730 if grain == "day" else 104):
            raise ValueError("预测日期距离训练数据过远，请更新数据后重试")
        if baseline is not None:
            if not math.isfinite(float(baseline)) or float(baseline) < 0:
                raise ValueError("基础日销量须为有限非负数")
            values = [float(baseline) * (1 if grain == "day" else 7)] * count
        elif route["method"] != "xgboost":
            mean = (_baseline(history.sales, grain, route["method"]) if not history.empty
                    else _baseline(day_history.sales, "day", "recent_mean") * 7)
            values = [mean] * count
        else:
            model = (self.day_model if grain == "day" else self.week_model)[_key(sku, site)]
            values = forecast(model, history.sales.tolist(), default_start, gap + count,
                              self.encoders[_key(sku, site)], grain)[gap:]
        validated = baseline is None and route is not None and route["validation_status"] == "validated"
        in_window = gap == 0 and (count == GATE[f"{grain}_horizon"] if route and
                    route["method"] == "intermittent_mean" else count <= GATE[f"{grain}_horizon"])
        # Unvalidated/extended horizons have no empirically supported error band.
        volume_only = validated and route["validation_scope"] == "window_total"
        error = route["mae"] if validated and in_window and not volume_only else None
        predictions = []
        for i, value in enumerate(values):
            when = start + i * step
            predictions.append({("date" if grain == "day" else "week_start"): when.date().isoformat(),
                                "sales": value, "lower": max(0.0, value - error) if error is not None else None,
                                "upper": value + error if error is not None else None})
        total = sum(values)
        reliability = "D" if not validated else ("B" if in_window else "C")
        interval = ([sum(p["lower"] for p in predictions), sum(p["upper"] for p in predictions)]
                    if error is not None else [None, None])
        if volume_only and in_window:
            interval = [max(0.0, total - route["volume_mae"]), total + route["volume_mae"]]
        return {"predictions": predictions, "weekly_total": total,
                "daily_avg": total / (count * (1 if grain == "day" else 7)),
                "confidence_interval": interval,
                "reliability": reliability, "method": "explicit_baseline" if baseline is not None else route["method"],
                "segment": route["segment"] if route else "cold_start",
                "validation_status": "validated" if validated and in_window else "unvalidated",
                "validation_scope": route["validation_scope"] if validated and in_window else None,
                "data_through": day_history.date.max().date().isoformat() if not day_history.empty else None}
