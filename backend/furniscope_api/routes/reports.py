"""MVP dashboard, report detail and evidence endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..errors import BusinessError
from ..repositories.analysis_task_repository import AnalysisTaskRepository
from ..repositories.report_repository import ReportRepository
from ..schemas import PageData, SuccessEnvelope
from ..schemas.analysis_tasks import AnalysisTaskArchiveRequest, AnalysisTaskArchiveResponse
from ..schemas.reports import (DashboardSummary, EvidenceItem, ReportArchiveRequest,
    ReportArchiveResponse, ReportDetail, ReportListItem)

router = APIRouter(prefix="/api/v1", tags=["Dashboard and Reports"])


@router.get("/dashboard/summary", response_model=SuccessEnvelope[DashboardSummary],
            operation_id="API-DSH-01", summary="查询工作台汇总")
async def dashboard_summary(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await ReportRepository().dashboard(session, tenant_id=principal.tenant_id)
    return SuccessEnvelope(data=DashboardSummary(**data), request_id=request.state.request_id)


@router.get("/reports", response_model=SuccessEnvelope[PageData[ReportListItem]],
            operation_id="API-RPT-01", summary="查询决策报告")
async def list_reports(request: Request, session: DatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    q: Annotated[str | None, Query(max_length=200)] = None,
    product_id: Annotated[int | None, Query(ge=1)] = None,
    target_country: Annotated[str | None, Query(min_length=2, max_length=2)] = None,
    created_since: Annotated[str | None, Query(max_length=40)] = None):
    rows, total = await ReportRepository().list_reports(
        session, tenant_id=principal.tenant_id, offset=pagination.offset,
        limit=pagination.page_size, keyword=q, product_id=product_id,
        target_country=target_country, created_since=created_since,
    )
    data = PageData[ReportListItem].build(
        items=[ReportListItem(**row) for row in rows], total=total, params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/reports/filter-options", response_model=SuccessEnvelope[dict],
            summary="查询决策报告全量筛选维度")
async def report_filter_options(
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
):
    data = await ReportRepository().filter_options(
        session, tenant_id=principal.tenant_id,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/reports:archive", response_model=SuccessEnvelope[ReportArchiveResponse],
             operation_id="API-RPT-04", summary="归档（删除）决策报告")
async def archive_reports(body: ReportArchiveRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    try:
        archived = await ReportRepository().archive_reports(
            session, tenant_id=principal.tenant_id,
            report_uuids=[str(item) for item in body.report_uuids],
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(data=ReportArchiveResponse(archived=archived),
                           request_id=request.state.request_id)


@router.post("/analysis-tasks:archive",
             response_model=SuccessEnvelope[AnalysisTaskArchiveResponse],
             summary="归档（删除）分析任务与对应决策报告")
async def archive_analysis_tasks(body: AnalysisTaskArchiveRequest, request: Request,
    session: DatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    try:
        archived = await AnalysisTaskRepository().archive_tasks(
            session, tenant_id=principal.tenant_id,
            task_uuids=[str(item) for item in body.task_uuids],
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(data=AnalysisTaskArchiveResponse(archived=archived),
                           request_id=request.state.request_id)


@router.get("/reports/{report_uuid}", response_model=SuccessEnvelope[ReportDetail],
            operation_id="API-RPT-02", summary="查询决策报告详情")
async def report_detail(report_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    row = await ReportRepository().detail(
        session, tenant_id=principal.tenant_id, report_uuid=str(report_uuid),
    )
    if row is None:
        raise BusinessError("REPORT_NOT_FOUND", "报告不存在或不可访问", status_code=404)
    return SuccessEnvelope(data=ReportDetail(**row), request_id=request.state.request_id)


@router.get("/reports/{report_uuid}/evidence",
            response_model=SuccessEnvelope[list[EvidenceItem]],
            operation_id="API-RPT-03", summary="查询报告证据链")
async def report_evidence(report_uuid: UUID, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    rows = await ReportRepository().evidence(
        session, tenant_id=principal.tenant_id, report_uuid=str(report_uuid),
    )
    if rows is None:
        raise BusinessError("REPORT_NOT_FOUND", "报告不存在或不可访问", status_code=404)
    return SuccessEnvelope(data=[EvidenceItem(**row) for row in rows],
                           request_id=request.state.request_id)
