"""Authorized collection, sentiment stream and policy alert endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..schemas import SuccessEnvelope
from ..schemas.signals import (
    AuthorizedSourceCreateRequest,
    AuthorizedSourceItem,
    AuthorizedSourceUpdateRequest,
    CollectRunResult,
    CollectUrlRequest,
    MarketSignalOverview,
    PolicyAlertItem,
    PolicyFetchResult,
    PolicySourceCreateRequest,
    PolicySourceItem,
    PolicySourceUpdateRequest,
    SentimentEventItem,
    SentimentFeedPage,
    SentimentIngestRequest,
    SentimentIngestResult,
)
from ..services.authorized_signals import AuthorizedSignalService

router = APIRouter(prefix="/api/v1/market-signals", tags=["Authorized Market Signals"])


def _service(request: Request) -> AuthorizedSignalService:
    return AuthorizedSignalService(request.app.state.settings)


@router.get("/overview", response_model=SuccessEnvelope[MarketSignalOverview], summary="授权信号总览")
async def signal_overview(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await _service(request).overview(session, tenant_id=principal.tenant_id)
    return SuccessEnvelope(data=MarketSignalOverview.model_validate(data), request_id=request.state.request_id)


@router.get("/sources", response_model=SuccessEnvelope[list[AuthorizedSourceItem]], summary="授权采集源列表")
async def list_sources(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    items = await _service(request).list_sources(session, tenant_id=principal.tenant_id)
    return SuccessEnvelope(data=[AuthorizedSourceItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.post("/sources", response_model=SuccessEnvelope[AuthorizedSourceItem], status_code=201,
             summary="创建授权采集源")
async def create_source(body: AuthorizedSourceCreateRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    item = await _service(request).create_source(session, tenant_id=principal.tenant_id,
        user_id=principal.user_id, payload=body.model_dump(mode="python"))
    await session.commit()
    return SuccessEnvelope(data=AuthorizedSourceItem.model_validate(item), request_id=request.state.request_id)


@router.patch("/sources/{source_id}", response_model=SuccessEnvelope[AuthorizedSourceItem],
              summary="启停授权采集源")
async def update_source(source_id: int, body: AuthorizedSourceUpdateRequest, request: Request,
    session: DatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    item = await _service(request).update_source(session, tenant_id=principal.tenant_id,
        source_id=source_id, payload=body.model_dump(mode="python", exclude_unset=True))
    await session.commit()
    return SuccessEnvelope(data=AuthorizedSourceItem.model_validate(item), request_id=request.state.request_id)


@router.delete("/sources/{source_id}", response_model=SuccessEnvelope[dict], summary="删除授权采集源")
async def delete_source(source_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    await _service(request).delete_source(session, tenant_id=principal.tenant_id, source_id=source_id)
    await session.commit()
    return SuccessEnvelope(data={"source_id": source_id, "deleted": True}, request_id=request.state.request_id)


@router.post("/sources/{source_id}/fetch", response_model=SuccessEnvelope[CollectRunResult],
             summary="手动拉取授权采集源")
async def fetch_source(source_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    result = await _service(request).fetch_source(session, tenant_id=principal.tenant_id, source_id=source_id)
    await session.commit()
    return SuccessEnvelope(data=CollectRunResult.model_validate(result), request_id=request.state.request_id)


@router.post("/collect-url", response_model=SuccessEnvelope[CollectRunResult],
             summary="粘贴网址后立即采集商品或评论")
async def collect_url(body: CollectUrlRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    result = await _service(request).collect_from_url(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id,
        payload=body.model_dump(mode="python"),
    )
    await session.commit()
    return SuccessEnvelope(data=CollectRunResult.model_validate(result), request_id=request.state.request_id)


@router.get("/sentiment-events", response_model=SuccessEnvelope[list[SentimentEventItem]],
            summary="查询授权舆情事件流")
async def list_sentiment_events(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    sentiment: Annotated[str | None, Query(pattern="^(positive|neutral|negative)$")] = None,
    market_country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50):
    items = await _service(request).list_sentiment_events(session, tenant_id=principal.tenant_id,
        sentiment=sentiment, market_country=market_country, query=q, limit=limit)
    return SuccessEnvelope(data=[SentimentEventItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.get("/sentiment-feed", response_model=SuccessEnvelope[SentimentFeedPage],
            summary="市场评论库与实时采集舆情")
async def list_sentiment_feed(request: Request, session: DatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    sentiment: Annotated[str | None, Query(pattern="^(positive|neutral|negative)$")] = None,
    market_country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    dataset_id: Annotated[int | None, Query(ge=1)] = None,
    origin: Annotated[str, Query(pattern="^(all|live|dataset)$")] = "all"):
    data = await _service(request).list_sentiment_feed(
        session, tenant_id=principal.tenant_id, sentiment=sentiment, market_country=market_country,
        query=q, dataset_id=dataset_id, origin=origin, offset=pagination.offset,
        limit=pagination.page_size, page=pagination.page)
    return SuccessEnvelope(data=SentimentFeedPage.model_validate(data), request_id=request.state.request_id)


@router.post("/sentiment-events", response_model=SuccessEnvelope[SentimentIngestResult], status_code=201,
             summary="接收授权舆情事件")
async def ingest_sentiment_events(body: SentimentIngestRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    result = await _service(request).ingest_sentiment_events(session, tenant_id=principal.tenant_id,
        events=[item.model_dump(mode="python") for item in body.events], source_id=body.source_id,
        authorization_reference=body.authorization_reference)
    await session.commit()
    return SuccessEnvelope(data=SentimentIngestResult.model_validate(result), request_id=request.state.request_id)


@router.get("/policy-sources", response_model=SuccessEnvelope[list[PolicySourceItem]], summary="政策源列表")
async def list_policy_sources(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    items = await _service(request).list_policy_sources(session, tenant_id=principal.tenant_id)
    return SuccessEnvelope(data=[PolicySourceItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.post("/policy-sources", response_model=SuccessEnvelope[PolicySourceItem], status_code=201,
             summary="创建官方政策源")
async def create_policy_source(body: PolicySourceCreateRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    item = await _service(request).create_policy_source(session, tenant_id=principal.tenant_id,
        user_id=principal.user_id, payload=body.model_dump(mode="python"))
    await session.commit()
    return SuccessEnvelope(data=PolicySourceItem.model_validate(item), request_id=request.state.request_id)


@router.patch("/policy-sources/{source_id}", response_model=SuccessEnvelope[PolicySourceItem],
              summary="启停政策源")
async def update_policy_source(source_id: int, body: PolicySourceUpdateRequest, request: Request,
    session: DatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    item = await _service(request).update_policy_source(session, tenant_id=principal.tenant_id,
        source_id=source_id, payload=body.model_dump(mode="python", exclude_unset=True))
    await session.commit()
    return SuccessEnvelope(data=PolicySourceItem.model_validate(item), request_id=request.state.request_id)


@router.delete("/policy-sources/{source_id}", response_model=SuccessEnvelope[dict], summary="删除政策源")
async def delete_policy_source(source_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    await _service(request).delete_policy_source(session, tenant_id=principal.tenant_id, source_id=source_id)
    await session.commit()
    return SuccessEnvelope(data={"source_id": source_id, "deleted": True}, request_id=request.state.request_id)


@router.post("/policy-sources/{source_id}/fetch", response_model=SuccessEnvelope[PolicyFetchResult],
             summary="手动拉取政策源")
async def fetch_policy_source(source_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    result = await _service(request).fetch_policy_source(session, tenant_id=principal.tenant_id, source_id=source_id)
    await session.commit()
    return SuccessEnvelope(data=PolicyFetchResult.model_validate(result), request_id=request.state.request_id)


@router.get("/policy-alerts", response_model=SuccessEnvelope[list[PolicyAlertItem]], summary="政策预警列表")
async def list_policy_alerts(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    severity: Annotated[str | None, Query(pattern="^(low|medium|high)$")] = None,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50):
    items = await _service(request).list_policy_alerts(session, tenant_id=principal.tenant_id,
        severity=severity, unread_only=unread_only, limit=limit)
    return SuccessEnvelope(data=[PolicyAlertItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.post("/policy-alerts/{alert_id}/read", response_model=SuccessEnvelope[dict], summary="政策预警已读")
async def read_policy_alert(alert_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    await _service(request).mark_policy_alert_read(session, tenant_id=principal.tenant_id, alert_id=alert_id)
    await session.commit()
    return SuccessEnvelope(data={"alert_id": alert_id, "is_read": True}, request_id=request.state.request_id)
