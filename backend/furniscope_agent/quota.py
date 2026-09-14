"""Detect provider quota / balance exhaustion so chat can fall back."""

from __future__ import annotations

_QUOTA_MARKERS = (
    "insufficient_quota",
    "insufficient quota",
    "quota exceeded",
    "quota_exceeded",
    "no remaining quota",
    "exceeded your current quota",
    "arrearage",
    "allocationquota",
    "accountoverdue",
    "insufficientbalance",
    "余额不足",
    "额度不足",
    "欠费",
    "配额不足",
    "套餐额度",
    "无可用额度",
)


class ModelQuotaExhausted(RuntimeError):
    """The current provider rejected the call because the plan/quota is empty."""


def is_quota_exhausted(status_code: int | None, body: str | None) -> bool:
    if status_code == 402:
        return True
    haystack = (body or "").lower()
    if not haystack:
        return False
    return any(marker in haystack for marker in _QUOTA_MARKERS)
