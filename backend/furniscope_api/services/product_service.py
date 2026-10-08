"""Product use cases with durable HTTP idempotency."""

from typing import Any
import json
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from furniscope_agent.product_facts import FACT_GROUPS, VOCABULARY_VERSION, fact_suggestions, validate_codes

from ..errors import BusinessError
from ..repositories.product_repository import ProductRepository
from .audit_service import AuditService
from .idempotency_service import IdempotencyService
from .resource_version import require_version, resource_version


PRODUCT_CATEGORIES = {"sofa", "chair", "table", "bed", "storage", "other"}


class ProductService:
    def __init__(self) -> None:
        self.repository = ProductRepository()
        self.idempotency = IdempotencyService()
        self.audit = AuditService()

    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     idempotency_key: str, payload: dict[str, Any], response_envelope,
                     error_envelope, request_id: str = "") -> tuple[int, dict[str, Any]]:
        if payload["category_code"] not in PRODUCT_CATEGORIES:
            raise BusinessError("PRODUCT_CATEGORY_INVALID", "品类代码不在受控词表中", status_code=422)
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
        await self.audit.record(
            session, tenant_id=tenant_id, actor_user_id=user_id,
            action_code="product.create", resource_type="product",
            resource_id=row["product_id"], request_id=request_id,
            after={key: row[key] for key in (
                "product_id", "sku", "name", "category_code", "lifecycle_status"
            )},
        )
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
            "profile_version_id": None if profile is None else profile["profile_version_id"],
            "profile_status": None if profile is None else profile["status"],
            "completeness_score": 0 if profile is None else profile["completeness_score"],
            "source_summary": {} if profile is None else profile["source_summary"],
            "attributes": attributes,
            "fact_suggestions": fact_suggestions(attributes),
            "resource_version": version,
        }

    async def update(self, session: AsyncSession, *, tenant_id: int, product_id: int,
                     user_id: int, if_match: str, payload: dict[str, Any],
                     request_id: str = "") -> dict[str, Any]:
        product = await self.repository.get_product(session, tenant_id=tenant_id, product_id=product_id, for_update=True)
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        require_version(if_match, product["updated_at"])
        if payload.get("category_code") not in {None, *PRODUCT_CATEGORIES}:
            raise BusinessError("PRODUCT_CATEGORY_INVALID", "品类代码不在受控词表中", status_code=422)
        if payload.get("analysis_status") not in {None, "draft", "profile_pending", "ready", "archived"}:
            raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", "analysis_status无效", status_code=422)
        profile_id = None
        attributes = payload.get("attributes")
        if payload.get("analysis_status") == "ready":
            profile = await self.repository.get_profile(session, tenant_id=tenant_id,
                product_id=product_id, profile_version_id=None)
            if attributes is not None or not profile or profile["status"] != "confirmed":
                raise BusinessError("PRODUCT_PROFILE_INCOMPLETE", "请先确认当前产品画像", status_code=422)
        if attributes is not None:
            codes = [item["attribute_code"] for item in attributes]
            if len(codes) != len(set(codes)):
                raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", "属性代码不能重复", status_code=422)
            profile_id = await self.repository.ensure_draft_profile(
                session, tenant_id=tenant_id, product_id=product_id)
            normalized = []
            for item in attributes:
                if item["source_type"] not in {"confirmed_structured","user_input","document","image","inferred"}:
                    raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", "属性来源类型无效", status_code=422)
                if item["confirmation_status"] not in {"unconfirmed","confirmed","conflicted","unknown"}:
                    raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", "属性确认状态无效", status_code=422)
                if item["attribute_code"] in FACT_GROUPS:
                    try:
                        validate_codes(item["attribute_code"], item["attribute_value"])
                    except ValueError as exc:
                        raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", str(exc), status_code=422) from exc
                locator = dict(item.get("source_locator") or {})
                # Review identity is assigned by the server, never accepted from a client.
                locator.pop("confirmed_by", None)
                locator.pop("confirmed_at", None)
                if item["confirmation_status"] == "confirmed":
                    if item["source_type"] not in {"user_input", "confirmed_structured"}:
                        raise BusinessError("PRODUCT_REVIEW_REQUIRED", "提取结果需通过画像确认后生效", status_code=422)
                    locator.update(confirmed_by=user_id, confirmed_at=datetime.now(timezone.utc).isoformat())
                    if item["attribute_code"] in FACT_GROUPS:
                        locator["vocabulary_version"] = VOCABULARY_VERSION
                normalized.append({
                    "attribute_code": item["attribute_code"], "value": json.dumps(item["attribute_value"], ensure_ascii=False),
                    "unit": item.get("unit"), "source_type": item["source_type"],
                    "source_locator": json.dumps(locator, ensure_ascii=False),
                    "confidence": item["confidence"], "confirmation_status": item["confirmation_status"],
                })
            attributes = normalized
        analysis_status = "profile_pending" if attributes is not None else payload.get("analysis_status")
        if payload.get("category_code") not in {None, product["category_code"]}:
            analysis_status = "draft"
        if payload.get("lifecycle_status") == "discontinued":
            analysis_status = "archived"
        try:
            row = await self.repository.update(
                session, tenant_id=tenant_id, product_id=product_id,
                sku=payload.get("sku"), name=payload.get("name"),
                category_code=payload.get("category_code"),
                lifecycle_status=payload.get("lifecycle_status"),
                description=payload.get("description"),
                description_is_set="description" in payload,
                analysis_status=analysis_status,
                profile_version_id=profile_id, attributes=attributes,
            )
            if payload.get("sku") and payload["sku"] != product["sku"]:
                await self.repository.update_sku_references(
                    session, tenant_id=tenant_id, old_sku=product["sku"],
                    new_sku=payload["sku"],
                )
        except IntegrityError as exc:
            raise BusinessError(
                "PRODUCT_SKU_CONFLICT",
                "SKU 与当前租户内已有产品或预测目录冲突",
                status_code=409,
            ) from exc
        before = {
            key: product.get(key) for key in (
                "product_id", "sku", "name", "category_code",
                "lifecycle_status", "description", "analysis_status",
            )
        }
        after = {
            key: row.get(key) for key in (
                "product_id", "sku", "name", "category_code",
                "lifecycle_status", "description", "analysis_status",
            )
        }
        await self.audit.record(
            session, tenant_id=tenant_id, actor_user_id=user_id,
            action_code="product.update", resource_type="product",
            resource_id=product_id, request_id=request_id, before=before, after=after,
        )
        row["resource_version"] = resource_version(row.pop("updated_at"))
        return row

    async def archive(
        self, session: AsyncSession, *, tenant_id: int, user_id: int,
        product_id: int, if_match: str, request_id: str = ""
    ) -> dict[str, Any]:
        product = await self.repository.get_product(
            session, tenant_id=tenant_id, product_id=product_id, for_update=True
        )
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        require_version(if_match, product["updated_at"])
        row = await self.repository.archive(
            session, tenant_id=tenant_id, product_id=product_id
        )
        if row is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或已归档", status_code=404)
        await self.audit.record(
            session, tenant_id=tenant_id, actor_user_id=user_id,
            action_code="product.archive", resource_type="product",
            resource_id=product_id, request_id=request_id,
            before={"sku": product["sku"], "analysis_status": product["analysis_status"],
                    "lifecycle_status": product["lifecycle_status"]},
            after={"sku": row["sku"], "analysis_status": row["analysis_status"],
                   "lifecycle_status": row["lifecycle_status"],
                   "archived_at": row["archived_at"]},
        )
        return row

    async def relations(
        self, session: AsyncSession, *, tenant_id: int, product_id: int
    ) -> dict[str, Any]:
        product = await self.repository.get_product(
            session, tenant_id=tenant_id, product_id=product_id
        )
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        return {
            "product_id": product_id,
            "items": await self.repository.relations(
                session, tenant_id=tenant_id, product_id=product_id
            ),
        }

    async def replace_relations(
        self, session: AsyncSession, *, tenant_id: int, user_id: int,
        product_id: int, items: list[dict[str, Any]], request_id: str = ""
    ) -> dict[str, Any]:
        product = await self.repository.get_product(
            session, tenant_id=tenant_id, product_id=product_id, for_update=True
        )
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        identities = {
            (item["group_type"], item["group_code"], item["member_role"])
            for item in items
        }
        if len(identities) != len(items):
            raise BusinessError("PRODUCT_RELATION_DUPLICATE", "产品关系不能重复", status_code=422)
        allowed_roles = {
            "spu": {"parent", "variant"},
            "variant": {"parent", "variant"},
            "bundle": {"parent", "item"},
            "bom": {"parent", "component"},
        }
        if any(item["member_role"] not in allowed_roles[item["group_type"]] for item in items):
            raise BusinessError(
                "PRODUCT_RELATION_INVALID", "关系角色与 SPU、套装或 BOM 类型不匹配",
                status_code=422,
            )
        before = await self.repository.relations(
            session, tenant_id=tenant_id, product_id=product_id
        )
        rows = await self.repository.replace_relations(
            session, tenant_id=tenant_id, user_id=user_id,
            product_id=product_id, items=items,
        )
        await self.audit.record(
            session, tenant_id=tenant_id, actor_user_id=user_id,
            action_code="product.relations.update", resource_type="product",
            resource_id=product_id, request_id=request_id,
            before={"items": before}, after={"items": rows},
        )
        return {"product_id": product_id, "items": rows}

    async def inventory_summary(
        self, session: AsyncSession, *, tenant_id: int, product_id: int
    ) -> dict[str, Any]:
        product = await self.repository.get_product(
            session, tenant_id=tenant_id, product_id=product_id
        )
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        return await self.repository.inventory_summary(
            session, tenant_id=tenant_id, product_id=product_id, sku=product["sku"]
        )

    async def confirm(self, session: AsyncSession, *, tenant_id: int, user_id: int, product_id: int,
                      profile_version_id: int, codes: list[str], if_match: str,
                      idempotency_key: str, response_envelope) -> tuple[int, dict[str, Any]]:
        decision = await self.idempotency.begin(session, tenant_id=tenant_id, actor_user_id=user_id,
            route_code="API-PRD-07", http_method="POST", idempotency_key=idempotency_key,
            request_payload={"product_id": product_id, "profile_version_id": profile_version_id,
                             "confirmed_attribute_codes": sorted(set(codes)), "if_match": if_match})
        if decision.action == "replay":
            return int(decision.response_status), dict(decision.response_body or {})
        product = await self.repository.get_product(session, tenant_id=tenant_id, product_id=product_id, for_update=True)
        if product is None:
            raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在或不可访问", status_code=404)
        require_version(if_match, product["updated_at"])
        profile = await self.repository.get_profile(session, tenant_id=tenant_id, product_id=product_id,
                                                    profile_version_id=profile_version_id)
        if profile is None:
            raise BusinessError("PRODUCT_PROFILE_NOT_FOUND", "产品画像版本不存在", status_code=404)
        if product["current_profile_version_id"] != profile_version_id:
            raise BusinessError("PRODUCT_PROFILE_STALE", "请确认最新画像，历史版本不可设为当前版本", status_code=409)
        attributes = await self.repository.attributes(session, tenant_id=tenant_id, profile_version_id=profile_version_id)
        if any(item["confirmation_status"] == "conflicted" for item in attributes):
            raise BusinessError("PRODUCT_PROFILE_CONFLICTED", "请先逐项修正冲突，再确认画像", status_code=422)
        if set(codes) - {item["attribute_code"] for item in attributes}:
            raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", "确认列表包含不存在的属性", status_code=422)
        for item in attributes:
            if item["attribute_code"] in FACT_GROUPS:
                try:
                    validate_codes(item["attribute_code"], item["attribute_value"])
                except ValueError as exc:
                    raise BusinessError("PRODUCT_ATTRIBUTE_INVALID", str(exc), status_code=422) from exc
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
