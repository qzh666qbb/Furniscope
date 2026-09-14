"""Tenant notification channel endpoints for competitor/policy alerts."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession
from ..schemas import SuccessEnvelope
from ..schemas.notifications import (
    NotificationChannelCreateRequest,
    NotificationChannelItem,
    NotificationChannelUpdateRequest,
    NotificationEventItem,
    NotificationTestResult,
)
from ..services.notifications import NotificationService

router = APIRouter(prefix="/api/v1/notification-channels", tags=["Alert Notifications"])


def _service(request: Request) -> NotificationService:
    return NotificationService(request.app.state.settings)


@router.get("", response_model=SuccessEnvelope[list[NotificationChannelItem]], summary="通知渠道列表")
async def list_channels(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    items = await _service(request).list_channels(session, tenant_id=principal.tenant_id)
    return SuccessEnvelope(data=[NotificationChannelItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.post("", response_model=SuccessEnvelope[NotificationChannelItem], status_code=201,
             summary="创建通知渠道")
async def create_channel(body: NotificationChannelCreateRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    item = await _service(request).create_channel(session, tenant_id=principal.tenant_id,
        user_id=principal.user_id, payload=body.model_dump(mode="python"))
    await session.commit()
    return SuccessEnvelope(data=NotificationChannelItem.model_validate(item), request_id=request.state.request_id)


@router.patch("/{channel_id}", response_model=SuccessEnvelope[NotificationChannelItem], summary="启停通知渠道")
async def update_channel(channel_id: int, body: NotificationChannelUpdateRequest, request: Request,
    session: DatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    item = await _service(request).update_channel(session, tenant_id=principal.tenant_id,
        channel_id=channel_id, payload=body.model_dump(mode="python", exclude_unset=True))
    await session.commit()
    return SuccessEnvelope(data=NotificationChannelItem.model_validate(item), request_id=request.state.request_id)


@router.delete("/{channel_id}", response_model=SuccessEnvelope[dict], summary="删除通知渠道")
async def delete_channel(channel_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    await _service(request).delete_channel(session, tenant_id=principal.tenant_id, channel_id=channel_id)
    await session.commit()
    return SuccessEnvelope(data={"channel_id": channel_id, "deleted": True}, request_id=request.state.request_id)


@router.post("/{channel_id}/test", response_model=SuccessEnvelope[NotificationTestResult], status_code=202,
             summary="发送通知渠道测试")
async def test_channel(channel_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    result = await _service(request).queue_test(session, tenant_id=principal.tenant_id, channel_id=channel_id)
    await session.commit()
    return SuccessEnvelope(data=NotificationTestResult.model_validate(result), request_id=request.state.request_id)


@router.get("/events", response_model=SuccessEnvelope[list[NotificationEventItem]], summary="通知投递记录")
async def list_events(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50):
    items = await _service(request).list_events(session, tenant_id=principal.tenant_id, limit=limit)
    return SuccessEnvelope(data=[NotificationEventItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)
