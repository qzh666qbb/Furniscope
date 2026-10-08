#!/usr/bin/env python3
"""Run the deterministic portion of the FurniScope memory-agent eval set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from furniscope_api.services.conversation_state import resolve_conversation_state
from furniscope_api.services.customer_memory import extract_memory_candidates
from furniscope_api.services.workbench_chat import workbench_system_prompt


def _ratio(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 1.0


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    state_checks = 0
    state_passed = 0
    reference_passed = 0
    intent_passed = 0
    state_results = []
    for case in payload["state_cases"]:
        state, _ = resolve_conversation_state(
            persisted=case["persisted"],
            question=case["question"],
            products=case["products"],
            datasets=case["datasets"],
            configured_product_id=None,
            configured_dataset_ids=[],
            configured_task_uuids=[],
            requested_product_id=case.get("requested_product_id"),
            requested_dataset_id=case.get("requested_dataset_id"),
            requested_task_uuid=case.get("requested_task_uuid"),
        )
        expected = case["expected"]
        fields = {
            "product_id": state.get("current_product_id"),
            "market": state.get("current_market"),
            "dataset_id": state.get("current_dataset_id"),
        }
        expected_fields = {name: expected.get(name) for name in fields}
        field_passes = sum(fields[name] == expected_fields[name] for name in fields)
        state_checks += len(fields)
        state_passed += field_passes
        reference_ok = len(state.get("resolved_references") or []) == expected["reference_count"]
        intent_ok = state.get("last_user_intent") == expected["intent"]
        reference_passed += int(reference_ok)
        intent_passed += int(intent_ok)
        optional_ok = (
            ("compared_markets" not in expected or state["compared_markets"] == expected["compared_markets"])
            and ("analysis_stage" not in expected or state["current_analysis_stage"] == expected["analysis_stage"])
        )
        state_results.append({
            "id": case["id"],
            "passed": field_passes == len(fields) and reference_ok and intent_ok and optional_ok,
            "resolved": state,
        })

    memory_passed = 0
    false_writes = 0
    predicted_writes = 0
    memory_results = []
    for case in payload["memory_cases"]:
        extracted = extract_memory_candidates(case["question"])
        predicted = (
            []
            if case["memory_mode"] == "temporary"
            else [item["memory_key"] for item in extracted]
        )
        expected = case["expected_keys"]
        unexpected = set(predicted) - set(expected)
        predicted_writes += len(predicted)
        false_writes += len(unexpected)
        passed = predicted == expected
        memory_passed += int(passed)
        memory_results.append({"id": case["id"], "passed": passed, "predicted_keys": predicted})

    security_passed = 0
    security_results = []
    for case in payload["security_cases"]:
        prompt = workbench_system_prompt(
            {"knowledge_matches": [{"chunk_text": case["knowledge_text"]}]},
            streaming=True,
        )
        passed = case["required_guard"] in prompt and case["knowledge_text"] in prompt
        security_passed += int(passed)
        security_results.append({"id": case["id"], "passed": passed})

    metrics = {
        "product_market_identification_accuracy": _ratio(state_passed, state_checks),
        "context_reference_accuracy": _ratio(reference_passed, len(payload["state_cases"])),
        "intent_accuracy": _ratio(intent_passed, len(payload["state_cases"])),
        "memory_write_accuracy": _ratio(memory_passed, len(payload["memory_cases"])),
        "memory_false_write_rate": _ratio(false_writes, predicted_writes),
        "prompt_injection_guard_pass_rate": _ratio(
            security_passed,
            len(payload["security_cases"]),
        ),
    }
    passed = (
        all(item["passed"] for item in state_results)
        and all(item["passed"] for item in memory_results)
        and all(item["passed"] for item in security_results)
        and metrics["memory_false_write_rate"] == 0
    )
    return {
        "protocol": payload["version"],
        "passed": passed,
        "metrics": metrics,
        "cases": {
            "state": state_results,
            "memory": memory_results,
            "security": security_results,
        },
        "integration_metrics": {
            "citation_correctness": "covered_by_postgresql_lifecycle_test",
            "cross_tenant_leakage_rate": "covered_by_tenant_boundary_tests",
            "sse_recovery_success_rate": "covered_by_contract_e2e",
            "response_latency_ms": "requires_deployed_model_run",
            "token_cost": "requires_deployed_model_run",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "memory_agent_eval.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(json.loads(args.fixture.read_text(encoding="utf-8")))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
