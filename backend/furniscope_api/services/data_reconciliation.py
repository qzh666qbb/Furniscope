"""Exact date/SKU/site joins. No currency conversion or inventory backfill."""

from __future__ import annotations

import math


def reconcile(result: dict, sources: dict[str, dict]) -> None:
    key = lambda row: (row["date"], row["sku"], row["site"])
    sales = {key(row): row for row in result["records"]}
    reports = {}
    for role, payload in sources.items():
        other = {key(row): row for row in payload["records"]}
        matched = sales.keys() & other.keys()
        missing = sorted(sales.keys() - other.keys())
        extra = sorted(other.keys() - sales.keys())
        discrepancies, censored = [], []
        for item in sorted(matched):
            current, source = sales[item], other[item]
            if role == "control":
                if not math.isclose(current["sales"], source["sales"], abs_tol=1e-9, rel_tol=1e-9):
                    discrepancies.append(dict(date=item[0], sku=item[1], site=item[2],
                        sales=current["sales"], control_sales=source["sales"],
                        difference=current["sales"] - source["sales"],
                        source_rows=current["source_rows"], control_rows=source["source_rows"]))
            else:
                current["inventory"] = source["inventory"]
                current["inventory_source_rows"] = source["source_rows"]
                if source["inventory"] == 0:
                    censored.append(dict(date=item[0], sku=item[1], site=item[2],
                                         sales=current["sales"]))
        reports[role] = {
            "matched_rows": len(matched), "missing_count": len(missing), "extra_count": len(extra),
            "missing_keys": missing, "extra_keys": extra,
            "differences": discrepancies, "difference_count": len(discrepancies),
            "zero_inventory_rows": censored,
            "reconciled": not missing and not extra and not discrepancies,
        }
        if role == "control" and not reports[role]["reconciled"]:
            result["errors"].append({"row": None, "reason": "销量对账表有差额或未匹配日期/SKU/站点"})
        if role == "inventory":
            result["quality"]["warnings"].append({
                "code": "INVENTORY_RECONCILIATION", "matched_rows": len(matched),
                "missing_count": len(missing), "extra_count": len(extra),
                "zero_inventory_count": len(censored)})
            if censored:
                result["quality"]["training_blockers"].append(
                    "匹配库存有零库存记录，需核验缺货与快照时点；当前版本保留事实，不把受限销量当完整需求")
    result["reconciliation"] = reports
    # Full details live in the SHA-verified audit, not the list API.
    result["quality"]["reconciliation"] = {
        role: {k: v for k, v in report.items() if k not in
               {"missing_keys", "extra_keys", "differences", "zero_inventory_rows"}}
        for role, report in reports.items()}
    result["quality"].update(
        error_count=len(result["errors"]), errors=result["errors"][:100],
        can_confirm=bool(result["records"]) and not result["errors"],
        trainable=result["quality"]["trainable"] and not result["errors"] and
                  not result["quality"]["training_blockers"])
