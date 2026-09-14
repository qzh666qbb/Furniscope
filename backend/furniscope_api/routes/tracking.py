"""Competitor tracking board backed by authorized dataset snapshots."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..errors import BusinessError
from ..schemas import PageData, SuccessEnvelope
from ..schemas.tracking import (
    AlertItem,
    CatalogListingItem,
    PricePoint,
    SnapshotItem,
    TrackingOverview,
    WatchCreateRequest,
    WatchFromDatasetRequest,
    WatchFromProductRequest,
    WatchFromUrlRequest,
    WatchItem,
    WatchPatchRequest,
)
from ..services.authorized_signals import AuthorizedSignalService
from ..services.competitor_tracking import CompetitorTrackingService

router = APIRouter(prefix="/api/v1/competitor-tracking", tags=["Competitor Tracking"])
service = CompetitorTrackingService()


@router.get("/overview", response_model=SuccessEnvelope[TrackingOverview], summary="竞品动态追踪总览")
async def tracking_overview(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    product_id: Annotated[int | None, Query()] = None):
    data = await service.overview(session, tenant_id=principal.tenant_id, product_id=product_id)
    await session.commit()
    return SuccessEnvelope(data=TrackingOverview.model_validate(data), request_id=request.state.request_id)


@router.get("/catalog", response_model=SuccessEnvelope[PageData[CatalogListingItem]], summary="可选入竞品库的市场商品")
async def list_catalog(request: Request, session: DatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    dataset_id: Annotated[int | None, Query()] = None,
    market_country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    exclude_watched: Annotated[bool, Query()] = False):
    items, total = await service.list_catalog(
        session, tenant_id=principal.tenant_id, offset=pagination.offset, limit=pagination.page_size,
        query=q, dataset_id=dataset_id, market_country=market_country, exclude_watched=exclude_watched,
    )
    await session.commit()
    data = PageData[CatalogListingItem].build(
        items=[CatalogListingItem.model_validate(item) for item in items], total=total, params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/watches", response_model=SuccessEnvelope[list[WatchItem]], summary="监控目标列表")
async def list_watches(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    product_id: Annotated[int | None, Query()] = None):
    items = await service.list_watches(session, tenant_id=principal.tenant_id, product_id=product_id)
    return SuccessEnvelope(data=[WatchItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.post("/watches", response_model=SuccessEnvelope[WatchItem], summary="添加监控 ASIN")
async def create_watch(body: WatchCreateRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await service.add_watch(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id,
        asin=body.asin, market_country=body.market_country, platform=body.platform,
        dataset_id=body.dataset_id, product_id=body.product_id,
    )
    await session.commit()
    return SuccessEnvelope(data=WatchItem.model_validate(data), request_id=request.state.request_id)


@router.post("/watches/from-dataset", response_model=SuccessEnvelope[list[WatchItem]],
             summary="从数据集加入监控")
async def watches_from_dataset(body: WatchFromDatasetRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    items = await service.add_from_dataset(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id,
        dataset_id=body.dataset_id, product_id=body.product_id, limit=body.limit,
    )
    await session.commit()
    return SuccessEnvelope(data=[WatchItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.post("/watches/from-product", response_model=SuccessEnvelope[list[WatchItem]],
             summary="按本企业产品匹配相似竞品")
async def watches_from_product(body: WatchFromProductRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    items = await service.match_from_product(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id,
        product_id=body.product_id, dataset_id=body.dataset_id, limit=body.limit,
    )
    await session.commit()
    return SuccessEnvelope(data=[WatchItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.post("/watches/from-url", response_model=SuccessEnvelope[list[WatchItem]],
             summary="从商品页采集相似竞品")
async def watches_from_url(body: WatchFromUrlRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    collected = await AuthorizedSignalService(request.app.state.settings).collect_from_url(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id,
        payload={
            "endpoint_url": body.endpoint_url,
            "dataset_id": body.dataset_id,
            "market_country": body.market_country,
        },
    )
    items = []
    for asin in collected.get("listing_asins") or []:
        try:
            items.append(await service.add_watch(
                session, tenant_id=principal.tenant_id, user_id=principal.user_id,
                asin=asin, market_country=body.market_country, platform="amazon",
                dataset_id=body.dataset_id, product_id=body.product_id,
            ))
        except BusinessError as exc:
            if exc.code == "WATCH_OWN_PRODUCT":
                continue
            raise
    await session.commit()
    if collected.get("status") != "succeeded":
        raise BusinessError("COMPETITOR_URL_FETCH_FAILED",
                            collected.get("error_summary") or "未能从该网址解析到竞品", status_code=422)
    if not items:
        raise BusinessError("COMPETITOR_URL_EMPTY", "该页面没有解析到可对比的商品信息", status_code=422)
    return SuccessEnvelope(data=[WatchItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.delete("/watches/{watch_id}", response_model=SuccessEnvelope[dict], summary="停止监控")
async def delete_watch(watch_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    await service.delete_watch(session, tenant_id=principal.tenant_id, watch_id=watch_id)
    await session.commit()
    return SuccessEnvelope(data={"watch_id": watch_id, "deleted": True}, request_id=request.state.request_id)


@router.patch("/watches/{watch_id}", response_model=SuccessEnvelope[WatchItem], summary="更新竞品库条目")
async def patch_watch(watch_id: int, body: WatchPatchRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await service.patch_watch(
        session, tenant_id=principal.tenant_id, watch_id=watch_id,
        compare_selected=body.compare_selected, product_id=body.product_id,
    )
    await session.commit()
    return SuccessEnvelope(data=WatchItem.model_validate(data), request_id=request.state.request_id)


@router.post("/refresh", response_model=SuccessEnvelope[dict], summary="按已导入数据刷新快照")
async def refresh_tracking(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    result = await service.refresh(session, tenant_id=principal.tenant_id)
    await session.commit()
    return SuccessEnvelope(data=result, request_id=request.state.request_id)


@router.get("/watches/{watch_id}/prices", response_model=SuccessEnvelope[list[PricePoint]])
async def watch_prices(watch_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    items = await service.price_series(session, tenant_id=principal.tenant_id, watch_id=watch_id)
    return SuccessEnvelope(data=[PricePoint.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.get("/watches/{watch_id}/snapshots", response_model=SuccessEnvelope[list[SnapshotItem]])
async def watch_snapshots(watch_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    items = await service.list_snapshots(session, tenant_id=principal.tenant_id, watch_id=watch_id)
    return SuccessEnvelope(data=[SnapshotItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.get("/alerts", response_model=SuccessEnvelope[list[AlertItem]])
async def list_alerts(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    unread_only: Annotated[bool, Query()] = False):
    items = await service.list_alerts(session, tenant_id=principal.tenant_id, unread_only=unread_only)
    return SuccessEnvelope(data=[AlertItem.model_validate(item) for item in items],
                           request_id=request.state.request_id)


@router.post("/alerts/{alert_id}/read", response_model=SuccessEnvelope[dict])
async def read_alert(alert_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    await service.mark_alert_read(session, tenant_id=principal.tenant_id, alert_id=alert_id)
    await session.commit()
    return SuccessEnvelope(data={"alert_id": alert_id, "is_read": True}, request_id=request.state.request_id)
