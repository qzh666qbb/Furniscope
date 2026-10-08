"""RS256 access-token verification and database-backed authorization context."""

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import text

from .config import ApiSettings
from .context import TenantContext
from .database import TenantPlacementUnavailable, bind_tenant_session
from .dependencies import DatabaseSession
from .errors import BusinessError

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    tenant_id: int
    user_id: int
    role_code: Literal["user", "admin"]
    jti_digest: str
    tenant_role_codes: tuple[str, ...]
    permissions: frozenset[str]

    def has_permission(self, permission_code: str) -> bool:
        return "*" in self.permissions or permission_code in self.permissions


def _public_keys(settings: ApiSettings) -> dict[str, str]:
    secret = settings.furniscope_jwt_public_keys_json
    if secret is None:
        raise BusinessError("AUTH_TOKEN_INVALID", "访问令牌无效", status_code=401)
    try:
        parsed = json.loads(secret.get_secret_value())
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BusinessError("AUTH_TOKEN_INVALID", "访问令牌无效", status_code=401) from exc
    if not isinstance(parsed, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
        raise BusinessError("AUTH_TOKEN_INVALID", "访问令牌无效", status_code=401)
    return parsed


def decode_access_token(token: str, settings: ApiSettings) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise InvalidTokenError("invalid header")
        key = _public_keys(settings).get(header["kid"])
        if key is None:
            raise InvalidTokenError("unknown kid")
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            issuer=settings.furniscope_jwt_issuer,
            audience=settings.furniscope_jwt_audience,
            leeway=settings.jwt_clock_skew_seconds,
            options={"require": ["sub", "user_id", "tenant_id", "role_code", "iat", "nbf", "exp", "jti"]},
        )
        user_id = int(claims["user_id"])
        tenant_id = int(claims["tenant_id"])
        if claims["sub"] != str(user_id) or claims["role_code"] not in {"user", "admin"} or tenant_id <= 0 or user_id <= 0:
            raise InvalidTokenError("invalid identity scope")
        return claims
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise BusinessError("AUTH_TOKEN_INVALID", "访问令牌无效", status_code=401) from exc


async def resolve_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    request: Request,
    session: DatabaseSession,
) -> AuthenticatedPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise BusinessError("AUTH_TOKEN_INVALID", "访问令牌无效", status_code=401)
    settings: ApiSettings = request.app.state.settings
    claims = decode_access_token(credentials.credentials, settings)
    result = await session.execute(
        text(
            """
            SELECT u.id AS user_id, u.tenant_id, u.role_code, u.status AS user_status,
                   t.status AS tenant_status,
                   COALESCE((
                     SELECT array_agg(DISTINCT r.role_code ORDER BY r.role_code)
                       FROM user_role_assignments a
                       JOIN tenant_roles r
                         ON r.id=a.role_id AND r.tenant_id=a.tenant_id
                      WHERE a.tenant_id=u.tenant_id AND a.user_id=u.id
                        AND (a.expires_at IS NULL OR a.expires_at>now())
                   ),ARRAY[]::varchar[]) AS tenant_role_codes,
                   COALESCE((
                     SELECT array_agg(DISTINCT rp.permission_code ORDER BY rp.permission_code)
                       FROM user_role_assignments a
                       JOIN role_permissions rp
                         ON rp.role_id=a.role_id AND rp.tenant_id=a.tenant_id
                      WHERE a.tenant_id=u.tenant_id AND a.user_id=u.id
                        AND (a.expires_at IS NULL OR a.expires_at>now())
                   ),ARRAY[]::varchar[]) AS permissions
              FROM users u
              JOIN tenants t ON t.id = u.tenant_id
             WHERE u.id = :user_id AND u.tenant_id = :tenant_id
            """
        ),
        {"user_id": int(claims["user_id"]), "tenant_id": int(claims["tenant_id"])},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise BusinessError("TENANT_CONTEXT_MISMATCH", "租户上下文不匹配", status_code=403)
    if row["tenant_status"] != "active":
        raise BusinessError("TENANT_SUSPENDED", "租户当前不可用", status_code=403)
    if row["user_status"] != "active":
        raise BusinessError("USER_DISABLED", "用户当前不可用", status_code=403)
    if row["role_code"] != claims["role_code"]:
        raise BusinessError("AUTH_TOKEN_INVALID", "访问令牌无效", status_code=401)
    digest = hashlib.sha256(str(claims["jti"]).encode()).hexdigest()[:12]
    tenant_role_codes = (
        ("platform_admin",) if row["role_code"] == "admin"
        else tuple(row["tenant_role_codes"])
    )
    permissions = (
        frozenset({"*"}) if row["role_code"] == "admin"
        else frozenset(row["permissions"])
    )
    principal = AuthenticatedPrincipal(
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        role_code=row["role_code"],
        jti_digest=digest,
        tenant_role_codes=tenant_role_codes,
        permissions=permissions,
    )
    request.state.tenant_context = TenantContext(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        role_code=principal.role_code,
        token_jti_digest=principal.jti_digest,
    )
    if principal.role_code == "user":
        try:
            await bind_tenant_session(session, principal.tenant_id)
        except TenantPlacementUnavailable as exc:
            if exc.actual_cell and exc.actual_cell != exc.expected_cell:
                raise BusinessError(
                    "TENANT_CELL_MISMATCH",
                    "企业数据已迁移到其他服务单元，请通过最新路由重新访问",
                    status_code=421,
                    details=[{
                        "expected_cell": exc.expected_cell,
                        "actual_cell": exc.actual_cell,
                    }],
                ) from exc
            raise BusinessError(
                "TENANT_MIGRATION_IN_PROGRESS",
                "企业数据正在迁移，当前暂不可写入",
                status_code=423,
                details=[{"placement_status": exc.status}],
            ) from exc
    return principal


async def require_user_or_admin(
    principal: Annotated[AuthenticatedPrincipal, Depends(resolve_principal)],
) -> AuthenticatedPrincipal:
    return principal


async def require_user(
    principal: Annotated[AuthenticatedPrincipal, Depends(resolve_principal)],
) -> AuthenticatedPrincipal:
    """Enterprise product APIs are for tenant users only; platform admins use /api/v1/admin/*."""
    if principal.role_code != "user":
        raise BusinessError("PERMISSION_DENIED", "当前角色无权执行该业务操作", status_code=403)
    return principal


async def require_admin(
    principal: Annotated[AuthenticatedPrincipal, Depends(resolve_principal)],
) -> AuthenticatedPrincipal:
    if principal.role_code != "admin":
        raise BusinessError("ADMIN_REQUIRED", "需要管理员权限", status_code=403)
    return principal


def require_permission(permission_code: str):
    async def dependency(
        principal: Annotated[AuthenticatedPrincipal, Depends(resolve_principal)],
    ) -> AuthenticatedPrincipal:
        if principal.role_code != "user" or not principal.has_permission(permission_code):
            raise BusinessError(
                "PERMISSION_DENIED",
                f"缺少企业权限：{permission_code}",
                status_code=403,
            )
        return principal

    dependency.__name__ = f"require_{permission_code.replace('.', '_')}"
    return dependency
