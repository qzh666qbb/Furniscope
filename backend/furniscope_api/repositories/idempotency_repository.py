"""Tenant-scoped durable HTTP idempotency persistence."""

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    id: int
    request_hash: str
    status: Literal["processing", "completed", "failed"]
    response_status: int | None
    response_body: dict[str, Any] | None
    expires_at: datetime


class IdempotencyRepository:
    async def remove_expired_scope(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        route_code: str,
        idempotency_key: str,
    ) -> None:
        await session.execute(
            text(
                """
                DELETE FROM api_idempotency_records
                 WHERE tenant_id=:tenant_id
                   AND route_code=:route_code
                   AND idempotency_key=:idempotency_key
                   AND expires_at<=CURRENT_TIMESTAMP
                """
            ),
            {
                "tenant_id": tenant_id,
                "route_code": route_code,
                "idempotency_key": idempotency_key,
            },
        )

    async def insert_processing(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        actor_user_id: int,
        route_code: str,
        http_method: str,
        idempotency_key: str,
        request_hash: str,
        expires_at: datetime,
    ) -> IdempotencyRecord | None:
        result = await session.execute(
            text(
                """
                INSERT INTO api_idempotency_records
                  (tenant_id,actor_user_id,route_code,http_method,idempotency_key,
                   request_hash,status,expires_at)
                VALUES
                  (:tenant_id,:actor_user_id,:route_code,:http_method,:idempotency_key,
                   :request_hash,'processing',:expires_at)
                ON CONFLICT (tenant_id,route_code,idempotency_key) DO NOTHING
                RETURNING id,request_hash,status,response_status,response_body,expires_at
                """
            ),
            {
                "tenant_id": tenant_id,
                "actor_user_id": actor_user_id,
                "route_code": route_code,
                "http_method": http_method,
                "idempotency_key": idempotency_key,
                "request_hash": request_hash,
                "expires_at": expires_at,
            },
        )
        row = result.mappings().one_or_none()
        return self._record(row) if row is not None else None

    async def get_scope(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        route_code: str,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        result = await session.execute(
            text(
                """
                SELECT id,request_hash,status,response_status,response_body,expires_at
                  FROM api_idempotency_records
                 WHERE tenant_id=:tenant_id
                   AND route_code=:route_code
                   AND idempotency_key=:idempotency_key
                """
            ),
            {
                "tenant_id": tenant_id,
                "route_code": route_code,
                "idempotency_key": idempotency_key,
            },
        )
        row = result.mappings().one_or_none()
        return self._record(row) if row is not None else None

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
        terminal_status = "completed" if response_status < 400 else "failed"
        result = await session.execute(
            text(
                """
                UPDATE api_idempotency_records
                   SET status=:terminal_status,
                       response_status=:response_status,
                       response_body=CAST(:response_body AS jsonb),
                       resource_type=:resource_type,
                       resource_public_id=:resource_public_id,
                       updated_at=CURRENT_TIMESTAMP
                 WHERE id=:record_id
                   AND tenant_id=:tenant_id
                   AND status='processing'
                RETURNING id,request_hash,status,response_status,response_body,expires_at
                """
            ),
            {
                "terminal_status": terminal_status,
                "response_status": response_status,
                "response_body": json.dumps(response_body, ensure_ascii=False, separators=(",", ":")),
                "resource_type": resource_type,
                "resource_public_id": resource_public_id,
                "record_id": record_id,
                "tenant_id": tenant_id,
            },
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise RuntimeError("Idempotency record is missing, outside tenant scope, or already terminal")
        return self._record(row)

    @staticmethod
    def _record(row: Any) -> IdempotencyRecord:
        return IdempotencyRecord(
            id=int(row["id"]),
            request_hash=str(row["request_hash"]),
            status=row["status"],
            response_status=row["response_status"],
            response_body=row["response_body"],
            expires_at=row["expires_at"],
        )
