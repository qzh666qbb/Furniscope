"""Strong ETag derived from normalized UTC updated_at; no extra database version column."""

from datetime import datetime, timezone
import hashlib


def resource_version(updated_at: datetime) -> str:
    normalized = updated_at.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return '"' + hashlib.sha256(normalized.encode()).hexdigest() + '"'


def require_version(if_match: str, updated_at: datetime) -> str:
    current = resource_version(updated_at)
    if if_match != current:
        from ..errors import BusinessError
        raise BusinessError("RESOURCE_VERSION_CONFLICT", "资源已被更新，请刷新后重试", status_code=409)
    return current
