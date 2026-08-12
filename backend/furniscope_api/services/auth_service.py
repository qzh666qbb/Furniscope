"""Login, Refresh rotation and current-user application service."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import ApiSettings
from ..errors import BusinessError
from ..repositories.auth_repository import AuthRepository, UserIdentity
from ..schemas.auth import AuthUser, CurrentUserResponse, LoginResponse, RefreshTokenResponse, TenantProjection
from ..security.password import PasswordService
from ..security.token_service import TokenConfigurationError, TokenService


class AuthService:
    def __init__(
        self,
        settings: ApiSettings,
        repository: AuthRepository | None = None,
        password_service: PasswordService | None = None,
        token_service: TokenService | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository or AuthRepository()
        self.password_service = password_service or PasswordService()
        self.token_service = token_service or TokenService(settings)

    async def login(self, session: AsyncSession, *, email: str, password: str) -> LoginResponse:
        identity = await self.repository.find_by_email(session, email)
        if not self.password_service.verify(password, identity.password_hash if identity else None):
            raise BusinessError("AUTH_INVALID_CREDENTIALS", "邮箱或密码错误", status_code=401)
        assert identity is not None
        self._assert_identity_active(identity)
        response = await self._issue_session(session, identity=identity, token_family_uuid=uuid4())
        await self.repository.update_last_login(
            session, user_id=identity.user_id, tenant_id=identity.tenant_id
        )
        return response

    async def refresh(self, session: AsyncSession, *, refresh_token: str) -> RefreshTokenResponse:
        token_hash = self.token_service.hash_refresh_token(refresh_token)
        stored = await self.repository.lock_refresh_session(session, token_hash)
        if stored is None:
            raise BusinessError("AUTH_REFRESH_TOKEN_INVALID", "刷新令牌无效", status_code=401)
        if stored.revoked_at is not None:
            await self.repository.revoke_family(session, stored.token_family_uuid, "reuse_detected")
            raise BusinessError("AUTH_TOKEN_REUSE_DETECTED", "检测到刷新令牌重复使用", status_code=409)
        if stored.expires_at <= datetime.now(timezone.utc):
            await self.repository.revoke_family(session, stored.token_family_uuid, "expired")
            raise BusinessError("AUTH_REFRESH_TOKEN_EXPIRED", "刷新令牌已过期", status_code=401)
        self._assert_identity_active(stored.identity)

        plain_refresh = self.token_service.issue_refresh_token()
        new_session_uuid = uuid4()
        await self.repository.rotate_refresh_session(
            session,
            old_session_uuid=stored.session_uuid,
            new_session_uuid=new_session_uuid,
            tenant_id=stored.tenant_id,
            user_id=stored.user_id,
            token_family_uuid=stored.token_family_uuid,
            refresh_token_hash=self.token_service.hash_refresh_token(plain_refresh),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.settings.refresh_token_ttl_seconds),
        )
        access_token = self._access(stored.identity)
        return RefreshTokenResponse(access_token=access_token, refresh_token=plain_refresh)

    async def current_user(
        self, session: AsyncSession, *, user_id: int, tenant_id: int
    ) -> CurrentUserResponse:
        identity = await self.repository.find_by_scope(session, user_id=user_id, tenant_id=tenant_id)
        if identity is None:
            raise BusinessError("TENANT_CONTEXT_MISMATCH", "租户上下文不匹配", status_code=403)
        self._assert_identity_active(identity)
        return CurrentUserResponse(
            **self._user(identity).model_dump(),
            tenant=TenantProjection(
                tenant_code=identity.tenant_code,
                name=identity.tenant_name,
                default_timezone=identity.default_timezone,
                default_currency=identity.default_currency,
            ),
        )

    async def _issue_session(
        self, session: AsyncSession, *, identity: UserIdentity, token_family_uuid
    ) -> LoginResponse:
        refresh_token = self.token_service.issue_refresh_token()
        await self.repository.create_refresh_session(
            session,
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            refresh_token_hash=self.token_service.hash_refresh_token(refresh_token),
            token_family_uuid=token_family_uuid,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.settings.refresh_token_ttl_seconds),
            session_uuid=uuid4(),
        )
        return LoginResponse(
            access_token=self._access(identity),
            refresh_token=refresh_token,
            user=self._user(identity),
        )

    def _access(self, identity: UserIdentity) -> str:
        try:
            return self.token_service.issue_access_token(
                user_id=identity.user_id, tenant_id=identity.tenant_id, role_code=identity.role_code
            )
        except TokenConfigurationError as exc:
            raise BusinessError("INTERNAL_SERVER_ERROR", "认证服务配置错误", status_code=500) from exc

    @staticmethod
    def _user(identity: UserIdentity) -> AuthUser:
        return AuthUser(
            user_id=identity.user_id,
            email=identity.email,
            name=identity.name,
            role_code=identity.role_code,
            status="active",
        )

    @staticmethod
    def _assert_identity_active(identity: UserIdentity) -> None:
        if identity.tenant_status != "active":
            raise BusinessError("TENANT_SUSPENDED", "租户当前不可用", status_code=403)
        if identity.user_status != "active":
            raise BusinessError("USER_DISABLED", "用户当前不可用", status_code=403)
        if identity.role_code not in {"user", "admin"}:
            raise BusinessError("AUTH_TOKEN_INVALID", "用户角色无效", status_code=401)
