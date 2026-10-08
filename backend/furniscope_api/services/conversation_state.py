"""Deterministic multi-turn state resolution for the analysis workspace."""

from __future__ import annotations

import re
from typing import Any

from .workbench_chat import MARKET_ALIASES, MARKET_NAMES

_REFERENCE_RE = re.compile(r"它|这个|那个|刚才(?:的)?|上一轮|继续")
_COMPARE_RE = re.compile(r"同时|比较|对比|分别")


def extract_markets(text: str) -> list[str]:
    raw = text or ""
    lowered = raw.lower()
    matches: list[tuple[int, str]] = []
    for label, code in MARKET_ALIASES:
        positions = [raw.find(label), lowered.find(label.lower())]
        if len(code) == 2:
            match = re.search(rf"(?<![a-z]){re.escape(code.lower())}(?![a-z])", lowered)
            positions.append(match.start() if match else -1)
        position = min((item for item in positions if item >= 0), default=-1)
        if position >= 0:
            matches.append((position, code))
    output: list[str] = []
    for _, code in sorted(matches):
        if code not in output:
            output.append(code)
    return output


def classify_intent(text: str) -> str:
    value = text or ""
    if re.search(r"忘记|不要记|删除记忆|别记住|清除", value):
        return "forget_memory"
    if re.search(r"暂停|先停|中断", value):
        return "pause_task"
    if re.search(r"不是.+(?:是|改成|换成)", value):
        return "correct_context"
    if _COMPARE_RE.search(value) and len(extract_markets(value)) > 1:
        return "compare_markets"
    if re.search(r"为什么|依据|证据|来源|引用", value):
        return "explain_evidence"
    if re.search(r"继续|恢复|接着", value):
        return "continue_task"
    if re.search(r"只看|先看|重点看", value):
        return "focus_analysis"
    if re.search(r"改成|调整为|换成", value):
        return "update_context"
    return "analyze"


def _explicit_product(products: list[dict[str, Any]], question: str) -> dict[str, Any] | None:
    lowered = (question or "").lower()
    matches: list[tuple[int, int, dict[str, Any]]] = []
    for item in products:
        sku = str(item.get("sku") or "").strip()
        name = str(item.get("name") or "").strip()
        for value, weight in ((sku, 2), (name, 1)):
            if not value:
                continue
            position = lowered.rfind(value.lower())
            if position >= 0:
                matches.append((position, weight, item))
    if not matches:
        return None
    return max(matches, key=lambda row: (row[0], row[1]))[2]


