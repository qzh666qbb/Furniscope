"""API-AUTH-01/02/03 routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..auth import AuthenticatedPrincipal, require_user_or_admin
from ..dependencies import DatabaseSession
from ..errors import BusinessError
from ..schemas import SuccessEnvelope
from ..schemas.auth import CurrentUserResponse, LoginRequest, LoginResponse, RefreshRequest, RefreshTokenResponse
from ..services.auth_service import AuthService

router = APIRouter(prefix="/api/v1", tags=["Authentication"])


@router.post(
    "/auth/login", response_model=SuccessEnvelope[LoginResponse],
    operation_id="API-AUTH-01", summary="用户登录"
)
async def login(body: LoginRequest, request: Request, session: DatabaseSession):
    try:
        data = await AuthService(request.app.state.settings).login(
            session, email=body.email, password=body.password
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post(
    "/auth/refresh", response_model=SuccessEnvelope[RefreshTokenResponse],
    operation_id="API-AUTH-02", summary="刷新访问令牌"
)
async def refresh(body: RefreshRequest, request: Request, session: DatabaseSession):
    try:
        data = await AuthService(request.app.state.settings).refresh(
            session, refresh_token=body.refresh_token
        )
        await session.commit()
    except BusinessError as exc:
        if exc.code in {"AUTH_TOKEN_REUSE_DETECTED", "AUTH_REFRESH_TOKEN_EXPIRED"}:
            await session.commit()
        else:
            await session.rollback()
        raise
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get(
    "/users/me", response_model=SuccessEnvelope[CurrentUserResponse],
    operation_id="API-AUTH-03", summary="获取当前用户"
)
async def current_user(
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user_or_admin)],
):
    data = await AuthService(request.app.state.settings).current_user(
        session, user_id=principal.user_id, tenant_id=principal.tenant_id
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)
