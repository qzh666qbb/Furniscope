"""Canonical request hashing and durable idempotency orchestration."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import BusinessError
from ..repositories.idempotency_repository import IdempotencyRecord, IdempotencyRepository

_SENSITIVE_KEY = re.compile(
    r"(authorization|cookie|password|token|api[_-]?key|secret|prompt|content_original|review_text|file_bytes)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+\-/]+=*")
_API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True, slots=True)
class IdempotencyDecision:
    action: Literal["execute", "replay"]
    record_id: int
    response_status: int | None = None
    response_body: dict[str, Any] | None = None


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else _sanitize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, bytes):
        return "[REDACTED_BYTES]"
    if isinstance(value, str):
        return _API_KEY.sub("[REDACTED]", _BEARER.sub("Bearer [REDACTED]", value))
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def canonical_request_hash(payload: Any) -> str:
    canonical = json.dumps(
        _sanitize(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyService:
    def __init__(self, repository: IdempotencyRepository | None = None) -> None:
        self.repository = repository or IdempotencyRepository()

    async def begin(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        actor_user_id: int,
        route_code: str,
        http_method: str,
        idempotency_key: str,
        request_payload: Any,
    ) -> IdempotencyDecision:
        method = http_method.upper()
        if tenant_id <= 0 or actor_user_id <= 0:
            raise ValueError("tenant_id and actor_user_id must be positive server-side identities")
        if route_code.startswith("API-AUTH-"):
            raise ValueError("Authentication routes must not use api_idempotency_records")
        if method not in _HTTP_METHODS:
            raise ValueError("Idempotency is only supported for business write methods")
        if not route_code or len(route_code) > 32 or not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("Invalid route_code or idempotency_key")

        request_hash = canonical_request_hash(request_payload)
        await self.repository.remove_expired_scope(
            session, tenant_id=tenant_id, route_code=route_code, idempotency_key=idempotency_key
        )
        inserted = await self.repository.insert_processing(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            route_code=route_code,
            http_method=method,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        )
        if inserted is not None:
            return IdempotencyDecision(action="execute", record_id=inserted.id)

        existing = await self.repository.get_scope(
            session, tenant_id=tenant_id, route_code=route_code, idempotency_key=idempotency_key
        )
        if existing is None:
            raise RuntimeError("Idempotency uniqueness conflict without readable tenant-scoped record")
        if existing.request_hash != request_hash:
            raise BusinessError("IDEMPOTENCY_CONFLICT", "幂等键已用于不同请求", status_code=409)
        if existing.status == "processing":
            raise BusinessError("IDEMPOTENCY_IN_PROGRESS", "相同请求正在处理中", status_code=409)
        if existing.response_status is None or existing.response_body is None:
            raise RuntimeError("Terminal idempotency record has no replay response")
        return IdempotencyDecision(
            action="replay",
            record_id=existing.id,
            response_status=existing.response_status,
            response_body=existing.response_body,
        )

    async def finish(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        record_id: int,
        response_status: int,
        response_body: dict[str, Any],
        resource_type: str | None = None,
        resource_public_id: str | None = None,
    ) -> IdempotencyRecord:
        if not 200 <= response_status <= 599:
            raise ValueError("response_status must be between 200 and 599")
        sanitized = _sanitize(response_body)
        if not isinstance(sanitized, dict):
            raise ValueError("Response Envelope must be a JSON object")
        return await self.repository.finish(
            session,
            tenant_id=tenant_id,
            record_id=record_id,
            response_status=response_status,
            response_body=sanitized,
            resource_type=resource_type,
            resource_public_id=resource_public_id,
        )
