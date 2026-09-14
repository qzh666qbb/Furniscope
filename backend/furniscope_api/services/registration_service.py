"""Public signup applications and admin approval."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import BusinessError
from ..security.password import PasswordService
from .enterprise_onboarding import provision_enterprise_account
from .resource_version import resource_version


_EMAIL_FIELDS = "id,application_uuid::text AS application_uuid,enterprise_name,contact_name,email,suggested_tenant_code,status,reject_reason,reviewed_at,approved_tenant_id,created_at,updated_at"


def suggest_tenant_code(enterprise_name: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", enterprise_name).upper()[:18]
    if len(compact) < 2:
        compact = "ENT"
    return f"{compact}_{uuid4().hex[:6].upper()}"[:32]


def _public(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["resource_version"] = resource_version(data.pop("updated_at", None))
    return data


class RegistrationService:
    def __init__(self, password_service: PasswordService | None = None) -> None:
        self.password_service = password_service or PasswordService()

    async def apply(
        self, session: AsyncSession, *, enterprise_name: str, contact_name: str, email: str, password: str,
    ) -> dict[str, Any]:
        existing_user = (await session.execute(text(
            "SELECT 1 FROM users WHERE email=:email"), {"email": email})).scalar_one_or_none()
        if existing_user is not None:
            raise BusinessError("REGISTRATION_EMAIL_EXISTS", "该邮箱已有账号，请直接登录", status_code=409)
        try:
            row = (await session.execute(text("""INSERT INTO registration_applications
                (enterprise_name,contact_name,email,password_hash,suggested_tenant_code,status)
                VALUES(:name,:contact,:email,:password,:code,'pending')
                RETURNING """ + _EMAIL_FIELDS), {
                "name": enterprise_name, "contact": contact_name, "email": email,
                "password": self.password_service.hash(password),
                "code": suggest_tenant_code(enterprise_name),
            })).mappings().one()
        except IntegrityError as exc:
            raise BusinessError("REGISTRATION_ALREADY_PENDING", "该邮箱已有待审批申请", status_code=409) from exc
        return _public(row)

    async def list(
        self, session: AsyncSession, *, status: str | None, offset: int, limit: int,
    ) -> tuple[list[dict[str, Any]], int]:
        params = {"status": status, "offset": offset, "limit": limit}
        where = "CAST(:status AS varchar) IS NULL OR status=:status"
        total = int((await session.execute(
            text(f"SELECT count(*) FROM registration_applications WHERE {where}"), params,
        )).scalar_one())
        rows = (await session.execute(text(f"""SELECT {_EMAIL_FIELDS}
            FROM registration_applications WHERE {where}
            ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC
            OFFSET :offset LIMIT :limit"""), params)).mappings().all()
        return [_public(row) for row in rows], total

    async def approve(
        self, session: AsyncSession, *, application_id: int, tenant_code: str, entitlements: list[str],
        actor_user_id: int,
    ) -> dict[str, Any]:
        application = (await session.execute(text("""SELECT * FROM registration_applications
            WHERE id=:id FOR UPDATE"""), {"id": application_id})).mappings().one_or_none()
        if application is None:
            raise BusinessError("REGISTRATION_NOT_FOUND", "注册申请不存在", status_code=404)
        if application["status"] != "pending":
            raise BusinessError("REGISTRATION_NOT_PENDING", "该申请已处理", status_code=409)
        existing_user = (await session.execute(text(
            "SELECT 1 FROM users WHERE email=:email"), {"email": application["email"]})).scalar_one_or_none()
        if existing_user is not None:
            raise BusinessError("REGISTRATION_EMAIL_EXISTS", "该邮箱已被开通，不能重复审批", status_code=409)
        try:
            provisioned = await provision_enterprise_account(
                session,
                tenant_code=tenant_code,
                enterprise_name=application["enterprise_name"],
                email=application["email"],
                contact_name=application["contact_name"],
                password_hash=application["password_hash"],
                entitlements=entitlements,
            )
        except IntegrityError as exc:
            raise BusinessError("ENTERPRISE_USER_CONFLICT", "租户编码或登录邮箱已存在", status_code=409) from exc
        await session.execute(text("""UPDATE registration_applications
            SET status='approved', reviewed_by=:actor, reviewed_at=now(),
                approved_tenant_id=:tenant, updated_at=now(), reject_reason=NULL
            WHERE id=:id"""), {
            "actor": actor_user_id, "tenant": provisioned["tenant_id"], "id": application_id,
        })
        return provisioned

    async def reject(
        self, session: AsyncSession, *, application_id: int, reason: str, actor_user_id: int,
    ) -> dict[str, Any]:
        application = (await session.execute(text("""SELECT * FROM registration_applications
            WHERE id=:id FOR UPDATE"""), {"id": application_id})).mappings().one_or_none()
        if application is None:
            raise BusinessError("REGISTRATION_NOT_FOUND", "注册申请不存在", status_code=404)
        if application["status"] != "pending":
            raise BusinessError("REGISTRATION_NOT_PENDING", "该申请已处理", status_code=409)
        row = (await session.execute(text("""UPDATE registration_applications
            SET status='rejected', reject_reason=:reason, reviewed_by=:actor,
                reviewed_at=now(), updated_at=now()
            WHERE id=:id RETURNING """ + _EMAIL_FIELDS), {
            "reason": reason, "actor": actor_user_id, "id": application_id,
        })).mappings().one()
        return _public(row)
