"""API-PRD-06 parse job query."""

from typing import Annotated
from fastapi import APIRouter, Depends, Query, Request

from ..auth import AuthenticatedPrincipal, require_user
from ..dependencies import DatabaseSession
from ..errors import BusinessError
from ..repositories.parse_repository import ParseRepository
from ..schemas import SuccessEnvelope
from ..schemas.parse_jobs import ParseFileResult, ParseJobDetail, ParseSummary

router=APIRouter(prefix="/api/v1/product-parse-jobs",tags=["Products"])


@router.get("/{parse_job_id}",response_model=SuccessEnvelope[ParseJobDetail],operation_id="API-PRD-06",summary="查询产品解析任务")
async def get_parse_job(parse_job_id: str,request:Request,session:DatabaseSession,
    principal:Annotated[AuthenticatedPrincipal,Depends(require_user)],
    include_files:Annotated[bool,Query()]=True):
    repo=ParseRepository()
    job=await repo.get_job(session,tenant_id=principal.tenant_id,parse_job_id=parse_job_id)
    if job is None:
        raise BusinessError("PARSE_JOB_NOT_FOUND","解析任务不存在或不可访问",status_code=404)
    files=await repo.files(session,tenant_id=principal.tenant_id,job_id=job["id"]) if include_files else []
    data=ParseJobDetail(parse_job_id=job["parse_job_id"],product_id=job["product_id"],status=job["status"],
        progress_percent=job["progress_percent"],current_stage=job["current_stage"],
        summary=ParseSummary(file_count=job["file_count"],succeeded_file_count=job["succeeded_file_count"],
                             failed_file_count=job["failed_file_count"]),
        file_results=[ParseFileResult(**{k:f[k] for k in ("file_name","security_status","parse_status","error_code","error_message")}) for f in files],
        retryable=job["retryable"],failure_code=job["failure_code"],failure_message=job["failure_message"])
    return SuccessEnvelope(data=data,request_id=request.state.request_id)
