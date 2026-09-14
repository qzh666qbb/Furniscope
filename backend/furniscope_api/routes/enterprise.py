"""Enterprise capability profile endpoints (settings page data source)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession
from ..repositories.enterprise_repository import EnterpriseRepository
from ..schemas import SuccessEnvelope
from ..schemas.enterprise import (EnterpriseCapabilityProfileResponse, EnterpriseProfileRecord,
    EnterpriseProfileSaveRequest, CapabilityRecord)

router = APIRouter(prefix="/api/v1/enterprise", tags=["Enterprise Profile"])


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
