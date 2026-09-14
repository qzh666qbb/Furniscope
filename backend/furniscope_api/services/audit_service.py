"""Privacy-safe audit recording for security-sensitive state changes."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class AuditService:
    async def record(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        actor_user_id: int,
        action_code: str,
        resource_type: str,
        resource_id: int | None,
        request_id: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await session.execute(
            text(
                """INSERT INTO audit_logs
                   (tenant_id,actor_user_id,action_code,resource_type,resource_id,request_id,
                    before_snapshot,after_snapshot)
                   VALUES(:tenant,:actor,:action,:resource,:resource_id,:request_id,
                          CAST(:before AS jsonb),CAST(:after AS jsonb))"""
            ),
            {
                "tenant": tenant_id,
                "actor": actor_user_id,
                "action": action_code,
                "resource": resource_type,
                "resource_id": resource_id,
                "request_id": request_id,
                "before": json.dumps(before, default=str) if before is not None else None,
                "after": json.dumps(after, default=str) if after is not None else None,
            },
        )
