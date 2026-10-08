"""Analytic ranking examples and raw-data blockers for offline acceptance."""

from copy import deepcopy
import json
import math

import pytest

from furniscope_api.schemas.data_imports import ImportRules
from scripts.evaluate_enterprise_forecast import evaluate_file
from scripts.evaluate_opportunity_ranking import evaluate_export


def export_sample():
    groups = []
    for task_id, month in ((1, "01"), (2, "03")):
        created = f"2025-{month}-01T00:00:00Z"
        capture = f"2025-{month}-01T01:00:00Z"
        labels_at = f"2025-{month}-02T00:00:00Z"
        candidates = [{
            "opportunity_id": task_id * 10 + i,
            "features": {"format_version": "opportunity-features-v2", "captured_at": capture,
                         "task": {"task_uuid": f"task-{task_id}", "created_at": created},
                         "opportunity": {"id": task_id * 10 + i, "analysis_job_id": task_id,
                                         "tenant_id": 1, "market_score": 80 - i * 10,
                                         "adjusted_score": 60 + i * 10}},
            "labels": {"decision": i, "decision_eligible": True, "decision_available_at": labels_at,
                       "outcome": None, "outcome_eligible": False, "outcome_available_at": None},
        } for i in range(2)]
        groups.append({"task_id": task_id, "task_uuid": f"task-{task_id}",
                       "group_time": created, "candidates": candidates})
    return {"format_version": "opportunity-ranking-v2", "as_of": "2025-05-01T00:00:00Z",
            "next_after_task_id": None, "groups": groups}


def audit(payload, **kwargs):
    return evaluate_export(payload, "2025-02-01T00:00:00Z",
                           min_train_groups=1, min_validation_groups=1, **kwargs)


def test_ndcg_hand_calculation_and_whole_task_split():
    report = audit(export_sample())
    assert report["readiness"] == "READY_FOR_MODEL_EXPERIMENT"
    for partition, task_id in (("train", 1), ("validation", 2)):
        result = report["partitions"][partition]
        assert [r["task_id"] for r in result["tasks"]] == [task_id]
        assert result["market_mean_ndcg"] == pytest.approx(1 / math.log2(3))
        assert result["adjusted_mean_ndcg"] == 1
        assert result["mean_paired_delta"] == pytest.approx(1 - 1 / math.log2(3))


def test_late_training_labels_and_missing_validation_labels_exclude_whole_groups():
    payload = export_sample()
    payload["groups"][0]["candidates"][0]["labels"]["decision_available_at"] = "2025-02-02T00:00:00Z"
    payload["groups"][1]["candidates"][0]["labels"]["decision"] = None
    result = audit(payload)
    assert result["readiness"] == "NO_GO_INSUFFICIENT_GROUPS"
    assert result["partitions"]["train"]["exclusions"] == {"unavailable_before_validation": 1}
    assert result["partitions"]["validation"]["exclusions"] == {"unlabeled_or_ineligible": 1}
    assert all(p["evaluated_groups"] == 0 and p["market_mean_ndcg"] is None
               for p in result["partitions"].values())
    assert audit(export_sample(), label="outcome")["readiness"] == "NO_GO_INSUFFICIENT_GROUPS"


def test_late_features_and_equal_boundary_are_not_training_samples():
    payload = export_sample()
    candidate = payload["groups"][0]["candidates"][0]
    candidate["features"]["captured_at"] = "2025-02-01T00:00:00Z"
    candidate["labels"]["decision_available_at"] = "2025-02-02T00:00:00Z"
    assert audit(payload)["partitions"]["train"]["evaluated_groups"] == 0
    # A label available exactly at validation start is not strictly earlier.
    payload = export_sample()
    payload["groups"][0]["candidates"][0]["labels"]["decision_available_at"] = "2025-02-01T00:00:00Z"
    assert audit(payload)["partitions"]["train"]["evaluated_groups"] == 0


@pytest.mark.parametrize("mutation", ["duplicate_task", "duplicate_candidate", "pagination",
                                     "naive_time", "future_label", "early_label", "other_tenant",
                                     "nan_score", "wrong_task"])
def test_malformed_or_leaking_export_rejected(mutation):
    payload = export_sample()
    candidate = payload["groups"][1]["candidates"][0]
    if mutation == "duplicate_task":
        payload["groups"].append(deepcopy(payload["groups"][0]))
    elif mutation == "duplicate_candidate":
        payload["groups"][1]["candidates"].append(deepcopy(candidate))
    elif mutation == "pagination":
        payload["next_after_task_id"] = 2
    elif mutation == "naive_time":
        candidate["features"]["captured_at"] = "2025-03-01T01:00:00"
    elif mutation == "future_label":
        candidate["labels"]["decision_available_at"] = "2026-01-01T00:00:00Z"
    elif mutation == "early_label":
        candidate["labels"]["decision_available_at"] = "2025-02-01T00:00:00Z"
    elif mutation == "other_tenant":
        candidate["features"]["opportunity"]["tenant_id"] = 2
    elif mutation == "nan_score":
        candidate["features"]["opportunity"]["market_score"] = float("nan")
    elif mutation == "wrong_task":
        candidate["features"]["task"]["task_uuid"] = "wrong"
    with pytest.raises(ValueError):
        audit(payload)


def test_offline_forecast_preserves_unknown_dates_and_short_history():
    rules = ImportRules(mapping={"date": "date", "sku": "sku", "site": "site", "sales": "sales"})
    data = [{"date": f"2025-01-0{i}", "sku": "S", "site": "US", "sales": 10} for i in (1, 3)]
    raw = json.dumps(data).encode()
    report = evaluate_file("sales.json", raw, rules)
    assert report["readiness"] == "NO_GO_DATA_QUALITY"
    assert report["quality"]["missing_dates"] == 1 and report["evaluation"] is None
    assert len(report["provenance"]["input_sha256"]) == 64
    rules = rules.model_copy(update={"missing_dates": "zero", "complete_export_confirmed": True})
    report = evaluate_file("sales.json", raw, rules)
    assert report["readiness"] == "NO_GO_EVALUATION"
    assert report["quality"]["filled_rows"] == 1
    assert report["evaluation"]["day"]["evaluated_series"] == 0
    assert report["evaluation"]["week"]["series_count"] == 1
