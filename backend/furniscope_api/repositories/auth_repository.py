"""Authentication database access with row locks for Refresh rotation."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class UserIdentity:
    user_id: int
    tenant_id: int
    email: str
    password_hash: str
    name: str
    role_code: str
    user_status: str
    tenant_status: str
    tenant_code: str
    tenant_name: str
    default_timezone: str
    default_currency: str


@dataclass(frozen=True, slots=True)
class RefreshSession:
    session_uuid: UUID
    tenant_id: int
    user_id: int
    token_family_uuid: UUID
    expires_at: datetime
    revoked_at: datetime | None
    revoke_reason: str | None
    identity: UserIdentity


class AuthRepository:
    _identity_select = """
        SELECT u.id AS user_id,u.tenant_id,u.email,u.password_hash,u.name,u.role_code,
               u.status AS user_status,t.status AS tenant_status,t.tenant_code,
               t.name AS tenant_name,t.default_timezone,t.default_currency
          FROM users u JOIN tenants t ON t.id=u.tenant_id
    """

    async def find_by_email(self, session: AsyncSession, email: str) -> UserIdentity | None:
        result = await session.execute(
            text(self._identity_select + " WHERE u.email=:email"), {"email": email}
        )
        row = result.mappings().one_or_none()
        return self._identity(row) if row is not None else None

    async def find_by_scope(
        self, session: AsyncSession, *, user_id: int, tenant_id: int
    ) -> UserIdentity | None:
        result = await session.execute(
            text(self._identity_select + " WHERE u.id=:user_id AND u.tenant_id=:tenant_id"),
            {"user_id": user_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return self._identity(row) if row is not None else None

    async def update_last_login(self, session: AsyncSession, *, user_id: int, tenant_id: int) -> None:
        await session.execute(
            text(
                "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=:user_id AND tenant_id=:tenant_id"
            ),
            {"user_id": user_id, "tenant_id": tenant_id},
        )

    async def create_refresh_session(
        self,
        session: AsyncSession,
        *,
        tenant_id: int,
        user_id: int,
        refresh_token_hash: str,
        token_family_uuid: UUID,
        expires_at: datetime,
        session_uuid: UUID,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO auth_sessions
                  (session_uuid,tenant_id,user_id,refresh_token_hash,token_family_uuid,expires_at)
                VALUES
                  (:session_uuid,:tenant_id,:user_id,:refresh_token_hash,:token_family_uuid,:expires_at)
                """
            ),
            {
                "session_uuid": session_uuid,
                "tenant_id": tenant_id,
                "user_id": user_id,
                "refresh_token_hash": refresh_token_hash,
                "token_family_uuid": token_family_uuid,
                "expires_at": expires_at,
            },
        )

    async def lock_refresh_session(self, session: AsyncSession, token_hash: str) -> RefreshSession | None:
        result = await session.execute(
            text(
                """
                SELECT s.session_uuid,s.tenant_id,s.user_id,s.token_family_uuid,s.expires_at,
                       s.revoked_at,s.revoke_reason,
                       u.email,u.password_hash,u.name,u.role_code,u.status AS user_status,
                       t.status AS tenant_status,t.tenant_code,t.name AS tenant_name,
                       t.default_timezone,t.default_currency
                  FROM auth_sessions s
                  JOIN users u ON u.id=s.user_id AND u.tenant_id=s.tenant_id
                  JOIN tenants t ON t.id=s.tenant_id
                 WHERE s.refresh_token_hash=:token_hash
                 FOR UPDATE OF s
                """
            ),
            {"token_hash": token_hash},
        )
        row = result.mappings().one_or_none()
        if row is None:
            return None
        identity = self._identity({**row, "user_id": row["user_id"], "tenant_id": row["tenant_id"]})
        return RefreshSession(
            session_uuid=row["session_uuid"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            token_family_uuid=row["token_family_uuid"],
            expires_at=row["expires_at"],
            revoked_at=row["revoked_at"],
            revoke_reason=row["revoke_reason"],
            identity=identity,
        )

    async def rotate_refresh_session(
        self,
        session: AsyncSession,
        *,
        old_session_uuid: UUID,
        new_session_uuid: UUID,
        tenant_id: int,
        user_id: int,
        token_family_uuid: UUID,
        refresh_token_hash: str,
        expires_at: datetime,
    ) -> None:
        await session.execute(
            text(
                """
                UPDATE auth_sessions
                   SET last_used_at=CURRENT_TIMESTAMP,revoked_at=CURRENT_TIMESTAMP,
                       revoke_reason='rotated',replaced_by_session_uuid=:new_session_uuid
                 WHERE session_uuid=:old_session_uuid AND revoked_at IS NULL
                """
            ),
            {"old_session_uuid": old_session_uuid, "new_session_uuid": new_session_uuid},
        )
        await self.create_refresh_session(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            token_family_uuid=token_family_uuid,
            expires_at=expires_at,
            session_uuid=new_session_uuid,
        )

    async def revoke_family(self, session: AsyncSession, token_family_uuid: UUID, reason: str) -> None:
        await session.execute(
            text(
                """
                UPDATE auth_sessions
                   SET revoked_at=COALESCE(revoked_at,CURRENT_TIMESTAMP),
                       revoke_reason=:reason
                 WHERE token_family_uuid=:family
                """
            ),
            {"family": token_family_uuid, "reason": reason},
        )

    @staticmethod
    def _identity(row: Any) -> UserIdentity:
        return UserIdentity(
            user_id=int(row["user_id"]), tenant_id=int(row["tenant_id"]), email=row["email"],
            password_hash=row["password_hash"], name=row["name"], role_code=row["role_code"],
            user_status=row["user_status"], tenant_status=row["tenant_status"],
            tenant_code=row["tenant_code"], tenant_name=row["tenant_name"],
            default_timezone=row["default_timezone"], default_currency=row["default_currency"],
        )
