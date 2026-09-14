"""Unified user-confirmation endpoints."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession, Pagination
from ..errors import BusinessError
from ..repositories.insight_repository import InsightRepository
from ..schemas import PageData, SuccessEnvelope
from ..schemas.analysis_tasks import (
    ConfirmationAnswerAccepted, ConfirmationAnswerRequest, ConfirmationListItem,
)
from ..services.analysis_agent_adapter import AnalysisAgentAdapter
from ..services.job_dispatch import enqueue_job

router = APIRouter(prefix="/api/v1/user-confirmations", tags=["User Confirmations"])


async def _resume(settings) -> None:
    await AnalysisAgentAdapter(settings).resume_next_confirmation()


@router.get("", response_model=SuccessEnvelope[PageData[ConfirmationListItem]],
            operation_id="API-CFM-01", summary="查询用户确认事项")
async def list_confirmations(request: Request, session: DatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    status: Annotated[Literal["pending", "responded", "expired", "cancelled"], Query()] = "pending"):
    rows, total = await InsightRepository().confirmations(
        session, tenant_id=principal.tenant_id, status=status,
        offset=pagination.offset, limit=pagination.page_size,
    )
    data = PageData[ConfirmationListItem].build(
        items=[ConfirmationListItem(**row) for row in rows], total=total, params=pagination,
    )
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/{confirmation_id}:respond", response_model=SuccessEnvelope[ConfirmationAnswerAccepted],
             status_code=202, operation_id="API-CFM-02", summary="回答确认事项并恢复任务")
async def respond_confirmation(confirmation_id: UUID, body: ConfirmationAnswerRequest,
    request: Request, background_tasks: BackgroundTasks,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    try:
        data = await AnalysisAgentAdapter(request.app.state.settings).accept_confirmation(
            confirmation_id=str(confirmation_id), selected_option=body.selected_option,
            user_input=body.user_input, user_id=principal.user_id,
        )
    except ValueError as exc:
        raise BusinessError("CONFIRMATION_INVALID", str(exc), status_code=409) from exc
    if request.app.state.job_queue is not None:
        await enqueue_job(request.app, "confirmation_resume", {},
                          job_id=f"confirmation:{confirmation_id}")
    else:
        background_tasks.add_task(_resume, request.app.state.settings)
    return SuccessEnvelope(data=ConfirmationAnswerAccepted(**data), request_id=request.state.request_id)
