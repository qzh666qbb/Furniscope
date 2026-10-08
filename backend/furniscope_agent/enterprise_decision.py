"""Explicit enterprise scoring policy and evidence-based feasibility checks."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any
from .product_facts import fact_is_confirmed, validate_codes

DEFAULT_WEIGHTS = {"demand_heat": .30, "demand_growth": .15, "unmet_need": .25,
                   "competition_space": .20, "profit_space": .10}
POLICY_TEMPLATES = {
    "balanced": {"name": "均衡判断", "weights": DEFAULT_WEIGHTS, "fit_strength": .3},
    "growth": {"name": "需求增长", "weights": dict(zip(DEFAULT_WEIGHTS, [.3, .3, .2, .15, .05])), "fit_strength": .3},
    "profit": {"name": "利润空间", "weights": dict(zip(DEFAULT_WEIGHTS, [.2, .1, .15, .2, .35])), "fit_strength": .3},
}


def default_policy() -> dict:
    return {"version": 0, "name": "均衡判断", "objective": "balanced",
            "weights": dict(DEFAULT_WEIGHTS), "fit_strength": .3,
            "required_capabilities": [], "calibration": "uncalibrated_heuristic"}


def number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    try:
        result = float(value) if value is not None and not isinstance(value, bool) else None
        return result if result is not None and math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _day(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _valid_at(item, captured):
    start = item.get("effective_at", item.get("valid_from"))
    end = item.get("expires_at", item.get("valid_until"))
    return not ((start and (_day(start) is None or _day(start) > captured))
                or (end and (_day(end) is None or _day(end) < captured)))


def enterprise_fit(snapshot: dict, *, category_code: str | None, market_country: str | None,
                   taxonomy_code: str, product_facts: dict | None = None) -> tuple[float | None, float, dict]:
    profile = snapshot.get("profile") or {}
    policy = snapshot.get("opportunity_policy") or default_policy()
    captured = _day(snapshot.get("captured_at")) or datetime.now(timezone.utc).date()
    confirmed = bool(profile.get("confirmed_at") and profile.get("confirmed_by"))
    facts = product_facts if product_facts is not None else snapshot.get("product_facts", {})
    capabilities = {
        (item.get("capability_type"), item.get("capability_code")): item
        for item in snapshot.get("capabilities", [])
        if item.get("source_type") in {"confirmed_user", "confirmed_structured"}
        and _valid_at(item, captured)
    }
    checks = []
    for key, values, desired in [
        ("category", profile.get("primary_categories", []), category_code),
        ("export_market", profile.get("export_markets", []), market_country),
    ]:
        matched = confirmed and desired and str(desired).lower() in {str(v).lower() for v in values}
        checks.append({"factor": key, "status": "pass" if matched else "unknown", "hard": False,
                       "required": desired, "reason": "已确认范围匹配" if matched else "尚无匹配的已确认范围"})

    requirements = [item for item in policy.get("required_capabilities", [])
                    if item.get("taxonomy_code") in {None, "", taxonomy_code}]
    # Exact codes in confirmed product facts can be used; free prose never implies
    # a particular manufacturing capability.
    for kind in ("material", "process", "packaging", "certification"):
        fact = facts.get(f"{kind}_codes", {})
        try:
            valid = validate_codes(f"{kind}_codes", fact.get("value"))
        except ValueError:
            valid = []
        if fact_is_confirmed(fact) and valid:
            requirements += [{"capability_type": kind, "capability_code": code}
                             for code in fact["value"] if isinstance(code, str)]
    seen = set()
    for requirement in requirements:
        key = (requirement["capability_type"], requirement["capability_code"])
        if key in seen:
            continue
        seen.add(key)
        item = capabilities.get(key, {})
        status = {"yes": "pass", "no": "blocked"}.get(item.get("availability"), "unknown")
        checks.append({"factor": "capability", "hard": True, "required": "/".join(key),
                       "status": status, "reason": {"pass": "企业已确认具备", "blocked": "企业明确确认不具备",
                                                    "unknown": "缺少该项能力的有效确认"}[status]})
    if not requirements:
        checks.append({"factor": "manufacturing_requirements", "hard": True, "status": "unknown",
                       "reason": "尚未明确本机会所需的具体工艺、材料或认证条件"})

    for constraint in profile.get("constraints", []):
        kind = constraint.get("constraint_type")
        fact = facts.get(kind, {})
        status, reason = "unknown", "事实未确认、单位不一致或规则尚不支持"
        value, limit = number(fact.get("value")), number(constraint.get("value"))
        unit = fact.get("unit") or (fact.get("value", {}).get("currency")
                                   if isinstance(fact.get("value"), dict) else None)
        operator = constraint.get("operator")
        comparable = (kind in {"unit_cost", "factory_price", "lead_time", "moq"}
                      and confirmed and _valid_at(constraint, captured)
                      and fact_is_confirmed(fact)
                      and value is not None and limit is not None
                      and unit and unit == constraint.get("unit")
                      and operator in {"lte", "gte", "eq", "<=", ">=", "="})
        if comparable:
            passes = (value <= limit if operator in {"lte", "<="}
                      else value >= limit if operator in {"gte", ">="} else value == limit)
            status, reason = ("pass" if passes else "blocked"), "已确认事实与约束的确定性比较"
        checks.append({"factor": kind, "hard": constraint.get("hardness") == "hard",
                       "status": status, "value": value, "limit": limit,
                       "unit": unit, "operator": operator, "reason": reason})
    hard = [c for c in checks if c["hard"]]
    gate = ("blocked" if any(c["status"] == "blocked" for c in hard)
            else "unknown" if any(c["status"] == "unknown" for c in hard) else "pass")
    known = [c for c in checks if c["status"] != "unknown"]
    score = round(100 * sum(c["status"] == "pass" for c in known) / len(known), 2) if known else None
    confidence = round(len(known) / max(1, len(checks)), 4)
    return score, confidence, {"taxonomy_code": taxonomy_code, "status": gate, "blocked": gate == "blocked",
                               "signals": checks, "profile_version": snapshot.get("profile_version", 0),
                               "fit_score": score, "policy_version": policy["version"]}


def score_opportunity(factors: dict, policy: dict, fit: float | None) -> dict:
    weights = policy["weights"]
    present = {key: number(value) for key, value in factors.items()}
    used = sum(weights[key] for key, value in present.items() if value is not None)
    market = round(sum(weights[key] * min(100, max(0, value))
                       for key, value in present.items() if value is not None) / used, 2) if used else None
    alpha = policy["fit_strength"]
    adjusted = (round(market * ((1 - alpha) + alpha * fit / 100), 2)
                if market is not None and fit is not None else market)
    return {"market_score": market, "adjusted_score": adjusted, "coverage": used,
            "missing_factors": [key for key, value in present.items() if value is None],
            "used_weights": {key: round(weights[key] / used, 6) for key, value in present.items()
                             if value is not None} if used else {},
            "fit_applied": fit is not None, "policy_version": policy["version"]}
