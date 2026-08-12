"""Structured logging with deterministic secret and sensitive-text redaction."""

import json
import logging
import re
from typing import Any

from .context import request_id_context

_SENSITIVE_KEY = re.compile(
    r"(authorization|cookie|password|token|api[_-]?key|secret|prompt|content_original|review_text)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+\-/]+=*")
_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _API_KEY.sub("[REDACTED]", _BEARER.sub("Bearer [REDACTED]", value))
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", None) or request_id_context.get(),
        }
        for key in ("method", "path", "status_code", "duration_ms", "error_type"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = redact(value)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> logging.Logger:
    logger = logging.getLogger("furniscope.api")
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger
