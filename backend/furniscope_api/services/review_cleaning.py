"""Rule-based review cleaning and sentiment tagging for market imports."""

from __future__ import annotations

import hashlib
import re
from typing import Any

EMPTY = "empty"
DUPLICATE = "duplicate"
GARBLED = "garbled"
SPAM = "spam"

REASON_LABELS = {
    EMPTY: "空评",
    DUPLICATE: "重复评论",
    GARBLED: "乱码",
    SPAM: "无意义水文",
}

_SPAM_PHRASES = {
    "good", "nice", "ok", "okay", "fine", "yes", "cool", "thanks",
    "好", "不错", "可以", "还行", "哈哈", "嘻嘻", "嗯嗯", "666", "111",
}
_POSITIVE = {
    "comfortable", "love", "great", "excellent", "perfect", "sturdy", "worth",
    "recommend", "amazing", "soft", "quality", "solid", "beautiful",
    "舒服", "喜欢", "推荐", "满意", "结实", "值得", "好看", "舒适", "耐用",
}
_NEGATIVE = {
    "broken", "cheap", "uncomfortable", "disappointed", "waste", "poor",
    "wobbly", "smell", "return", "fragile", "scratch",
    "失望", "质量差", "退货", "摇晃", "难闻", "便宜货", "后悔", "开裂",
}
_WORD = re.compile(r"[\w\u4e00-\u9fff]+")
_MEANINGFUL = re.compile(r"[\w\u4e00-\u9fff]")
_MOJIBAKE = re.compile(r"(Ã.|Â.|åœ|ä¸|è¿|é¸|æ˜|çš)")


def normalize_review_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_review_text(text).encode("utf-8")).hexdigest()


def unique_invalid_hash(platform_review_id: str, text: str) -> str:
    payload = f"{platform_review_id}:{normalize_review_text(text)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def review_invalid_reason(text: str, seen_hashes: set[str]) -> str | None:
    stripped = (text or "").strip()
    compact = re.sub(r"\s+", "", stripped)
    if _is_garbled(stripped):
        return GARBLED
    if len(compact) < 6 or not _MEANINGFUL.search(stripped):
        return EMPTY
    if _is_spam(stripped, compact):
        return SPAM
    digest = content_hash(stripped)
    if digest in seen_hashes:
        return DUPLICATE
    seen_hashes.add(digest)
    return None


def infer_sentiment(text: str, rating: Any = None) -> str:
    blob = (text or "").lower()
    positive = sum(1 for word in _POSITIVE if word in blob)
    negative = sum(1 for word in _NEGATIVE if word in blob)
    if positive > negative and positive > 0:
        return "positive"
    if negative > positive and negative > 0:
        return "negative"
    if positive and negative:
        return "neutral"
    try:
        score = float(rating)
    except (TypeError, ValueError):
        return "neutral"
    if score >= 4:
        return "positive"
    if score <= 2:
        return "negative"
    return "neutral"


def _is_garbled(text: str) -> bool:
    if "\ufffd" in text or any(ord(char) < 9 for char in text):
        return True
    letters = sum(1 for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    if len(text) >= 8 and letters / len(text) < 0.35:
        return True
    return bool(_MOJIBAKE.search(text) and letters < 8)


def _is_spam(text: str, compact: str) -> bool:
    if len(compact) >= 6 and len(set(compact)) <= 2:
        return True
    tokens = _WORD.findall(text.lower())
    if tokens and all(token in _SPAM_PHRASES or token.isdigit() for token in tokens) and len(compact) < 20:
        return True
    return False
