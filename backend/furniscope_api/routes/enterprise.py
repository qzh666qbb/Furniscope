"""Enterprise capability profile endpoints (settings page data source)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AwareDatetime
from furniscope_agent.product_facts import vocabulary

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession
from ..repositories.enterprise_repository import EnterpriseRepository
from ..schemas import SuccessEnvelope
from ..schemas.enterprise import (EnterpriseCapabilityProfileResponse, EnterpriseProfileRecord,
    EnterpriseProfileSaveRequest, CapabilityRecord)
from ..schemas.opportunity_policy import OpportunityFeedbackSave, OpportunityPolicySave, OpportunityOutcomeSave
from ..services.opportunity_policy import OpportunityPolicyService
from ..services.opportunity_outcomes import OpportunityOutcomeService

router = APIRouter(prefix="/api/v1/enterprise", tags=["Enterprise Profile"])


@router.get("/fact-vocabulary", operation_id="API-ENT-08", summary="产品事实与企业能力共用词表")
async def get_fact_vocabulary(request: Request,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    return SuccessEnvelope(data=vocabulary(), request_id=request.state.request_id)


async def _load(session: DatabaseSession, tenant_id: int) -> EnterpriseCapabilityProfileResponse:
    repository = EnterpriseRepository()
    profile = await repository.get_profile(session, tenant_id=tenant_id)
    capabilities = await repository.list_capabilities(session, tenant_id=tenant_id)
    return EnterpriseCapabilityProfileResponse(
        profile=EnterpriseProfileRecord(**profile) if profile else None,
        capabilities=[CapabilityRecord(**item) for item in capabilities],
    )


@router.get("/profile", response_model=SuccessEnvelope[EnterpriseCapabilityProfileResponse],
            operation_id="API-ENT-01", summary="查询企业制造能力画像")
async def get_profile(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await _load(session, principal.tenant_id)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/opportunity-policy", operation_id="API-ENT-03", summary="查询企业机会策略")
async def get_opportunity_policy(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await OpportunityPolicyService().get(session, principal.tenant_id)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.put("/opportunity-policy", operation_id="API-ENT-04", summary="保存企业机会策略新版本")
async def save_opportunity_policy(body: OpportunityPolicySave, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await OpportunityPolicyService().save(session, principal.tenant_id, principal.user_id, body)
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/opportunity-ranking/export", operation_id="API-ENT-09", summary="按任务分组导出排序评测候选和标签")
async def export_opportunity_ranking(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    as_of: AwareDatetime | None = None,
    after_task_id: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    data = await OpportunityOutcomeService(request.app.state.settings).export(
        session, principal.tenant_id, as_of, after_task_id, limit)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/opportunities/{opportunity_id}/outcomes", operation_id="API-ENT-10", summary="查询实施与经营观察历史")
async def get_opportunity_outcomes(opportunity_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await OpportunityOutcomeService(request.app.state.settings).history(
        session, principal.tenant_id, opportunity_id)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.put("/opportunities/{opportunity_id}/outcomes", operation_id="API-ENT-11", summary="新增不可变实施与经营观察记录")
async def save_opportunity_outcomes(opportunity_id: int, body: OpportunityOutcomeSave,
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await OpportunityOutcomeService(request.app.state.settings).save(
        session, principal.tenant_id, principal.user_id, opportunity_id, body)
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/opportunity-feedback/export", operation_id="API-ENT-05", summary="导出本企业反馈事件")
async def export_opportunity_feedback(request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)],
    after_id: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=1000)] = 500):
    data = await OpportunityPolicyService().export(session, principal.tenant_id, after_id, limit)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/opportunities/{opportunity_id}/feedback", operation_id="API-ENT-06", summary="查询机会处理历史")
async def get_opportunity_feedback(opportunity_id: int, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await OpportunityPolicyService().feedback(session, principal.tenant_id, opportunity_id)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.put("/opportunities/{opportunity_id}/feedback", operation_id="API-ENT-07", summary="记录机会处理结果")
async def save_opportunity_feedback(opportunity_id: int, body: OpportunityFeedbackSave,
    request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    data = await OpportunityPolicyService().save_feedback(
        session, principal.tenant_id, principal.user_id, opportunity_id, body)
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.put("/profile", response_model=SuccessEnvelope[EnterpriseCapabilityProfileResponse],
            operation_id="API-ENT-02", summary="保存企业制造能力画像")
async def save_profile(body: EnterpriseProfileSaveRequest, request: Request, session: DatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_user)]):
    try:
        await EnterpriseRepository().save_profile(
            session, tenant_id=principal.tenant_id, user_id=principal.user_id,
            profile=body.profile.model_dump(),
            capabilities=[item.model_dump() for item in body.capabilities],
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    data = await _load(session, principal.tenant_id)
    return SuccessEnvelope(data=data, request_id=request.state.request_id)