def resolve_conversation_state(
    *,
    persisted: dict[str, Any],
    question: str,
    products: list[dict[str, Any]],
    datasets: list[dict[str, Any]],
    configured_product_id: int | None,
    configured_dataset_ids: list[int],
    configured_task_uuids: list[str],
    requested_product_id: int | None,
    requested_dataset_id: int | None,
    requested_task_uuid: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Resolve explicit text first, then request bindings, then durable state."""
    log: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    explicit_product = _explicit_product(products, question)
    product_id = (
        explicit_product.get("product_id")
        if explicit_product else
        requested_product_id or persisted.get("current_product_id") or configured_product_id
    )
    selected_product = next(
        (item for item in products if int(item.get("product_id") or 0) == int(product_id or 0)),
        None,
    )
    if explicit_product:
        log.append({
            "field": "current_product_id",
            "source": "current_user",
            "value": product_id,
            "reason": "问题中明确出现产品名称或 SKU",
        })
    elif requested_product_id:
        log.append({
            "field": "current_product_id",
            "source": "turn_binding",
            "value": product_id,
            "reason": "本轮请求显式绑定产品",
        })
    elif product_id and _REFERENCE_RE.search(question or ""):
        references.append({
            "expression": "产品指代",
            "resolved_to": product_id,
            "source": "workspace_state",
        })
        log.append({
            "field": "current_product_id",
            "source": "workspace_state",
            "value": product_id,
            "reason": "将“它/这个/刚才那个”解析为工作台当前产品",
        })

    requested_dataset = next(
        (item for item in datasets if int(item.get("dataset_id") or 0) == int(requested_dataset_id or 0)),
        None,
    )
    explicit_markets = extract_markets(question)
    compared_markets = explicit_markets if len(explicit_markets) > 1 and _COMPARE_RE.search(question or "") else []
    requested_market = str((requested_dataset or {}).get("market_country") or "").upper()
    current_market = (
        explicit_markets[0]
        if explicit_markets else
        requested_market or persisted.get("current_market")
    )
    if explicit_markets:
        log.append({
            "field": "current_market",
            "source": "current_user",
            "value": current_market,
            "reason": "问题中明确指定目标市场",
        })
    elif requested_market:
        log.append({
            "field": "current_market",
            "source": "turn_binding",
            "value": current_market,
            "reason": "从本轮显式绑定的数据集解析市场",
        })
    elif current_market and _REFERENCE_RE.search(question or ""):
        references.append({
            "expression": "省略的目标市场",
            "resolved_to": current_market,
            "source": "workspace_state",
        })

    fallback_dataset_id = (
        requested_dataset_id
        or persisted.get("current_dataset_id")
        or (configured_dataset_ids[0] if configured_dataset_ids else None)
    )
    dataset_id = fallback_dataset_id
    if current_market:
        requested_matches_market = (
            requested_dataset
            and str(requested_dataset.get("market_country") or "").upper() == current_market
        )
        if explicit_markets and not requested_matches_market:
            category = str((selected_product or {}).get("category_code") or "")
            matching = [
                item for item in datasets
                if item.get("status") == "ready"
                and str(item.get("market_country") or "").upper() == current_market
                and (not category or not item.get("category_code") or item.get("category_code") == category)
            ]
            dataset_id = matching[0].get("dataset_id") if matching else None
            log.append({
                "field": "current_dataset_id",
                "source": "context_builder",
                "value": dataset_id,
                "reason": (
                    "市场切换后自动匹配同市场授权数据集"
                    if dataset_id else
                    "市场切换后解除不匹配的数据集绑定"
                ),
            })

    task_uuid = (
        requested_task_uuid
        or persisted.get("current_task_uuid")
        or (configured_task_uuids[0] if configured_task_uuids else None)
    )
    intent = classify_intent(question)
    stage = persisted.get("current_analysis_stage")
    if intent == "pause_task":
        stage = "pause_requested"
    elif intent == "continue_task" and stage == "pause_requested":
        stage = "resume_requested"

    state = {
        "state_uuid": persisted.get("state_uuid"),
        "revision": int(persisted.get("revision") or 0),
        "current_product_id": product_id,
        "current_product_label": (
            f"{selected_product.get('sku')} · {selected_product.get('name')}"
            if selected_product else None
        ),
        "current_market": current_market,
        "compared_markets": compared_markets,
        "current_dataset_id": dataset_id,
        "current_task_uuid": task_uuid,
        "current_analysis_stage": stage,
        "pending_confirmation": persisted.get("pending_confirmation"),
        "last_user_intent": intent,
        "resolved_references": references,
    }
    return state, log


def detect_context_conflicts(
    *,
    state: dict[str, Any],
    enterprise_profile: dict[str, Any] | None,
    memories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_market = state.get("current_market")
    if not current_market:
        return []
    values: list[dict[str, Any]] = [{
        "source": "current_user_or_workspace_state",
        "value": current_market,
        "priority": 100,
    }]
    enterprise_markets = (enterprise_profile or {}).get("export_markets") or []
    normalized_enterprise: set[str] = set()
    for item in enterprise_markets:
        raw = (
            item.get("country") or item.get("code")
            if isinstance(item, dict)
            else item
        )
        if not raw:
            continue
        parsed = extract_markets(str(raw))
        if parsed:
            normalized_enterprise.update(parsed)
            continue
        code = str(raw).strip().upper()
        if re.fullmatch(r"[A-Z]{2}", code):
            normalized_enterprise.add(code)
    if normalized_enterprise and current_market not in normalized_enterprise:
        values.append({
            "source": "enterprise_profile",
            "value": sorted(normalized_enterprise),
            "priority": 90,
        })
    memory_markets = []
    for item in memories:
        if item.get("memory_type") != "target_market":
            continue
        raw = (item.get("value") or {}).get("value")
        parsed = extract_markets(str(raw or ""))
        memory_markets.extend(parsed or ([str(raw)] if raw else []))
    memory_markets = list(dict.fromkeys(memory_markets))
    if memory_markets and current_market not in memory_markets:
        values.append({
            "source": "confirmed_customer_memory",
            "value": memory_markets,
            "priority": 80,
        })
    if len(values) == 1:
        return []
    return [{
        "field": "target_market",
        "values": values,
        "selected_source": "current_user_or_workspace_state",
        "resolution": "当前问题仅覆盖本轮/工作台状态；长期记忆和企业画像不自动改写",
    }]


def market_label(code: str | None) -> str | None:
    return MARKET_NAMES.get(code or "", code)
