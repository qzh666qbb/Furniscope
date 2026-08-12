"""Product use cases with durable HTTP idempotency."""

from typing import Any
import json

from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import BusinessError
from ..repositories.product_repository import ProductRepository
from .idempotency_service import IdempotencyService
from .resource_version import require_version, resource_version


class ProductService:
    def __init__(self) -> None:
        self.repository = ProductRepository()
        self.idempotency = IdempotencyService()

    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     idempotency_key: str, payload: dict[str, Any], response_envelope,
                     error_envelope) -> tuple[int, dict[str, Any]]:
        if payload["category_code"] != "sofa":
            raise BusinessError("PRODUCT_CATEGORY_INVALID", "P0仅支持sofa品类", status_code=422)
        decision = await self.idempotency.begin(session, tenant_id=tenant_id, actor_user_id=user_id,
            route_code="API-PRD-01", http_method="POST", idempotency_key=idempotency_key,
            request_payload=payload)
        if decision.action == "replay":
            return int(decision.response_status), dict(decision.response_body or {})
        row = await self.repository.create(session, tenant_id=tenant_id, user_id=user_id, **payload)
        if row is None:
            envelope = error_envelope("PRODUCT_SKU_CONFLICT", "SKU在当前租户内已存在")
            await self.idempotency.finish(session, tenant_id=tenant_id, record_id=decision.record_id,
                response_status=409, response_body=envelope)
            return 409, envelope
        envelope = response_envelope(row)
        await self.idempotency.finish(session, tenant_id=tenant_id, record_id=decision.record_id,
            response_status=201, response_body=envelope, resource_type="products",
            resource_public_id=str(row["product_id"]))
        return 201, envelope

    async def detail(self, session: AsyncSession, *, tenant_id: int, product_id: int,
                     profile_version_id: int | None) -> dict[str, Any]:
        product = await self.repository.get_product(session, tenant_id=tenant_id, product_id=product_id)
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        profile = await self.repository.get_profile(session, tenant_id=tenant_id, product_id=product_id,
                                                    profile_version_id=profile_version_id)
        if profile_version_id is not None and profile is None:
            raise BusinessError("PRODUCT_PROFILE_NOT_FOUND", "产品画像版本不存在", status_code=404)
        attributes = [] if profile is None else await self.repository.attributes(
            session, tenant_id=tenant_id, profile_version_id=profile["profile_version_id"])
        version = resource_version(product.pop("updated_at"))
        return product | {
            "profile_version": None if profile is None else profile["profile_version"],
            "completeness_score": 0 if profile is None else profile["completeness_score"],
            "source_summary": {} if profile is None else profile["source_summary"],
            "attributes": attributes,
            "resource_version": version,
        }

    async def update(self, session: AsyncSession, *, tenant_id: int, product_id: int,
                     if_match: str, payload: dict[str, Any]) -> dict[str, Any]:
        product = await self.repository.get_product(session, tenant_id=tenant_id, product_id=product_id)
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        require_version(if_match, product["updated_at"])
        if payload.get("analysis_status") not in {None, "draft", "profile_pending", "ready", "archived"}:
            raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", "analysis_status无效", status_code=422)
        profile_id = None
        attributes = payload.get("attributes")
        if attributes is not None:
            profile_id = await self.repository.ensure_draft_profile(
                session, tenant_id=tenant_id, product_id=product_id)
            normalized = []
            for item in attributes:
                if item["source_type"] not in {"confirmed_structured","user_input","document","image","inferred"}:
                    raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", "属性来源类型无效", status_code=422)
                if item["confirmation_status"] not in {"unconfirmed","confirmed","conflicted","unknown"}:
                    raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", "属性确认状态无效", status_code=422)
                normalized.append({
                    "attribute_code": item["attribute_code"], "value": json.dumps(item["attribute_value"], ensure_ascii=False),
                    "unit": item.get("unit"), "source_type": item["source_type"],
                    "source_locator": json.dumps(item.get("source_locator"), ensure_ascii=False) if item.get("source_locator") else None,
                    "confidence": item["confidence"], "confirmation_status": item["confirmation_status"],
                })
            attributes = normalized
        row = await self.repository.update(session, tenant_id=tenant_id, product_id=product_id,
            name=payload.get("name"), description=payload.get("description"),
            analysis_status=payload.get("analysis_status"), profile_version_id=profile_id, attributes=attributes)
        row["resource_version"] = resource_version(row.pop("updated_at"))
        return row

    async def confirm(self, session: AsyncSession, *, tenant_id: int, user_id: int, product_id: int,
                      profile_version_id: int, codes: list[str], if_match: str,
                      idempotency_key: str, response_envelope) -> tuple[int, dict[str, Any]]:
        decision = await self.idempotency.begin(session, tenant_id=tenant_id, actor_user_id=user_id,
            route_code="API-PRD-07", http_method="POST", idempotency_key=idempotency_key,
            request_payload={"product_id": product_id, "profile_version_id": profile_version_id,
                             "confirmed_attribute_codes": sorted(set(codes)), "if_match": if_match})
        if decision.action == "replay":
            return int(decision.response_status), dict(decision.response_body or {})
        product = await self.repository.get_product(session, tenant_id=tenant_id, product_id=product_id)
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        require_version(if_match, product["updated_at"])
        profile = await self.repository.get_profile(session, tenant_id=tenant_id, product_id=product_id,
                                                    profile_version_id=profile_version_id)
        if profile is None:
            raise BusinessError("PRODUCT_PROFILE_NOT_FOUND", "产品画像版本不存在", status_code=404)
        try:
            row = await self.repository.confirm_profile(session, tenant_id=tenant_id, user_id=user_id,
                product_id=product_id, profile_version_id=profile_version_id, codes=codes)
        except ValueError as exc:
            code = "PRODUCT_PROFILE_CONFLICTED" if str(exc) == "conflicted" else "PRODUCT_PROFILE_INCOMPLETE"
            raise BusinessError(code, "产品画像存在冲突或未完成确认", status_code=422) from exc
        if row is None:
            raise BusinessError("PRODUCT_PROFILE_IMMUTABLE", "已确认画像不可修改", status_code=422)
        envelope = response_envelope(row)
        await self.idempotency.finish(session, tenant_id=tenant_id, record_id=decision.record_id,
            response_status=200, response_body=envelope, resource_type="product_profile_versions",
            resource_public_id=str(profile_version_id))
        return 200, envelope
