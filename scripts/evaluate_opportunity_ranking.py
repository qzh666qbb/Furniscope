"""Audit a complete ranking-v2 export without training or publishing a model."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return result.astimezone(timezone.utc)


def ndcg(candidates, score, label, k):
    ordered = sorted(candidates, key=lambda c: (-float(c["features"]["opportunity"][score]),
                                                c["opportunity_id"]))
    gains = [c["labels"][label] for c in ordered[:k]]
    ideal = sorted((c["labels"][label] for c in candidates), reverse=True)[:k]
    dcg = lambda values: sum(value / math.log2(i + 2) for i, value in enumerate(values))
    return dcg(gains) / dcg(ideal) if any(ideal) else None


def evaluate_export(payload, validation_start, *, label="decision", k=5,
                    min_train_groups=30, min_validation_groups=20):
    if label not in {"decision", "outcome"} or k < 1:
        raise ValueError("标签或NDCG截断参数无效")
    if min_train_groups < 1 or min_validation_groups < 1:
        raise ValueError("最小任务数必须为正数")
    if payload.get("format_version") != "opportunity-ranking-v2":
        raise ValueError("需要opportunity-ranking-v2完整导出")
    if payload.get("next_after_task_id") is not None:
        raise ValueError("导出仍有后续页，请在页面下载完整导出")
    cutoff, split = timestamp(payload["as_of"]), timestamp(validation_start)
    if split >= cutoff:
        raise ValueError("验证开始时间必须早于导出截点")
    task_ids, task_uuids, opportunity_ids, tenants = set(), set(), set(), set()
    counts = {name: Counter() for name in ("train", "validation")}
    exclusions = {name: Counter() for name in counts}
    accepted = {name: [] for name in counts}
    for group in sorted(payload["groups"], key=lambda g: (timestamp(g["group_time"]), g["task_id"])):
        group_time = timestamp(group["group_time"])
        if group_time > cutoff:
            raise ValueError("任务晚于导出截点")
        if group["task_id"] in task_ids or group["task_uuid"] in task_uuids:
            raise ValueError("重复任务或分页重复，不能拆分任务后评测")
        task_ids.add(group["task_id"])
        task_uuids.add(group["task_uuid"])
        partition = "train" if group_time < split else "validation"
        count, rejected = counts[partition], exclusions[partition]
        count["groups"] += 1
        usable, reasons = [], set()
        for candidate in group["candidates"]:
            identity = candidate["opportunity_id"]
            if identity in opportunity_ids:
                raise ValueError("机会重复，不能把反馈修订视为独立样本")
            opportunity_ids.add(identity)
            count["candidates"] += 1
            features, labels = candidate.get("features"), candidate["labels"]
            if not features:
                reasons.add("missing_historical_features")
                continue
            if features.get("format_version") != "opportunity-features-v2":
                raise ValueError("不支持的特征快照版本")
            captured = timestamp(features["captured_at"])
            if not group_time <= captured <= cutoff:
                raise ValueError("特征时间不在任务创建与导出截点之间")
            opportunity, task = features["opportunity"], features["task"]
            if (task["task_uuid"] != group["task_uuid"] or
                    opportunity["analysis_job_id"] != group["task_id"] or
                    opportunity["id"] != identity or timestamp(task["created_at"]) != group_time):
                raise ValueError("特征快照与任务/机会归属不一致")
            tenants.add(opportunity["tenant_id"])
            if len(tenants) > 1:
                raise ValueError("不能混合多个企业进行此项验收")
            value = labels.get(label)
            if labels.get(f"{label}_eligible") is not True or value is None:
                reasons.add("unlabeled_or_ineligible")
                continue
            if type(value) is not int or value not in (0, 1):
                raise ValueError("有效标签必须为二元整数")
            available = timestamp(labels[f"{label}_available_at"])
            if not captured <= available <= cutoff:
                raise ValueError("标签早于特征或晚于导出截点")
            if partition == "train" and (captured >= split or available >= split):
                reasons.add("unavailable_before_validation")
                continue
            if any(opportunity.get(score) is None for score in ("market_score", "adjusted_score")):
                reasons.add("missing_score")
                continue
            if any(not math.isfinite(float(opportunity[score])) or not 0 <= float(opportunity[score]) <= 100
                   for score in ("market_score", "adjusted_score")):
                raise ValueError("分数必须在0到100之间且有限")
            count["eligible_candidates"] += 1
            usable.append(candidate)
        # Partial judgments change the candidate set and inflate ranking scores.
        if reasons:
            rejected.update(reasons)
        elif len(usable) < 2:
            rejected["fewer_than_two_candidates"] += 1
        elif len({c["labels"][label] for c in usable}) < 2:
            rejected["no_label_contrast"] += 1
        else:
            accepted[partition].append({
                "task_id": group["task_id"], "task_uuid": group["task_uuid"],
                "group_time": group["group_time"], "candidate_count": len(usable),
                "market_ndcg": ndcg(usable, "market_score", label, k),
                "adjusted_ndcg": ndcg(usable, "adjusted_score", label, k),
            })
    partitions = {}
    for name, rows in accepted.items():
        totals = counts[name]
        partitions[name] = {
            **{key: totals[key] for key in ("groups", "candidates", "eligible_candidates")},
            "evaluated_groups": len(rows),
            "evaluated_group_coverage": len(rows) / totals["groups"] if totals["groups"] else 0,
            "exclusions": dict(exclusions[name]), "tasks": rows,
            "market_mean_ndcg": sum(r["market_ndcg"] for r in rows) / len(rows) if rows else None,
            "adjusted_mean_ndcg": sum(r["adjusted_ndcg"] for r in rows) / len(rows) if rows else None,
            "mean_paired_delta": sum(r["adjusted_ndcg"] - r["market_ndcg"] for r in rows) / len(rows) if rows else None,
        }
    enough = len(accepted["train"]) >= min_train_groups and len(accepted["validation"]) >= min_validation_groups
    return {
        "protocol": "ranking-audit-v1", "label": label, "k": k, "as_of": payload["as_of"],
        "validation_start": split.isoformat(), "partitions": partitions,
        "readiness": "READY_FOR_MODEL_EXPERIMENT" if enough else "NO_GO_INSUFFICIENT_GROUPS",
        "minimum_groups": {"train": min_train_groups, "validation": min_validation_groups},
        "limits": [
            "任务按创建时间整组划分；训练特征和标签须在验证开始前已存在。",
            "只评估完整标注且同时有正负标签的任务，缺失标签不视为失败；覆盖筛选会产生选择偏差。",
            "采纳标签是偏好，经营结果是企业报告的目标达成，均不证明因果收益。",
            "当前导出只包含截点时最新修订；晚到修订会保守排除旧训练任务，不推测旧标签。",
            "最小任务数是可配置工程门槛，达标仅支持开展模型实验，不构成统计显著性或上线批准。",
            "未训练模型；NDCG比较不替代硬约束门控、真实试点及独立留出验证。",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--validation-start", required=True, help="预先选定的验证开始时间，须含时区")
    parser.add_argument("--label", choices=("decision", "outcome"), default="decision")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--min-train-groups", type=int, default=30)
    parser.add_argument("--min-validation-groups", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("报告不能覆盖输入")
    raw = args.input.read_bytes()
    try:
        report = evaluate_export(json.loads(raw), args.validation_start, label=args.label, k=args.k,
                                 min_train_groups=args.min_train_groups,
                                 min_validation_groups=args.min_validation_groups)
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    report["provenance"] = {"input_sha256": hashlib.sha256(raw).hexdigest(),
                            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(report["readiness"])
    return 0 if report["readiness"] == "READY_FOR_MODEL_EXPERIMENT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
