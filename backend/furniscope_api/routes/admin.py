"""Tenant-admin management, configuration, and safe diagnostics APIs."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..auth import AuthenticatedPrincipal, require_admin, require_permission
from ..dependencies import AdminDatabaseSession, Pagination
from ..errors import BusinessError
from ..schemas import SuccessEnvelope
from ..schemas.admin import (AdminUserUpdate, ModelRouteUpdate, PromptTemplateCreate,
                             EnterprisePasswordReset, EnterpriseUserCreate, EnterpriseUserUpdate,
                             RegistrationApproval, RegistrationRejection,
                             TenantDataSourceCreate, TenantLegalHoldCreate,
                             TenantLegalHoldRelease, TenantMemberRolesUpdate, TenantSkuUpsert,
                             WorkflowRecoveryRequest)
from ..services.password_reset_service import PasswordResetService
from ..security.password import PasswordService
from ..services.enterprise_onboarding import provision_enterprise_account
from ..services.forecast_training_service import ForecastTrainingService
from ..services.forecast_publication import ForecastPublication
from ..services.forecast_model_replacement_service import ForecastModelReplacementService
from ..services.registration_service import RegistrationService
from ..services.job_dispatch import enqueue_job
from ..services.resource_version import require_version, resource_version
from ..services.idempotency_service import IdempotencyService
from .internal_model import _authorize

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


def _mapping(row: Any) -> dict[str, Any]:
    return dict(row) if row is not None else {}


async def _audit(session: AdminDatabaseSession, principal: AuthenticatedPrincipal, *, action: str,
                 resource: str, resource_id: int | None, before: dict[str, Any] | None,
                 after: dict[str, Any] | None, request_id: str,
                 tenant_id: int | None = None) -> None:
    await session.execute(text("""INSERT INTO furniscope.audit_logs
        (tenant_id,actor_user_id,action_code,resource_type,resource_id,request_id,before_snapshot,after_snapshot)
        VALUES(:tenant,:actor,:action,:resource,:resource_id,:request_id,
               CAST(:before AS jsonb),CAST(:after AS jsonb))"""), {
        "tenant": tenant_id or principal.tenant_id, "actor": principal.user_id, "action": action,
        "resource": resource, "resource_id": resource_id, "request_id": request_id,
        "before": json.dumps(before, default=str) if before is not None else None,
        "after": json.dumps(after, default=str) if after is not None else None,
    })


def _page(items: list[dict[str, Any]], total: int, pagination: Pagination) -> dict[str, Any]:
    return {"items": items, "total": total, "page": pagination.page,
            "page_size": pagination.page_size,
            "has_next": pagination.page * pagination.page_size < total}


def _enterprise_projection(row: Any) -> dict[str, Any]:
    data = _mapping(row)
    updated_at = data.pop("tenant_updated_at", None)
    data["resource_version"] = resource_version(updated_at)
    return data


@router.get("/enterprise-users", operation_id="API-ADM-14")
async def list_enterprise_users(
    request: Request, session: AdminDatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    keyword: str | None = Query(default=None, max_length=200),
    status: str | None = None,
):
    if status and status not in {"trial", "active", "suspended", "closed"}:
        raise BusinessError("TENANT_FILTER_INVALID", "企业状态筛选无效", status_code=400)
    params = {"keyword": f"%{keyword}%" if keyword else None, "status": status,
              "offset": pagination.offset, "limit": pagination.page_size}
    where = """t.data_class='business' AND u.role_code='user'
        AND ((CAST(:status AS varchar) IS NULL AND t.status<>'closed') OR t.status=:status)
        AND (CAST(:keyword AS varchar) IS NULL OR t.name ILIKE :keyword
             OR t.tenant_code ILIKE :keyword OR u.email ILIKE :keyword
             OR u.name ILIKE :keyword)"""
    joins = """
        LEFT JOIN LATERAL (
          SELECT m.version,m.training_data_through,m.metrics,d.deployed_at
            FROM forecast_model_deployments d JOIN forecast_models m ON m.id=d.model_id
           WHERE d.tenant_id=t.id AND d.scenario_code='sales_forecast' AND d.status='active'
           ORDER BY d.deployed_at DESC LIMIT 1
        ) model ON true
        LEFT JOIN LATERAL (
          SELECT count(*)::int AS sku_count FROM tenant_sku_catalog c
           WHERE c.tenant_id=t.id AND c.lifecycle_status='active'
        ) catalog ON true"""
    total = int((await session.execute(text(
        f"SELECT count(*) FROM users u JOIN tenants t ON t.id=u.tenant_id {joins} WHERE {where}"
    ), params)).scalar_one())
    summary = _mapping((await session.execute(text("""
        SELECT count(*) FILTER(WHERE t.status<>'closed')::int total_accounts,
               count(*) FILTER(
                 WHERE t.status='active' AND u.status='active'
               )::int active_accounts,
               count(*) FILTER(
                 WHERE t.status<>'closed'
                   AND 'sales_forecast'=ANY(
                     SELECT jsonb_array_elements_text(t.entitlements)
                   )
               )::int forecast_enabled_accounts,
               count(DISTINCT t.id) FILTER(
                 WHERE t.status IN ('suspended','closed')
               )::int suspended_tenants
          FROM users u
          JOIN tenants t ON t.id=u.tenant_id
         WHERE t.data_class='business' AND u.role_code='user'
    """))).mappings().one())
    rows = (await session.execute(text(f"""SELECT t.id AS tenant_id,t.tenant_code,
        t.name AS enterprise_name,t.status AS tenant_status,t.entitlements,t.created_at,
        t.updated_at AS tenant_updated_at,u.id AS user_id,
        u.name AS contact_name,u.email,u.status AS user_status,
        u.last_login_at,u.created_at AS user_created_at,
        model.version AS model_version,
        model.training_data_through,model.metrics AS model_metrics,model.deployed_at,
        COALESCE(catalog.sku_count,0) AS sku_count
        FROM users u JOIN tenants t ON t.id=u.tenant_id {joins} WHERE {where}
        ORDER BY u.created_at,u.id OFFSET :offset LIMIT :limit"""), params)).mappings().all()
    data = _page([_enterprise_projection(row) for row in rows], total, pagination)
    data["summary"] = summary
    return SuccessEnvelope(data=data,
                           request_id=request.state.request_id)


@router.get("/registration-applications", operation_id="API-ADM-19")
async def list_registration_applications(
    request: Request, session: AdminDatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    status: str | None = None,
):
    if status and status not in {"pending", "approved", "rejected"}:
        raise BusinessError("REGISTRATION_FILTER_INVALID", "注册申请状态筛选无效", status_code=400)
    items, total = await RegistrationService().list(
        session, status=status, offset=pagination.offset, limit=pagination.page_size,
    )
    return SuccessEnvelope(data=_page(items, total, pagination), request_id=request.state.request_id)


@router.post("/registration-applications/{application_id}:approve", operation_id="API-ADM-20")
async def approve_registration_application(
    application_id: int, body: RegistrationApproval, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    idem = IdempotencyService()
    decision = await idem.begin(
        session, tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
        route_code="API-ADM-20", http_method="POST", idempotency_key=idempotency_key,
        request_payload={"application_id": application_id, **body.model_dump(mode="json")},
    )
    if decision.action == "replay":
        await session.commit()
        return JSONResponse(status_code=int(decision.response_status), content=decision.response_body)
    try:
        provisioned = await RegistrationService().approve(
            session, application_id=application_id, tenant_code=body.tenant_code,
            entitlements=list(body.entitlements), actor_user_id=principal.user_id,
        )
        data = {**provisioned, "resource_version": resource_version(provisioned.pop("updated_at"))}
        await _audit(session, principal, action="admin.registration.approve", resource="tenant",
                     resource_id=data["tenant_id"], before=None,
                     after={key: value for key, value in data.items() if key != "resource_version"},
                     request_id=request.state.request_id, tenant_id=data["tenant_id"])
        content = SuccessEnvelope(data=data, request_id=request.state.request_id).model_dump(mode="json")
        await idem.finish(session, tenant_id=principal.tenant_id, record_id=decision.record_id,
                          response_status=200, response_body=content,
                          resource_type="tenant", resource_public_id=str(data["tenant_id"]))
        await session.commit()
        return JSONResponse(content=content)
    except IntegrityError as exc:
        await session.rollback()
        raise BusinessError("ENTERPRISE_USER_CONFLICT", "租户编码或登录邮箱已存在", status_code=409) from exc


@router.post("/registration-applications/{application_id}:reject", operation_id="API-ADM-21")
async def reject_registration_application(
    application_id: int, body: RegistrationRejection, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
):
    data = await RegistrationService().reject(
        session, application_id=application_id, reason=body.reason.strip(),
        actor_user_id=principal.user_id,
    )
    await _audit(session, principal, action="admin.registration.reject", resource="registration_application",
                 resource_id=application_id, before=None,
                 after={"status": "rejected", "reason": body.reason.strip(), "email": data["email"]},
                 request_id=request.state.request_id)
    await session.commit()
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/enterprise-users", status_code=201, operation_id="API-ADM-15")
async def create_enterprise_user(
    body: EnterpriseUserCreate, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    email = body.email.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise BusinessError("REQUEST_VALIDATION_FAILED", "企业用户邮箱格式无效", status_code=422)
    idem = IdempotencyService()
    decision = await idem.begin(
        session, tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
        route_code="API-ADM-15", http_method="POST", idempotency_key=idempotency_key,
        request_payload={**body.model_dump(mode="json", exclude={"initial_password"}),
                         "password_sha256": hashlib.sha256(body.initial_password.encode()).hexdigest()},
    )
    if decision.action == "replay":
        await session.commit()
        return JSONResponse(status_code=int(decision.response_status), content=decision.response_body)
    try:
        provisioned = await provision_enterprise_account(
            session,
            tenant_code=body.tenant_code,
            enterprise_name=body.enterprise_name,
            email=email,
            contact_name=body.contact_name,
            password_hash=PasswordService().hash(body.initial_password),
            entitlements=list(body.entitlements),
        )
        data = {**provisioned, "resource_version": resource_version(provisioned.pop("updated_at"))}
        await _audit(session, principal, action="admin.enterprise.create", resource="tenant",
                     resource_id=data["tenant_id"], before=None,
                     after={key: value for key, value in data.items() if key != "resource_version"},
                     request_id=request.state.request_id, tenant_id=data["tenant_id"])
        content = SuccessEnvelope(data=data, request_id=request.state.request_id).model_dump(mode="json")
        await idem.finish(session, tenant_id=principal.tenant_id, record_id=decision.record_id,
                          response_status=201, response_body=content,
                          resource_type="tenant", resource_public_id=str(data["tenant_id"]))
        await session.commit()
        return JSONResponse(status_code=201, content=content)
    except IntegrityError as exc:
        await session.rollback()
        raise BusinessError("ENTERPRISE_USER_CONFLICT", "租户编码或登录邮箱已存在", status_code=409) from exc


@router.patch("/enterprise-users/{tenant_id}", operation_id="API-ADM-16")
async def update_enterprise_user(
    tenant_id: int, body: EnterpriseUserUpdate, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    if_match: Annotated[str, Header(alias="If-Match")],
    user_id: int | None = Query(default=None, gt=0),
):
    tenant = (await session.execute(text(
        "SELECT * FROM tenants WHERE id=:tenant FOR UPDATE"), {"tenant": tenant_id})).mappings().one_or_none()
    if tenant is None:
        raise BusinessError("TENANT_NOT_FOUND", "企业用户不存在", status_code=404)
    require_version(if_match, tenant["updated_at"])
    owner = (await session.execute(text("""SELECT * FROM users WHERE tenant_id=:tenant
        AND role_code='user' AND (CAST(:user_id AS bigint) IS NULL OR id=:user_id)
        ORDER BY created_at,id LIMIT 1 FOR UPDATE"""),
        {"tenant": tenant_id, "user_id": user_id})).mappings().one_or_none()
    values = body.model_dump(exclude_unset=True)
    if not values:
        raise BusinessError("REQUEST_VALIDATION_FAILED", "至少提供一个修改字段", status_code=422)
    before = {"enterprise_name": tenant["name"], "tenant_status": tenant["status"],
              "entitlements": tenant["entitlements"],
              "user_status": owner["status"] if owner else None}
    name = values.get("enterprise_name", tenant["name"])
    tenant_status = values.get("tenant_status", tenant["status"])
    if tenant["status"] == "closed" and tenant_status in {"trial", "active"}:
        raise BusinessError(
            "TENANT_RESTORE_ENDPOINT_REQUIRED",
            "已关闭企业必须使用租户恢复操作，以恢复关闭前的全部账号状态",
            status_code=409,
        )
    entitlements = values.get("entitlements", tenant["entitlements"])
    updated = (await session.execute(text("""UPDATE tenants SET name=:name,status=:status,
        entitlements=CAST(:entitlements AS jsonb),updated_at=now() WHERE id=:tenant
        RETURNING id,tenant_code,name,status,entitlements,updated_at"""), {
        "tenant": tenant_id, "name": name, "status": tenant_status,
        "entitlements": json.dumps(entitlements),
    })).mappings().one()
    if "user_status" in values:
        if owner is None:
            raise BusinessError("USER_NOT_FOUND", "企业登录账号不存在", status_code=404)
        await session.execute(text("UPDATE users SET status=:status,updated_at=now() WHERE id=:id"),
                              {"status": values["user_status"], "id": owner["id"]})
    after = {"enterprise_name": updated["name"], "tenant_status": updated["status"],
             "entitlements": updated["entitlements"],
             "user_status": values.get("user_status", before["user_status"])}
    await _audit(session, principal, action="admin.enterprise.update", resource="tenant",
                 resource_id=tenant_id, before=before, after=after,
                 request_id=request.state.request_id, tenant_id=tenant_id)
    await session.commit()
    return SuccessEnvelope(data={"tenant_id": updated["id"], "tenant_code": updated["tenant_code"],
        **after, "resource_version": resource_version(updated["updated_at"])},
        request_id=request.state.request_id)


@router.post("/enterprise-users/{tenant_id}:restore", operation_id="API-ADM-35")
async def restore_enterprise_tenant(
    tenant_id: int, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    if_match: Annotated[str, Header(alias="If-Match")],
):
    tenant = (await session.execute(text("""
        SELECT * FROM tenants WHERE id=:tenant AND data_class='business' FOR UPDATE
    """), {"tenant": tenant_id})).mappings().one_or_none()
    if tenant is None:
        raise BusinessError("TENANT_NOT_FOUND", "企业用户不存在", status_code=404)
    require_version(if_match, tenant["updated_at"])
    if tenant["status"] != "closed":
        raise BusinessError("TENANT_NOT_CLOSED", "企业当前不是已关闭状态", status_code=409)
    deletion = (await session.execute(text("""
        SELECT id,request_uuid::text,inventory_snapshot
          FROM tenant_deletion_requests
         WHERE tenant_id=:tenant AND status IN ('scheduled','blocked')
         ORDER BY requested_at DESC LIMIT 1 FOR UPDATE
    """), {"tenant": tenant_id})).mappings().one_or_none()
    if deletion is None:
        raise BusinessError(
            "TENANT_RESTORE_UNAVAILABLE",
            "删除任务已开始或恢复期限已结束，不能在线恢复",
            status_code=409,
        )
    statuses = (deletion["inventory_snapshot"] or {}).get("account_statuses") or []
    if not statuses:
        statuses = await session.scalar(text("""
            SELECT before_snapshot->'accounts'
              FROM audit_logs
             WHERE tenant_id=:tenant AND action_code='admin.enterprise.delete'
             ORDER BY occurred_at DESC,id DESC LIMIT 1
        """), {"tenant": tenant_id}) or []
    if not statuses:
        raise BusinessError(
            "TENANT_RESTORE_SNAPSHOT_MISSING",
            "关闭前账号快照缺失，已阻止不完整恢复",
            status_code=409,
        )
    allowed_statuses = {"invited", "active", "disabled", "locked"}
    restored = 0
    for item in statuses:
        status = str(item.get("status") or "")
        if status not in allowed_statuses:
            continue
        result = await session.execute(text("""
            UPDATE users SET status=:status,updated_at=now()
             WHERE id=:user_id AND tenant_id=:tenant AND role_code='user'
        """), {
            "status": status, "user_id": int(item["user_id"]), "tenant": tenant_id,
        })
        restored += int(result.rowcount or 0)
    updated = (await session.execute(text("""
        UPDATE tenants SET status='active',updated_at=now()
         WHERE id=:tenant
        RETURNING id,tenant_code,name,status,updated_at
    """), {"tenant": tenant_id})).mappings().one()
    await session.execute(text("""
        UPDATE tenant_deletion_requests
           SET status='cancelled',cancelled_at=now(),cancelled_by=:actor,
               cancellation_reason='tenant_restored_before_retention_deadline',
               updated_at=now()
         WHERE id=:id
    """), {
        "id": deletion["id"], "actor": principal.user_id,
    })
    await _audit(
        session, principal, action="admin.enterprise.restore", resource="tenant",
        resource_id=tenant_id,
        before={"tenant_status": "closed", "account_statuses": statuses},
        after={"tenant_status": "active", "restored_account_count": restored},
        request_id=request.state.request_id, tenant_id=tenant_id,
    )
    await session.commit()
    return SuccessEnvelope(data={
        "tenant_id": updated["id"],
        "tenant_code": updated["tenant_code"],
        "enterprise_name": updated["name"],
        "tenant_status": updated["status"],
        "restored_account_count": restored,
        "resource_version": resource_version(updated["updated_at"]),
    }, request_id=request.state.request_id)


@router.post("/enterprise-users/{tenant_id}:reset-password", operation_id="API-ADM-26")
async def reset_enterprise_password(
    tenant_id: int, body: EnterprisePasswordReset, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    if_match: Annotated[str, Header(alias="If-Match")],
    user_id: int | None = Query(default=None, gt=0),
):
    tenant = (await session.execute(text(
        "SELECT * FROM tenants WHERE id=:tenant FOR UPDATE"), {"tenant": tenant_id})).mappings().one_or_none()
    if tenant is None:
        raise BusinessError("TENANT_NOT_FOUND", "企业用户不存在", status_code=404)
    require_version(if_match, tenant["updated_at"])
    owner = (await session.execute(text("""SELECT * FROM users WHERE tenant_id=:tenant
        AND role_code='user' AND (CAST(:user_id AS bigint) IS NULL OR id=:user_id)
        ORDER BY created_at,id LIMIT 1 FOR UPDATE"""),
        {"tenant": tenant_id, "user_id": user_id})).mappings().one_or_none()
    if owner is None:
        raise BusinessError("USER_NOT_FOUND", "企业登录账号不存在", status_code=404)
    await PasswordResetService().admin_set_password(
        session, user_id=int(owner["id"]), new_password=body.new_password
    )
    await _audit(session, principal, action="admin.enterprise.reset_password", resource="user",
                 resource_id=int(owner["id"]), before={"email": owner["email"]},
                 after={"email": owner["email"], "sessions_revoked": True},
                 request_id=request.state.request_id, tenant_id=tenant_id)
    await session.commit()
    return SuccessEnvelope(
        data={"tenant_id": tenant_id, "user_id": int(owner["id"]), "email": owner["email"], "reset": True},
        request_id=request.state.request_id,
    )


@router.delete("/enterprise-users/{tenant_id}", operation_id="API-ADM-22")
async def delete_enterprise_user(
    tenant_id: int, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    if_match: Annotated[str, Header(alias="If-Match")],
):
    if tenant_id == principal.tenant_id:
        raise BusinessError("ADMIN_TENANT_DELETE_FORBIDDEN",
                            "不能删除当前管理员所在租户", status_code=409)
    tenant = (await session.execute(text(
        "SELECT * FROM tenants WHERE id=:tenant FOR UPDATE"),
        {"tenant": tenant_id})).mappings().one_or_none()
    if tenant is None:
        raise BusinessError("TENANT_NOT_FOUND", "企业用户不存在", status_code=404)
    require_version(if_match, tenant["updated_at"])
    users = (await session.execute(text("""SELECT id,email,status FROM users
        WHERE tenant_id=:tenant AND role_code='user' ORDER BY id FOR UPDATE"""),
        {"tenant": tenant_id})).mappings().all()
    existing_request = (await session.execute(text("""
        SELECT request_uuid::text,status,scheduled_for
          FROM tenant_deletion_requests
         WHERE tenant_id=:tenant AND status IN ('scheduled','running','blocked')
         ORDER BY requested_at DESC LIMIT 1
    """), {"tenant": tenant_id})).mappings().one_or_none()
    if existing_request is not None:
        raise BusinessError(
            "TENANT_DELETION_ALREADY_SCHEDULED",
            "企业删除任务已存在",
            status_code=409,
        )
    before = {
        "enterprise_name": tenant["name"],
        "tenant_status": tenant["status"],
        "accounts": [{"id": user["id"], "status": user["status"]} for user in users],
    }
    updated = (await session.execute(text("""UPDATE tenants
        SET status='closed',updated_at=now() WHERE id=:tenant
        RETURNING id,tenant_code,name,status,updated_at"""),
        {"tenant": tenant_id})).mappings().one()
    await session.execute(text("""UPDATE users SET status='disabled',updated_at=now()
        WHERE tenant_id=:tenant AND role_code='user'"""), {"tenant": tenant_id})
    await session.execute(text("""UPDATE auth_sessions
        SET revoked_at=now(),revoke_reason='admin_enterprise_deleted'
        WHERE tenant_id=:tenant AND revoked_at IS NULL"""), {"tenant": tenant_id})
    has_hold = bool((await session.execute(text("""
        SELECT EXISTS(
          SELECT FROM tenant_legal_holds
           WHERE tenant_id=:tenant AND released_at IS NULL
        )
    """), {"tenant": tenant_id})).scalar_one())
    inventory = (await session.execute(text("""
        SELECT jsonb_build_object(
          'users',(SELECT count(*) FROM users WHERE tenant_id=:tenant),
          'products',(SELECT count(*) FROM products WHERE tenant_id=:tenant),
          'market_datasets',(SELECT count(*) FROM market_datasets WHERE tenant_id=:tenant),
          'analysis_tasks',(SELECT count(*) FROM analysis_tasks WHERE tenant_id=:tenant),
          'knowledge_documents',(SELECT count(*) FROM knowledge_documents WHERE tenant_id=:tenant)
        )
    """), {"tenant": tenant_id})).scalar_one()
    inventory = {
        **dict(inventory),
        "account_statuses": [
            {"user_id": int(user["id"]), "status": user["status"]}
            for user in users
        ],
    }
    deletion = (await session.execute(text("""
        INSERT INTO tenant_deletion_requests(
          tenant_id,tenant_code_snapshot,tenant_name_snapshot,status,reason,
          requested_by,scheduled_for,inventory_snapshot
        )
        VALUES(
          :tenant,:code,:name,:status,'platform_admin_requested',:actor,
          now()+make_interval(days=>:retention_days),CAST(:inventory AS jsonb)
        )
        RETURNING request_uuid::text,status,requested_at,scheduled_for
    """), {
        "tenant": tenant_id,
        "code": tenant["tenant_code"],
        "name": tenant["name"],
        "status": "blocked" if has_hold else "scheduled",
        "actor": principal.user_id,
        "retention_days": int(tenant["data_retention_days"]),
        "inventory": json.dumps(inventory),
    })).mappings().one()
    after = {"enterprise_name": updated["name"], "tenant_status": updated["status"],
             "disabled_account_count": len(users),
             "deletion_request_uuid": deletion["request_uuid"],
             "deletion_status": deletion["status"],
             "scheduled_for": deletion["scheduled_for"]}
    await _audit(session, principal, action="admin.enterprise.delete", resource="tenant",
                 resource_id=tenant_id, before=before, after=after,
                 request_id=request.state.request_id, tenant_id=tenant_id)
    await session.commit()
    return SuccessEnvelope(data={"tenant_id": updated["id"],
        "tenant_code": updated["tenant_code"], "enterprise_name": updated["name"],
        "tenant_status": updated["status"], "deleted": True,
        "disabled_account_count": len(users),
        "deletion_request_uuid": deletion["request_uuid"],
        "deletion_status": deletion["status"],
        "scheduled_for": deletion["scheduled_for"],
        "resource_version": resource_version(updated["updated_at"])},
        request_id=request.state.request_id)


@router.get("/tenant-deletions", operation_id="API-ADM-32")
async def list_tenant_deletions(
    request: Request,
    session: AdminDatabaseSession,
    pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    tenant_id: int | None = Query(default=None, gt=0),
    status: str | None = Query(default=None),
):
    if status and status not in {
        "scheduled", "running", "completed", "cancelled", "blocked", "failed"
    }:
        raise BusinessError("TENANT_DELETION_FILTER_INVALID", "删除任务状态无效", status_code=400)
    params = {
        "tenant": tenant_id,
        "status": status,
        "offset": pagination.offset,
        "limit": pagination.page_size,
    }
    where = """(CAST(:tenant AS bigint) IS NULL OR d.tenant_id=:tenant)
        AND (CAST(:status AS varchar) IS NULL OR d.status=:status)"""
    total = int((await session.execute(
        text(f"SELECT count(*) FROM tenant_deletion_requests d WHERE {where}"),
        params,
    )).scalar_one())
    rows = (await session.execute(text(f"""
        SELECT d.request_uuid::text,d.tenant_id,d.tenant_code_snapshot,
               d.tenant_name_snapshot,d.status,d.reason,d.requested_by,
               d.requested_at,d.scheduled_for,d.started_at,d.completed_at,
               d.cancelled_at,d.failure_code,d.failure_detail,d.inventory_snapshot,
               c.certificate_uuid::text,c.evidence_hash
          FROM tenant_deletion_requests d
          LEFT JOIN tenant_deletion_certificates c ON c.request_uuid=d.request_uuid
         WHERE {where}
         ORDER BY d.requested_at DESC
         OFFSET :offset LIMIT :limit
    """), params)).mappings().all()
    return SuccessEnvelope(
        data=_page([_mapping(row) for row in rows], total, pagination),
        request_id=request.state.request_id,
    )


@router.post("/enterprise-users/{tenant_id}/legal-holds", operation_id="API-ADM-33")
async def create_tenant_legal_hold(
    tenant_id: int,
    body: TenantLegalHoldCreate,
    request: Request,
    session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
):
    if not await session.scalar(text("SELECT EXISTS(SELECT FROM tenants WHERE id=:tenant)"),
                                {"tenant": tenant_id}):
        raise BusinessError("TENANT_NOT_FOUND", "企业用户不存在", status_code=404)
    try:
        row = (await session.execute(text("""
            INSERT INTO tenant_legal_holds(
              tenant_id,reason,reference_code,placed_by
            ) VALUES(:tenant,:reason,:reference,:actor)
            RETURNING hold_uuid::text,tenant_id,reason,reference_code,placed_at
        """), {
            "tenant": tenant_id,
            "reason": body.reason,
            "reference": body.reference_code,
            "actor": principal.user_id,
        })).mappings().one()
    except IntegrityError as exc:
        await session.rollback()
        raise BusinessError("LEGAL_HOLD_CONFLICT", "法务保留编号已存在", status_code=409) from exc
    await session.execute(text("""
        UPDATE tenant_deletion_requests
           SET status='blocked',updated_at=now()
         WHERE tenant_id=:tenant AND status='scheduled'
    """), {"tenant": tenant_id})
    await _audit(
        session, principal, action="admin.legal_hold.create", resource="tenant",
        resource_id=tenant_id, before=None, after=_mapping(row),
        request_id=request.state.request_id, tenant_id=tenant_id,
    )
    await session.commit()
    return SuccessEnvelope(data=_mapping(row), request_id=request.state.request_id)


@router.post("/enterprise-users/{tenant_id}/legal-holds/{hold_uuid}:release",
             operation_id="API-ADM-34")
async def release_tenant_legal_hold(
    tenant_id: int,
    hold_uuid: UUID,
    body: TenantLegalHoldRelease,
    request: Request,
    session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
):
    row = (await session.execute(text("""
        UPDATE tenant_legal_holds
           SET released_at=now(),released_by=:actor,release_reason=:reason
         WHERE tenant_id=:tenant AND hold_uuid=:hold AND released_at IS NULL
        RETURNING hold_uuid::text,tenant_id,reason,reference_code,placed_at,
                  released_at,release_reason
    """), {
        "tenant": tenant_id,
        "hold": hold_uuid,
        "actor": principal.user_id,
        "reason": body.reason,
    })).mappings().one_or_none()
    if row is None:
        raise BusinessError("LEGAL_HOLD_NOT_FOUND", "有效法务保留不存在", status_code=404)
    active_hold = bool((await session.execute(text("""
        SELECT EXISTS(
          SELECT FROM tenant_legal_holds
           WHERE tenant_id=:tenant AND released_at IS NULL
        )
    """), {"tenant": tenant_id})).scalar_one())
    if not active_hold:
        await session.execute(text("""
            UPDATE tenant_deletion_requests
               SET status='scheduled',updated_at=now()
             WHERE tenant_id=:tenant AND status='blocked'
        """), {"tenant": tenant_id})
    await _audit(
        session, principal, action="admin.legal_hold.release", resource="tenant",
        resource_id=tenant_id, before=None, after=_mapping(row),
        request_id=request.state.request_id, tenant_id=tenant_id,
    )
    await session.commit()
    return SuccessEnvelope(data=_mapping(row), request_id=request.state.request_id)


@router.get("/forecast-models", operation_id="API-ADM-17")
async def list_enterprise_forecast_models(
    request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
):
    rows = (await session.execute(text("""SELECT t.id AS tenant_id,t.tenant_code,
        t.name AS enterprise_name,t.status AS tenant_status,t.entitlements,
        m.version,m.model_scope,m.engine,m.training_data_through,m.metrics,
        d.deployment_uuid::text,d.deployed_at,
        COALESCE(c.sku_count,0) AS sku_count,
        latest.status AS latest_training_status,latest.created_at AS latest_training_at
        FROM tenants t
        LEFT JOIN forecast_model_deployments d ON d.id=(
          SELECT d1.id FROM forecast_model_deployments d1
           WHERE d1.tenant_id=t.id AND d1.scenario_code='sales_forecast' AND d1.status='active'
           ORDER BY d1.deployed_at DESC LIMIT 1)
        LEFT JOIN forecast_models m ON m.id=d.model_id
        LEFT JOIN LATERAL (SELECT count(*)::int AS sku_count FROM tenant_sku_catalog c1
          WHERE c1.tenant_id=t.id AND c1.lifecycle_status='active') c ON true
        LEFT JOIN LATERAL (SELECT r.status,r.created_at FROM forecast_training_runs r
          WHERE r.tenant_id=t.id ORDER BY r.created_at DESC LIMIT 1) latest ON true
        WHERE t.status<>'closed' AND t.data_class='business'
          AND 'sales_forecast'=ANY(SELECT jsonb_array_elements_text(t.entitlements))
        ORDER BY t.name,t.id"""))).mappings().all()
    return SuccessEnvelope(data={"items": [_mapping(row) for row in rows]},
                           request_id=request.state.request_id)


@router.get("/forecast-models/{tenant_id}", operation_id="API-ADM-18")
async def enterprise_forecast_model_detail(
    tenant_id: int, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
):
    tenant = (await session.execute(text("""SELECT id,tenant_code,name,status,entitlements
        FROM tenants WHERE id=:tenant AND data_class='business'"""),
        {"tenant": tenant_id})).mappings().one_or_none()
    if tenant is None:
        raise BusinessError("TENANT_NOT_FOUND", "企业用户不存在", status_code=404)
    deployments = (await session.execute(text("""SELECT d.deployment_uuid::text,d.status AS deployment_status,
        d.deployed_at,d.retired_at,d.route_policy,m.model_uuid::text,m.version,m.model_scope,
        m.engine,m.training_data_through,m.metrics,m.created_at
        FROM forecast_model_deployments d JOIN forecast_models m ON m.id=d.model_id
        WHERE d.tenant_id=:tenant AND d.scenario_code='sales_forecast'
        ORDER BY d.deployed_at DESC LIMIT 50"""), {"tenant": tenant_id})).mappings().all()
    runs = await ForecastTrainingService(request.app.state.settings,
        request.app.state.forecast_runtime).list(session, tenant_id=tenant_id, limit=50)
    return SuccessEnvelope(data={**_mapping(tenant), "enterprise_name": tenant["name"],
        "deployments": [_mapping(row) for row in deployments], "training_runs": runs},
        request_id=request.state.request_id)


async def _run_admin_training(app: Any, dispatch: dict[str, Any]) -> None:
    try:
        async with app.state.admin_database.session_factory() as background_session:
            await ForecastTrainingService(app.state.settings, app.state.forecast_runtime).execute(
                background_session, **dispatch)
    except Exception:
        app.state.logger.exception("admin_forecast_training_failed",
                                   extra={"training_uuid": dispatch["training_uuid"]})


@router.post("/forecast-models/{tenant_id}/append", status_code=202,
             operation_id="API-ADM-23")
async def append_enterprise_forecast_data(
    tenant_id: int, request: Request, background_tasks: BackgroundTasks,
    session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    orders_file: Annotated[UploadFile, File(description="企业订单 .xlsx 文件")],
    inventory_file: Annotated[UploadFile | None, File(description="企业库存 .xlsx 文件")] = None,
):
    tenant = (await session.execute(text("""SELECT id,status,entitlements FROM tenants
        WHERE id=:tenant"""), {"tenant": tenant_id})).mappings().one_or_none()
    if tenant is None:
        raise BusinessError("TENANT_NOT_FOUND", "企业用户不存在", status_code=404)
    if tenant["status"] != "active":
        raise BusinessError("TENANT_SUSPENDED", "企业用户当前不可更新模型", status_code=409)
    if "sales_forecast" not in (tenant["entitlements"] or []):
        raise BusinessError("FORECAST_NOT_ENTITLED", "该企业未开通销量预测", status_code=403)
    orders_content = await orders_file.read()
    inventory_content = await inventory_file.read() if inventory_file else None
    service = ForecastTrainingService(request.app.state.settings, request.app.state.forecast_runtime)
    try:
        status_code, data = await service.accept(
            session, tenant_id=tenant_id, user_id=principal.user_id,
            idempotency_key=idempotency_key,
            orders_filename=orders_file.filename or "orders.xlsx", orders_content=orders_content,
            inventory_filename=inventory_file.filename if inventory_file else None,
            inventory_content=inventory_content, allow_history_overwrite=False)
        await _audit(session, principal, action="admin.forecast.train", resource="tenant",
                     resource_id=tenant_id, before=None,
                     after={"training_uuid": data["training_uuid"], "status": data["status"]},
                     request_id=request.state.request_id, tenant_id=tenant_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    if status_code == 202:
        dispatch = {"tenant_id": tenant_id, "training_uuid": data["training_uuid"]}
        if request.app.state.job_queue is not None:
            await enqueue_job(request.app, "forecast_training", dispatch,
                              job_id=f"forecast-training:{data['training_uuid']}")
        else:
            background_tasks.add_task(_run_admin_training, request.app, dispatch)
    content = SuccessEnvelope(data=data, request_id=request.state.request_id).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=content)


@router.post("/forecast-models/{tenant_id}/replace", operation_id="API-ADM-24")
async def replace_enterprise_forecast_model(
    tenant_id: int, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    version: Annotated[str, Form(min_length=1, max_length=64)],
    algorithm: Annotated[str, Form(pattern="^(current|xgboost|lightgbm|custom)$")],
    code_file: Annotated[UploadFile, File(description="审核通过的预测模型 Python 代码")],
    parameters_file: Annotated[UploadFile, File(description="模型参数 JSON 文件")],
    change_note: Annotated[str, Form(max_length=500)] = "",
):
    tenant = (await session.execute(text("""SELECT id,status,entitlements FROM tenants
        WHERE id=:tenant"""), {"tenant": tenant_id})).mappings().one_or_none()
    if tenant is None:
        raise BusinessError("TENANT_NOT_FOUND", "企业用户不存在", status_code=404)
    if tenant["status"] != "active":
        raise BusinessError("TENANT_SUSPENDED", "企业用户当前不可更新模型", status_code=409)
    if "sales_forecast" not in (tenant["entitlements"] or []):
        raise BusinessError("FORECAST_NOT_ENTITLED", "该企业未开通销量预测", status_code=403)
    service = ForecastModelReplacementService(request.app.state.settings,
                                              request.app.state.forecast_runtime)
    try:
        data = await service.replace(
            session, tenant_id=tenant_id, user_id=principal.user_id,
            version=version, algorithm=algorithm, change_note=change_note,
            code_filename=code_file.filename or "forecast.py",
            code_content=await code_file.read(),
            parameters_filename=parameters_file.filename or "model_params.json",
            parameters_content=await parameters_file.read())
        await _audit(session, principal, action="admin.forecast.model.replace",
                     resource="forecast_model", resource_id=data["model_id"], before=None,
                     after={key: data[key] for key in ("model_uuid", "version", "engine",
                                                       "deployment_uuid")},
                     request_id=request.state.request_id, tenant_id=tenant_id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/forecast-models/{tenant_id}/deployments/{deployment_uuid}:rollback",
             operation_id="API-ADM-25")
async def rollback_enterprise_forecast_model(
    tenant_id: int, deployment_uuid: UUID, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
):
    deployed, current = await ForecastPublication().rollback(
        session, request.app.state.forecast_runtime, tenant_id=tenant_id,
        deployment_uuid=deployment_uuid, user_id=principal.user_id)
    await _audit(session, principal, action="admin.forecast.rollback", resource="forecast_model",
                 resource_id=deployed["model_id"], before=_mapping(current), after=deployed,
                 request_id=request.state.request_id, tenant_id=tenant_id)
    await session.commit()
    return SuccessEnvelope(data=deployed, request_id=request.state.request_id)


@router.get("/tenant-roles", operation_id="API-ADM-27")
async def list_tenant_roles(
    request: Request,
    session: AdminDatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("tenant.members.manage"))
    ],
):
    rows = (await session.execute(text("""
        SELECT r.role_code,r.display_name,r.description,r.built_in,
               COALESCE(array_agg(p.permission_code ORDER BY p.permission_code)
                 FILTER(WHERE p.permission_code IS NOT NULL),ARRAY[]::varchar[]) permissions
          FROM tenant_roles r
          LEFT JOIN role_permissions p
            ON p.role_id=r.id AND p.tenant_id=r.tenant_id
         WHERE r.tenant_id=:tenant
         GROUP BY r.id
         ORDER BY r.built_in DESC,r.role_code
    """), {"tenant": principal.tenant_id})).mappings().all()
    return SuccessEnvelope(
        data={"items": [_mapping(row) for row in rows]},
        request_id=request.state.request_id,
    )


@router.get("/tenant-members", operation_id="API-ADM-28")
async def list_tenant_members(
    request: Request,
    session: AdminDatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("tenant.members.manage"))
    ],
):
    rows = (await session.execute(text("""
        SELECT u.id AS user_id,u.email,u.name,u.status,
               COALESCE(array_agg(r.role_code ORDER BY r.role_code)
                 FILTER(WHERE r.role_code IS NOT NULL),ARRAY[]::varchar[]) role_codes
          FROM users u
          LEFT JOIN user_role_assignments a
            ON a.user_id=u.id AND a.tenant_id=u.tenant_id
           AND (a.expires_at IS NULL OR a.expires_at>now())
          LEFT JOIN tenant_roles r
            ON r.id=a.role_id AND r.tenant_id=a.tenant_id
         WHERE u.tenant_id=:tenant AND u.role_code='user'
         GROUP BY u.id
         ORDER BY u.created_at,u.id
    """), {"tenant": principal.tenant_id})).mappings().all()
    return SuccessEnvelope(
        data={"items": [_mapping(row) for row in rows]},
        request_id=request.state.request_id,
    )


@router.put("/tenant-members/{user_id}/roles", operation_id="API-ADM-29")
async def update_tenant_member_roles(
    user_id: int,
    body: TenantMemberRolesUpdate,
    request: Request,
    session: AdminDatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("tenant.members.manage"))
    ],
):
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"furniscope.rbac.{principal.tenant_id}"},
    )
    member = (await session.execute(text("""
        SELECT id,email,name,status FROM users
         WHERE id=:user AND tenant_id=:tenant AND role_code='user'
         FOR UPDATE
    """), {"user": user_id, "tenant": principal.tenant_id})).mappings().one_or_none()
    if member is None:
        raise BusinessError("USER_NOT_FOUND", "企业成员不存在或不可访问", status_code=404)
    roles = (await session.execute(text("""
        SELECT id,role_code FROM tenant_roles
         WHERE tenant_id=:tenant AND role_code=ANY(CAST(:codes AS varchar[]))
    """), {"tenant": principal.tenant_id, "codes": body.role_codes})).mappings().all()
    if len(roles) != len(body.role_codes):
        raise BusinessError("TENANT_ROLE_INVALID", "企业角色不存在", status_code=422)
    before = (await session.execute(text("""
        SELECT r.role_code FROM user_role_assignments a
        JOIN tenant_roles r ON r.id=a.role_id AND r.tenant_id=a.tenant_id
        WHERE a.tenant_id=:tenant AND a.user_id=:user
        ORDER BY r.role_code
    """), {"tenant": principal.tenant_id, "user": user_id})).scalars().all()
    if "tenant_owner" in before and "tenant_owner" not in body.role_codes:
        owner_count = int((await session.execute(text("""
            SELECT count(DISTINCT a.user_id)
              FROM user_role_assignments a
              JOIN tenant_roles r ON r.id=a.role_id AND r.tenant_id=a.tenant_id
             WHERE a.tenant_id=:tenant AND r.role_code='tenant_owner'
               AND (a.expires_at IS NULL OR a.expires_at>now())
        """), {"tenant": principal.tenant_id})).scalar_one())
        if owner_count <= 1:
            raise BusinessError(
                "LAST_TENANT_OWNER_REQUIRED",
                "企业必须保留至少一名所有者",
                status_code=409,
            )
    await session.execute(text("""
        DELETE FROM user_role_assignments
         WHERE tenant_id=:tenant AND user_id=:user
    """), {"tenant": principal.tenant_id, "user": user_id})
    await session.execute(text("""
        INSERT INTO user_role_assignments(tenant_id,user_id,role_id,assigned_by)
        SELECT :tenant,:user,r.id,:actor FROM tenant_roles r
         WHERE r.tenant_id=:tenant AND r.role_code=ANY(CAST(:codes AS varchar[]))
    """), {
        "tenant": principal.tenant_id,
        "user": user_id,
        "actor": principal.user_id,
        "codes": body.role_codes,
    })
    after = sorted(body.role_codes)
    await _audit(
        session,
        principal,
        action="tenant.member.roles.update",
        resource="user",
        resource_id=user_id,
        before={"role_codes": list(before)},
        after={"role_codes": after},
        request_id=request.state.request_id,
    )
    await session.commit()
    return SuccessEnvelope(
        data={
            **_mapping(member),
            "role_codes": after,
        },
        request_id=request.state.request_id,
    )


@router.get("/audit-events", operation_id="API-ADM-30")
async def list_audit_events(
    request: Request,
    session: AdminDatabaseSession,
    pagination: Pagination,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("audit.read"))
    ],
):
    total = int((await session.execute(
        text("SELECT count(*) FROM audit_logs WHERE tenant_id=:tenant"),
        {"tenant": principal.tenant_id},
    )).scalar_one())
    rows = (await session.execute(text("""
        SELECT event_uuid::text,chain_sequence,previous_hash,event_hash,
               actor_user_id,action_code,resource_type,resource_id,request_id,
               before_snapshot,after_snapshot,occurred_at
          FROM audit_logs
         WHERE tenant_id=:tenant
         ORDER BY chain_sequence DESC
         OFFSET :offset LIMIT :limit
    """), {
        "tenant": principal.tenant_id,
        "offset": pagination.offset,
        "limit": pagination.page_size,
    })).mappings().all()
    return SuccessEnvelope(
        data=_page([_mapping(row) for row in rows], total, pagination),
        request_id=request.state.request_id,
    )


@router.get("/audit-chain:verify", operation_id="API-ADM-31")
async def verify_audit_chain(
    request: Request,
    session: AdminDatabaseSession,
    principal: Annotated[
        AuthenticatedPrincipal, Depends(require_permission("audit.read"))
    ],
):
    result = (await session.execute(text("""
        WITH ordered AS (
          SELECT a.*,
                 lag(event_hash,1,repeat('0',64)::char(64))
                   OVER(ORDER BY chain_sequence) AS expected_previous
            FROM audit_logs a
           WHERE tenant_id=:tenant
        ), verified AS (
          SELECT *,
                 audit_event_hash(
                   event_uuid,tenant_id,chain_sequence,previous_hash,actor_user_id,
                   action_code,resource_type,resource_id,request_id,before_snapshot,
                   after_snapshot,ip_address,user_agent,occurred_at
                 ) AS expected_hash
            FROM ordered
        )
        SELECT count(*)::int AS event_count,
               count(*) FILTER(
                 WHERE previous_hash<>expected_previous OR event_hash<>expected_hash
               )::int AS invalid_count,
               min(chain_sequence) FILTER(
                 WHERE previous_hash<>expected_previous OR event_hash<>expected_hash
               ) AS first_invalid_sequence
          FROM verified
    """), {"tenant": principal.tenant_id})).mappings().one()
    data = _mapping(result)
    data["valid"] = data["invalid_count"] == 0
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/users", operation_id="API-ADM-01", include_in_schema=False)
async def list_users(request: Request, session: AdminDatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    tenant_id: int | None = None, status: str | None = None,
    keyword: str | None = Query(default=None, max_length=200)):
    if tenant_id is not None and tenant_id != principal.tenant_id:
        raise BusinessError("ADMIN_DIAGNOSTIC_SCOPE_DENIED", "管理员不能跨租户查询", status_code=403)
    if status and status not in {"invited", "active", "disabled", "locked"}:
        raise BusinessError("USER_FILTER_INVALID", "用户状态筛选无效", status_code=400)
    params = {"tenant": principal.tenant_id, "status": status, "keyword": f"%{keyword}%" if keyword else None,
              "offset": pagination.offset, "limit": pagination.page_size}
    where = "u.tenant_id=:tenant AND (CAST(:status AS varchar) IS NULL OR u.status=:status) AND (CAST(:keyword AS varchar) IS NULL OR u.email ILIKE :keyword OR u.name ILIKE :keyword)"
    total = int((await session.execute(text(f"SELECT count(*) FROM furniscope.users u WHERE {where}"), params)).scalar_one())
    rows = (await session.execute(text(f"""SELECT u.id user_id,u.tenant_id,u.email,u.name,u.role_code,u.status,
        u.last_login_at,u.created_at,u.updated_at FROM furniscope.users u WHERE {where}
        ORDER BY u.created_at DESC OFFSET :offset LIMIT :limit"""), params)).mappings().all()
    items = [{**_mapping(row), "resource_version": resource_version(row["updated_at"])} for row in rows]
    return SuccessEnvelope(data=_page(items, total, pagination), request_id=request.state.request_id)


@router.patch("/users/{user_id}", operation_id="API-ADM-02", include_in_schema=False)
async def update_user(user_id: int, body: AdminUserUpdate, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    if_match: Annotated[str, Header(alias="If-Match")]):
    row = (await session.execute(text("SELECT * FROM furniscope.users WHERE id=:id AND tenant_id=:tenant FOR UPDATE"),
                                 {"id": user_id, "tenant": principal.tenant_id})).mappings().one_or_none()
    if row is None:
        raise BusinessError("USER_NOT_FOUND", "用户不存在或不可访问", status_code=404)
    require_version(if_match, row["updated_at"])
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise BusinessError("REQUEST_VALIDATION_FAILED", "至少提供一个修改字段", status_code=422)
    if user_id == principal.user_id and changes.get("status") not in {None, "active"}:
        raise BusinessError("USER_ROLE_INVALID", "管理员不能停用当前会话账号", status_code=422)
    assignments = ",".join(f"{key}=:{key}" for key in changes)
    updated = (await session.execute(text(f"""UPDATE furniscope.users SET {assignments},updated_at=now()
        WHERE id=:id AND tenant_id=:tenant RETURNING id user_id,tenant_id,email,name,role_code,status,last_login_at,created_at,updated_at"""),
        {**changes, "id": user_id, "tenant": principal.tenant_id})).mappings().one()
    await _audit(session, principal, action="admin.user.update", resource="user", resource_id=user_id,
                 before={k: row[k] for k in changes}, after={k: updated[k] for k in changes}, request_id=request.state.request_id)
    await session.commit()
    data = {**_mapping(updated), "resource_version": resource_version(updated["updated_at"])}
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.get("/model-routes/{task_type}", operation_id="API-ADM-03", include_in_schema=False)
async def get_model_route(task_type: str, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    internal_token: str | None = Header(default=None, alias="X-Internal-Token")):
    _authorize(request, internal_token)
    row = (await session.execute(text("""SELECT id,task_type,config_version,primary_model_id,fallback_model_ids,
        timeout_ms,max_retries,batch_size,concurrency_limit,compute_config,active,updated_at
        FROM furniscope.model_route_configs WHERE task_type=:task ORDER BY active DESC,updated_at DESC LIMIT 1"""),
        {"task": task_type})).mappings().one_or_none()
    if row is None:
        raise BusinessError("MODEL_ROUTE_NOT_FOUND", "模型路由不存在", status_code=404)
    return SuccessEnvelope(data={**_mapping(row), "resource_version": resource_version(row["updated_at"])}, request_id=request.state.request_id)


@router.put("/model-routes/{task_type}", operation_id="API-ADM-04", include_in_schema=False)
async def update_model_route(task_type: str, body: ModelRouteUpdate, request: Request,
    session: AdminDatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    if_match: Annotated[str, Header(alias="If-Match")],
    internal_token: str | None = Header(default=None, alias="X-Internal-Token")):
    _authorize(request, internal_token)
    idem = IdempotencyService()
    decision = await idem.begin(
        session, tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
        route_code="API-ADM-04", http_method="PUT", idempotency_key=idempotency_key,
        request_payload={"task_type": task_type, **body.model_dump(mode="json")},
    )
    if decision.action == "replay":
        await session.commit()
        return JSONResponse(status_code=int(decision.response_status), content=decision.response_body)
    row = (await session.execute(text("SELECT * FROM furniscope.model_route_configs WHERE task_type=:task ORDER BY active DESC,updated_at DESC LIMIT 1 FOR UPDATE"), {"task": task_type})).mappings().one_or_none()
    if row is None:
        raise BusinessError("MODEL_ROUTE_NOT_FOUND", "模型路由不存在", status_code=404)
    require_version(if_match, row["updated_at"])
    values = body.model_dump(mode="json")
    if values["active"]:
        await session.execute(text("UPDATE furniscope.model_route_configs SET active=false,updated_at=now() WHERE task_type=:task AND id<>:id"), {"task": task_type, "id": row["id"]})
    updated = (await session.execute(text("""UPDATE furniscope.model_route_configs SET
        primary_model_id=:primary_model_id,fallback_model_ids=CAST(:fallback AS jsonb),timeout_ms=:timeout_ms,
        max_retries=:max_retries,batch_size=:batch_size,concurrency_limit=:concurrency_limit,
        compute_config=CAST(:compute AS jsonb),active=:active,updated_at=now() WHERE id=:id RETURNING *"""),
        {**values, "fallback": json.dumps(values.pop("fallback_model_ids")), "compute": json.dumps(values.pop("compute_config")), "id": row["id"]})).mappings().one()
    await _audit(session, principal, action="admin.model_route.update", resource="model_route", resource_id=row["id"], before=_mapping(row), after=_mapping(updated), request_id=request.state.request_id)
    content = SuccessEnvelope(data={**_mapping(updated), "resource_version": resource_version(updated["updated_at"])}, request_id=request.state.request_id).model_dump(mode="json")
    await idem.finish(session, tenant_id=principal.tenant_id, record_id=decision.record_id,
                      response_status=200, response_body=content,
                      resource_type="model_route", resource_public_id=task_type)
    await session.commit()
    return JSONResponse(status_code=200, content=content)


@router.get("/prompt-templates", operation_id="API-ADM-05", include_in_schema=False)
async def list_prompt_templates(request: Request, session: AdminDatabaseSession, pagination: Pagination,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)], code: str | None = None,
    task_type: str | None = None, status: str | None = None, version: str | None = None,
    internal_token: str | None = Header(default=None, alias="X-Internal-Token")):
    _authorize(request, internal_token)
    params = {"code": code, "task": task_type, "status": status, "version": version,
              "offset": pagination.offset, "limit": pagination.page_size}
    where = "(CAST(:code AS varchar) IS NULL OR code=:code) AND (CAST(:task AS varchar) IS NULL OR task_type=:task) AND (CAST(:status AS varchar) IS NULL OR status=:status) AND (CAST(:version AS varchar) IS NULL OR version=:version)"
    total = int((await session.execute(text(f"SELECT count(*) FROM furniscope.prompt_templates WHERE {where}"), params)).scalar_one())
    rows = (await session.execute(text(f"SELECT * FROM furniscope.prompt_templates WHERE {where} ORDER BY created_at DESC OFFSET :offset LIMIT :limit"), params)).mappings().all()
    return SuccessEnvelope(data=_page([_mapping(r) for r in rows], total, pagination), request_id=request.state.request_id)


@router.post("/prompt-templates", status_code=201, operation_id="API-ADM-06",
             include_in_schema=False)
async def create_prompt_template(body: PromptTemplateCreate, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    internal_token: str | None = Header(default=None, alias="X-Internal-Token")):
    _authorize(request, internal_token)
    values = body.model_dump(mode="json")
    try:
        idem = IdempotencyService()
        decision = await idem.begin(
            session, tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
            route_code="API-ADM-06", http_method="POST", idempotency_key=idempotency_key,
            request_payload={
                **{key: value for key, value in values.items() if key != "template_content"},
                "template_sha256": hashlib.sha256(values["template_content"].encode()).hexdigest(),
            },
        )
        if decision.action == "replay":
            await session.commit()
            return JSONResponse(status_code=int(decision.response_status), content=decision.response_body)
        row = (await session.execute(text("""INSERT INTO furniscope.prompt_templates
            (code,version,task_type,template_content,output_schema,status,evaluation_set_version)
            VALUES(:code,:version,:task_type,:template_content,CAST(:schema AS jsonb),:status,:evaluation_set_version)
            RETURNING *"""), {**values, "schema": json.dumps(values.pop("output_schema")) if values.get("output_schema") is not None else None})).mappings().one()
        await _audit(session, principal, action="admin.prompt.create", resource="prompt_template", resource_id=row["id"], before=None, after=_mapping(row), request_id=request.state.request_id)
        content = SuccessEnvelope(data=_mapping(row), request_id=request.state.request_id).model_dump(mode="json")
        await idem.finish(session, tenant_id=principal.tenant_id, record_id=decision.record_id,
                          response_status=201, response_body=content,
                          resource_type="prompt_template", resource_public_id=f"{row['code']}:{row['version']}")
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise BusinessError("PROMPT_VERSION_CONFLICT", "提示词版本已存在", status_code=409) from exc
    return JSONResponse(status_code=201, content=content)


@router.get("/data-sources", operation_id="API-ADM-09", include_in_schema=False)
async def list_data_sources(request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)]):
    rows = (await session.execute(text("""SELECT source_uuid::text,name,source_kind,connection_type,
        secret_ref,authorization_reference,safe_config,status,created_at,updated_at
        FROM furniscope.tenant_data_sources WHERE tenant_id=:tenant ORDER BY created_at DESC"""),
        {"tenant": principal.tenant_id})).mappings().all()
    return SuccessEnvelope(data={"items": [_mapping(row) for row in rows]},
                           request_id=request.state.request_id)


@router.post("/data-sources", status_code=201, operation_id="API-ADM-10", include_in_schema=False)
async def create_data_source(body: TenantDataSourceCreate, request: Request,
    session: AdminDatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)]):
    values = body.model_dump(mode="json")
    try:
        row = (await session.execute(text("""INSERT INTO furniscope.tenant_data_sources
            (tenant_id,name,source_kind,connection_type,secret_ref,authorization_reference,
             safe_config,created_by) VALUES(:tenant,:name,:source_kind,:connection_type,
             :secret_ref,:authorization_reference,CAST(:safe_config AS jsonb),:actor)
            RETURNING id,source_uuid::text,name,source_kind,connection_type,secret_ref,
                      authorization_reference,safe_config,status,created_at,updated_at"""),
            {**values, "tenant": principal.tenant_id, "actor": principal.user_id,
             "safe_config": json.dumps(values["safe_config"])})).mappings().one()
        await _audit(session, principal, action="admin.data_source.create", resource="tenant_data_source",
                     resource_id=row["id"], before=None, after=_mapping(row),
                     request_id=request.state.request_id)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise BusinessError("DATA_SOURCE_CONFLICT", "同名数据源已存在", status_code=409) from exc
    return SuccessEnvelope(data=_mapping(row), request_id=request.state.request_id)


@router.put("/sku-catalog/{sku}/{site}", operation_id="API-ADM-11", include_in_schema=False)
async def upsert_tenant_sku(sku: str, site: str, body: TenantSkuUpsert, request: Request,
    session: AdminDatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)]):
    normalized_site = site.strip().upper()
    if body.sku != sku or body.site.strip().upper() != normalized_site:
        raise BusinessError("SKU_PATH_MISMATCH", "路径与请求体的 SKU/站点不一致", status_code=422)
    source_id = None
    if body.source_uuid:
        source_id = (await session.execute(text("""SELECT id FROM furniscope.tenant_data_sources
            WHERE source_uuid=CAST(:uuid AS uuid) AND tenant_id=:tenant AND status='active'"""),
            {"uuid": body.source_uuid, "tenant": principal.tenant_id})).scalar_one_or_none()
        if source_id is None:
            raise BusinessError("DATA_SOURCE_NOT_FOUND", "数据源不存在或不可访问", status_code=404)
    values = body.model_dump(mode="json")
    row = (await session.execute(text("""INSERT INTO furniscope.tenant_sku_catalog
        (tenant_id,sku,site,category_code,lifecycle_status,label_status,history_weeks,
         model_eligible,source_id,attributes)
        VALUES(:tenant,:sku,:site,:category_code,:lifecycle_status,:label_status,:history_weeks,
               :model_eligible,:source_id,CAST(:attributes AS jsonb))
        ON CONFLICT(tenant_id,sku,site) DO UPDATE SET category_code=EXCLUDED.category_code,
          lifecycle_status=EXCLUDED.lifecycle_status,label_status=EXCLUDED.label_status,
          history_weeks=EXCLUDED.history_weeks,model_eligible=EXCLUDED.model_eligible,
          source_id=EXCLUDED.source_id,attributes=EXCLUDED.attributes,updated_at=now()
        RETURNING id,sku,site,category_code,lifecycle_status,label_status,history_weeks,
                  model_eligible,attributes,created_at,updated_at"""),
        {**values, "tenant": principal.tenant_id, "site": normalized_site,
         "source_id": source_id, "attributes": json.dumps(values["attributes"])})).mappings().one()
    await _audit(session, principal, action="admin.sku.upsert", resource="tenant_sku",
                 resource_id=row["id"], before=None, after=_mapping(row),
                 request_id=request.state.request_id)
    await session.commit()
    return SuccessEnvelope(data=_mapping(row), request_id=request.state.request_id)


@router.get("/sku-catalog", operation_id="API-ADM-13", include_in_schema=False)
async def list_tenant_sku_catalog(request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    site: str | None = Query(default=None, min_length=2, max_length=16),
    lifecycle_status: str | None = None, limit: int = Query(default=5000, ge=1, le=5000)):
    if lifecycle_status and lifecycle_status not in {"active", "out_of_stock", "discontinued", "unknown"}:
        raise BusinessError("SKU_LIFECYCLE_INVALID", "SKU 生命周期状态无效", status_code=400)
    rows = (await session.execute(text("""SELECT c.sku,c.site,c.category_code,c.lifecycle_status,
        c.label_status,c.history_weeks,c.model_eligible,c.attributes,s.source_uuid::text,
        CASE WHEN c.model_eligible THEN 'v4'
             WHEN c.history_weeks>=4 THEN 'baseline_or_reference_required'
             WHEN c.category_code IS NOT NULL THEN 'reference_or_baseline_required'
             ELSE 'insufficient_data' END AS forecast_strategy,
        c.created_at,c.updated_at
        FROM furniscope.tenant_sku_catalog c
        LEFT JOIN furniscope.tenant_data_sources s
          ON s.id=c.source_id AND s.tenant_id=c.tenant_id
        WHERE c.tenant_id=:tenant
          AND (CAST(:site AS varchar) IS NULL OR c.site=:site)
          AND (CAST(:lifecycle AS varchar) IS NULL OR c.lifecycle_status=:lifecycle)
        ORDER BY c.sku,c.site LIMIT :limit"""), {
            "tenant": principal.tenant_id, "site": site.upper() if site else None,
            "lifecycle": lifecycle_status, "limit": limit,
        })).mappings().all()
    return SuccessEnvelope(data={"items": [_mapping(row) for row in rows]},
                           request_id=request.state.request_id)


@router.get("/forecast-deployment", operation_id="API-ADM-12", include_in_schema=False)
async def get_forecast_deployment(request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)]):
    from ..repositories.forecast_repository import ForecastRepository
    row = await ForecastRepository().active_deployment(session, tenant_id=principal.tenant_id)
    if row is None:
        raise BusinessError("FORECAST_DEPLOYMENT_NOT_FOUND", "当前租户尚未部署预测模型", status_code=404)
    row.pop("state_uri", None)
    return SuccessEnvelope(data=row, request_id=request.state.request_id)


@router.get("/analysis-tasks/{task_uuid}/diagnostics", operation_id="API-ADM-07", include_in_schema=False)
async def task_diagnostics(task_uuid: UUID, request: Request, session: AdminDatabaseSession,
    principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    include_model_runs: bool = True, include_control_events: bool = True,
    include_partial_failures: bool = True):
    task = (await session.execute(text("SELECT * FROM furniscope.analysis_tasks WHERE task_uuid=:uuid AND tenant_id=:tenant"), {"uuid": str(task_uuid), "tenant": principal.tenant_id})).mappings().one_or_none()
    if task is None:
        raise BusinessError("TASK_NOT_FOUND", "任务不存在或不可访问", status_code=404)
    stages = (await session.execute(text("SELECT stage_code,attempt_no,status,started_at,ended_at,error_code,retryable FROM furniscope.task_stage_runs WHERE task_id=:id ORDER BY id"), {"id": task["id"]})).mappings().all()
    data = {"task": _mapping(task), "stage_runs": [_mapping(r) for r in stages], "model_runs": [], "control_events": [], "partial_failures": []}
    if include_model_runs:
        rows = (await session.execute(text("SELECT id,provider,model_id,task_type,input_tokens,output_tokens,latency_ms,status,retry_count,schema_valid,error_code,created_at FROM furniscope.ai_model_runs WHERE task_id=:id ORDER BY id"), {"id": task["id"]})).mappings().all()
        data["model_runs"] = [_mapping(r) for r in rows]
    if include_control_events:
        rows = (await session.execute(text("SELECT event_uuid,event_type,checkpoint_id,stage_code,status,delivery_attempts,requested_at,consumed_at,error_code FROM furniscope.workflow_control_events WHERE task_id=:id ORDER BY id"), {"id": task["id"]})).mappings().all()
        data["control_events"] = [_mapping(r) for r in rows]
    if include_partial_failures:
        rows = (await session.execute(text("SELECT stage_code,unit_type,failed_unit_ids,failed_count,total_count,impact,confidence_cap,retryable,resolved_status,created_at FROM furniscope.workflow_partial_failures WHERE task_id=:id ORDER BY id"), {"id": task["id"]})).mappings().all()
        data["partial_failures"] = [_mapping(r) for r in rows]
    return SuccessEnvelope(data=data, request_id=request.state.request_id)


@router.post("/analysis-tasks/{task_uuid}:recover", status_code=202, operation_id="API-ADM-08", include_in_schema=False)
async def recover_task(task_uuid: UUID, body: WorkflowRecoveryRequest, request: Request,
    session: AdminDatabaseSession, principal: Annotated[AuthenticatedPrincipal, Depends(require_admin)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
    idem = IdempotencyService()
    decision = await idem.begin(
        session, tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
        route_code="API-ADM-08", http_method="POST", idempotency_key=idempotency_key,
        request_payload={"task_uuid": str(task_uuid), **body.model_dump(mode="json")},
    )
    if decision.action == "replay":
        await session.commit()
        return JSONResponse(status_code=int(decision.response_status), content=decision.response_body)
    task = (await session.execute(text("SELECT * FROM furniscope.analysis_tasks WHERE task_uuid=:uuid AND tenant_id=:tenant FOR UPDATE"), {"uuid": str(task_uuid), "tenant": principal.tenant_id})).mappings().one_or_none()
    if task is None:
        raise BusinessError("TASK_NOT_FOUND", "任务不存在或不可访问", status_code=404)
    pending = (await session.execute(text("SELECT EXISTS(SELECT 1 FROM furniscope.user_confirmations WHERE task_id=:id AND status='pending')"), {"id": task["id"]})).scalar_one()
    if pending:
        raise BusinessError("WORKFLOW_CONFIRMATION_PENDING", "任务正在等待用户确认，管理员不能代答", status_code=409)
    if body.event_type == "auto_retry":
        checkpoint = (await session.execute(text("SELECT checkpoint_id,stage_code FROM furniscope.workflow_checkpoints WHERE checkpoint_id=:checkpoint AND task_id=:task AND status='active' AND is_safe_resume"), {"checkpoint": body.checkpoint_id, "task": task["id"]})).mappings().one_or_none()
        if checkpoint is None or checkpoint["stage_code"] != body.stage_code:
            raise BusinessError("WORKFLOW_CHECKPOINT_CONFLICT", "安全检查点不存在或阶段不匹配", status_code=409)
    if task["status"] not in {"failed", "partial_succeeded", "running", "queued"}:
        raise BusinessError("WORKFLOW_NOT_RECOVERABLE", "当前任务状态不可恢复", status_code=409)
    event_uuid = uuid4()
    try:
        await session.execute(text("""INSERT INTO furniscope.workflow_control_events
            (event_uuid,tenant_id,task_id,event_type,checkpoint_id,stage_code,payload,idempotency_key,requested_by)
            VALUES(:event,:tenant,:task,:type,:checkpoint,:stage,CAST(:payload AS jsonb),:key,:user)"""),
            {"event": str(event_uuid), "tenant": principal.tenant_id, "task": task["id"], "type": body.event_type,
             "checkpoint": body.checkpoint_id, "stage": body.stage_code, "payload": json.dumps(body.payload),
             "key": idempotency_key, "user": principal.user_id})
        await _audit(session, principal, action=f"admin.workflow.{body.event_type}", resource="analysis_task", resource_id=task["id"], before={"status": task["status"]}, after={"event_uuid": str(event_uuid)}, request_id=request.state.request_id)
        data = {"event_uuid": str(event_uuid), "task_uuid": str(task_uuid), "event_type": body.event_type,
                "status": "pending", "checkpoint_id": body.checkpoint_id, "stage_code": body.stage_code}
        content = SuccessEnvelope(data=data, request_id=request.state.request_id).model_dump(mode="json")
        await idem.finish(session, tenant_id=principal.tenant_id, record_id=decision.record_id,
                          response_status=202, response_body=content,
                          resource_type="analysis_task", resource_public_id=str(task_uuid))
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise BusinessError("IDEMPOTENCY_CONFLICT", "恢复请求幂等键冲突", status_code=409) from exc
    return JSONResponse(status_code=202, content=content)
