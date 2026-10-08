"""Small, explicit customer-memory layer for grounded conversational context.

Only high-signal statements are promoted automatically. Everything else remains
in the transcript and can be reviewed later; this avoids turning guesses into
durable customer facts.
"""

from __future__ import annotations

import re
from typing import Any

MEMORY_PATTERNS = (
    ("target_market", re.compile(r"(?:主要|重点|目标)(?:做|市场)?(?:改成|调整为|是|为)\s*([^，。；;\n]{2,30})")),
    ("budget", re.compile(r"(?:预算|预算上限)\s*(?:改成|调整为|是|为)?\s*([^，。；;\n]{1,30})")),
    ("unit_cost_limit", re.compile(r"(?:成本上限|单位成本|成本不能超过)\s*(?:改成|调整为|是|为|不超过)?\s*([^，。；;\n]{1,30})")),
    ("preferred_channel", re.compile(r"(?:渠道偏好|主要渠道|销售渠道)\s*(?:是|为)?\s*([^，。；;\n]{2,30})")),
    ("customer_preference", re.compile(r"(?:偏好|希望|更看重|重点关注)\s*([^，。；;\n]{2,60})")),
)


def extract_memory_candidates(text: str) -> list[dict[str, Any]]:
    """Extract only explicit preference/constraint statements from one turn."""
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, pattern in MEMORY_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        value = match.group(1).strip(" \t:：\"'")
        if not value or key in seen:
            continue
        seen.add(key)
        memory_value: dict[str, Any] = {"value": value}
        if key in {"budget", "unit_cost_limit"}:
            amount = re.search(r"(\d+(?:\.\d+)?)", value.replace(",", ""))
            if amount:
                memory_value["amount"] = float(amount.group(1))
                if memory_value["amount"].is_integer():
                    memory_value["amount"] = int(memory_value["amount"])
                memory_value["currency"] = (
                    "USD" if re.search(r"美元|USD|\$", value, re.I)
                    else "CNY" if re.search(r"人民币|CNY|元", value, re.I)
                    else None
                )
                memory_value["operator"] = "lte"
        output.append({"memory_key": key, "memory_value": memory_value, "confidence": 0.92})
    return output


def memory_context(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "memory_key": row.get("memory_key"),
            "value": (row.get("memory_value") or {}).get("value", row.get("memory_value")),
            "source": row.get("source", "conversation"),
        }
        for row in rows
    ]


def requested_forget_keys(text: str, known_keys: list[str] | None = None) -> list[str]:
    """Recognize explicit forget requests; never delete on an ambiguous question."""
    if not re.search(r"忘记|不要记|删除记忆|别记住|清除", text or ""):
        return []
    keys = known_keys or [key for key, _ in MEMORY_PATTERNS]
    aliases = {
        "市场": "target_market", "目标市场": "target_market", "预算": "budget",
        "成本": "unit_cost_limit", "渠道": "preferred_channel", "偏好": "customer_preference",
    }
    result = []
    for label, key in aliases.items():
        if label in (text or "") and key in keys:
            result.append(key)
    return result if result else keys
