"""Unified API for the competition's five market-insight capabilities."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..schemas import PageData, SuccessEnvelope
from ..schemas.market_intelligence import (
    CompetitorAlertDetail,
    CompetitorAlertSummary,
    MarketIntelligenceOverview,
    MarketOpportunity,
    PricingSimulationRequest,
    ReviewClusterDetail,
    ReviewClusterSummary,
)
from ..services.market_intelligence import MarketIntelligenceService

router = APIRouter(prefix="/api/v1/market-intelligence", tags=["Market Intelligence"])


@router.get(
    "/overview",
    response_model=SuccessEnvelope[MarketIntelligenceOverview],
    operation_id="API-MKT-01",
    summary="查询五项市场智能能力总览",
)
async def overview(
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    dataset_id: Annotated[int | None, Query(ge=1)] = None,
    product_id: Annotated[int | None, Query(ge=1)] = None,
):
    data = await MarketIntelligenceService().overview(
        session,
        tenant_id=principal.tenant_id,
        dataset_id=dataset_id,
        product_id=product_id,
    )
    return SuccessEnvelope(
        data=MarketIntelligenceOverview.model_validate(data),
        request_id=request.state.request_id,
    )


@router.get(
    "/opportunities",
    response_model=SuccessEnvelope[PageData[MarketOpportunity]],
    operation_id="API-MKT-03",
    summary="分页查询当前数据集的智能选品机会",
)
async def list_opportunities(
    request: Request,
    session: DatabaseSession,
    pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    dataset_id: Annotated[int, Query(ge=1)],
    product_id: Annotated[int | None, Query(ge=1)] = None,
):
    data = await MarketIntelligenceService().list_opportunities(
        session,
        tenant_id=principal.tenant_id,
        dataset_id=dataset_id,
        product_id=product_id,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return SuccessEnvelope(
        data=PageData[MarketOpportunity].model_validate(data),
        request_id=request.state.request_id,
    )


@router.get(
    "/opportunities/{opportunity_id}",
    response_model=SuccessEnvelope[MarketOpportunity],
    operation_id="API-MKT-04",
    summary="查询智能选品机会详情",
)
async def opportunity_detail(
    opportunity_id: int,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    data = await MarketIntelligenceService().get_opportunity(
        session,
        tenant_id=principal.tenant_id,
        opportunity_id=opportunity_id,
    )
    return SuccessEnvelope(
        data=MarketOpportunity.model_validate(data),
        request_id=request.state.request_id,
    )


@router.get(
    "/competitor-alerts",
    response_model=SuccessEnvelope[PageData[CompetitorAlertSummary]],
    operation_id="API-MKT-05",
    summary="分页查询当前数据集的竞品动态",
)
async def list_competitor_alerts(
    request: Request,
    session: DatabaseSession,
    pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    dataset_id: Annotated[int, Query(ge=1)],
):
    data = await MarketIntelligenceService().list_competitor_alerts(
        session,
        tenant_id=principal.tenant_id,
        dataset_id=dataset_id,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return SuccessEnvelope(
        data=PageData[CompetitorAlertSummary].model_validate(data),
        request_id=request.state.request_id,
    )


@router.get(
    "/competitor-alerts/{alert_id}",
    response_model=SuccessEnvelope[CompetitorAlertDetail],
    operation_id="API-MKT-06",
    summary="查询竞品动态详情",
)
async def competitor_alert_detail(
    alert_id: int,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    data = await MarketIntelligenceService().get_competitor_alert(
        session,
        tenant_id=principal.tenant_id,
        alert_id=alert_id,
    )
    return SuccessEnvelope(
        data=CompetitorAlertDetail.model_validate(data),
        request_id=request.state.request_id,
    )


@router.get(
    "/review-clusters",
    response_model=SuccessEnvelope[PageData[ReviewClusterSummary]],
    operation_id="API-MKT-07",
    summary="分页查询当前数据集的评论需求主题",
)
async def list_review_clusters(
    request: Request,
    session: DatabaseSession,
    pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    dataset_id: Annotated[int, Query(ge=1)],
    product_id: Annotated[int | None, Query(ge=1)] = None,
):
    data = await MarketIntelligenceService().list_review_clusters(
        session,
        tenant_id=principal.tenant_id,
        dataset_id=dataset_id,
        product_id=product_id,
        page=pagination.page,
        page_size=pagination.page_size,
    )
    return SuccessEnvelope(
        data=PageData[ReviewClusterSummary].model_validate(data),
        request_id=request.state.request_id,
    )


@router.get(
    "/review-clusters/{cluster_id}",
    response_model=SuccessEnvelope[ReviewClusterDetail],
    operation_id="API-MKT-08",
    summary="查询评论需求主题及原文证据详情",
)
async def review_cluster_detail(
    cluster_id: int,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    data = await MarketIntelligenceService().get_review_cluster(
        session,
        tenant_id=principal.tenant_id,
        cluster_id=cluster_id,
    )
    return SuccessEnvelope(
        data=ReviewClusterDetail.model_validate(data),
        request_id=request.state.request_id,
    )


@router.post("/pricing", response_model=SuccessEnvelope[dict],
             operation_id="API-MKT-02", summary="模拟目标毛利约束下的建议价格")
async def simulate_pricing(
    body: PricingSimulationRequest,
    request: Request,
    session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    data = await MarketIntelligenceService().simulate_pricing(
        session,
        tenant_id=principal.tenant_id,
        dataset_id=body.dataset_id,
        unit_cost=body.unit_cost,
        target_margin=body.target_margin,
        promo_margin_floor=body.promo_margin_floor,
        max_discount_rate=body.max_discount_rate,
        comparator_group=body.comparator_group,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)
