"""Offline raw-data quality and rolling forecast evaluation; no deployment."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import platform

from furniscope_api.schemas.data_imports import ImportRules
from furniscope_api.services import sales_data_cleaner as cleaner
from furniscope_forecast import tenant_engine


def evaluate_file(filename, raw, rules):
    cleaned = cleaner.clean_table(cleaner.read_table(filename, raw), rules)
    report = {
        "protocol": "enterprise-forecast-audit-v1", "rules": rules.model_dump(),
        "quality": cleaned["quality"], "evaluation": None,
        "provenance": {
            "input_sha256": cleaner.sha256(raw),
            "rules_sha256": cleaner.sha256(cleaner.stable_json(rules.model_dump())),
            "canonical_records_sha256": cleaner.sha256(cleaner.stable_json(cleaned["records"])),
            "code_sha256": {Path(module.__file__).name: cleaner.sha256(Path(module.__file__).read_bytes())
                           for module in (cleaner, tenant_engine)},
            "runner_sha256": cleaner.sha256(Path(__file__).read_bytes()),
            "schema_sha256": cleaner.sha256(cleaner.stable_json(ImportRules.model_json_schema())),
            "python": platform.python_version(),
            "packages": {name: version(name) for name in ("numpy", "pandas", "xgboost", "scikit-learn", "pydantic")},
        },
        "limits": [
            "仅本地质量与滚动评测，未建立企业产品映射、未验证上传人身份，未写数据库或发布模型。",
            "规则须由企业明确核验；如需独立销量/库存多表对账，先在训练页面完成确认。",
            "标准记录摘要针对records数组，不等同于API带元数据的标准文件SHA。",
            "使用与上线相同的逐SKU三窗口评测；门槛通过不保证未来精度。",
            "无真实企业数据时，合成样本只能验证工程行为；短历史和全零仍未验证。",
        ],
    }
    if not cleaned["quality"]["trainable"]:
        report["readiness"] = "NO_GO_DATA_QUALITY"
        return report
    daily, weekly = tenant_engine.prepare(cleaned["records"])
    pairs = list(daily.groupby(["sku", "site"]).groups)
    evaluation = {"engine": tenant_engine.ENGINE_VERSION, "gate": tenant_engine.GATE,
                  "day": tenant_engine.evaluate(daily, "day", pairs),
                  "week": tenant_engine.evaluate(weekly, "week", pairs)}
    evaluation["passed"] = evaluation["day"]["passed"] and evaluation["week"]["passed"]
    report["evaluation"] = evaluation
    report["readiness"] = "OFFLINE_GATE_PASSED" if evaluation["passed"] else "NO_GO_EVALUATION"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="CSV/XLSX/JSON原始销量文件")
    parser.add_argument("--rules", type=Path, required=True, help="已核验的ImportRules JSON")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {args.input.resolve(), args.rules.resolve()}:
        parser.error("报告不能覆盖输入或规则")
    from furniscope_api.errors import BusinessError
    try:
        report = evaluate_file(args.input.name, args.input.read_bytes(),
                               ImportRules.model_validate_json(args.rules.read_bytes()))
    except (ValueError, BusinessError) as exc:
        parser.error(str(exc))
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(report["readiness"])
    return 0 if report["readiness"] == "OFFLINE_GATE_PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
