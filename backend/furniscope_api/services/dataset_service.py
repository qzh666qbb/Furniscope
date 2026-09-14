"""Market dataset creation with authorization and mapping validation."""

from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import BusinessError
from ..repositories.dataset_repository import DatasetRepository
from .audit_service import AuditService
from .idempotency_service import IdempotencyService


class DatasetService:
    def __init__(self) -> None:
        self.repository = DatasetRepository()
        self.idempotency = IdempotencyService()
        self.audit = AuditService()

    async def create(self, session: AsyncSession, *, tenant_id: int, user_id: int,
                     idempotency_key: str, payload: dict[str, Any], response_envelope,
                     request_id: str) -> tuple[int, dict[str, Any]]:
        if not payload["category_code"] or payload["data_end_date"] > date.today() or (
            payload.get("data_start_date") and payload["data_start_date"] > payload["data_end_date"]):
            raise BusinessError("DATASET_SCOPE_INVALID", "数据集品类或时间范围无效", status_code=422)
        if payload["source_type"] not in {"enterprise_export","licensed_provider","public_authorized"}:
            raise BusinessError("DATASET_SCOPE_INVALID", "数据来源类型无效", status_code=422)
        if not payload.get("authorization_reference"):
            raise BusinessError("DATASET_AUTHORIZATION_REQUIRED", "市场数据集必须提供授权依据", status_code=422)
        for mapping in payload["field_mapping"]:
            if mapping["mapping_status"] not in {"mapped","ignored","pending"} or mapping.get("transform_rule") not in {None,"identity"}:
                raise BusinessError("FIELD_MAPPING_INVALID", "字段映射或转换规则不在P0安全白名单", status_code=422)
        hash_payload = payload | {k: v.isoformat() for k, v in payload.items() if isinstance(v, date)}
        decision = await self.idempotency.begin(session, tenant_id=tenant_id, actor_user_id=user_id,
            route_code="API-DAT-01", http_method="POST", idempotency_key=idempotency_key,
            request_payload=hash_payload)
        if decision.action == "replay":
            return int(decision.response_status), dict(decision.response_body or {})
        row = await self.repository.create(session, tenant_id=tenant_id, user_id=user_id, payload=payload)
        await self.audit.record(
            session, tenant_id=tenant_id, actor_user_id=user_id,
            action_code="dataset.create", resource_type="market_dataset",
            resource_id=row["dataset_id"], request_id=request_id,
            after={"name": row["name"], "platform": row["platform"],
                   "market_country": row["market_country"], "status": row["status"]},
        )
        envelope = response_envelope(row)
        await self.idempotency.finish(session, tenant_id=tenant_id, record_id=decision.record_id,
            response_status=201, response_body=envelope, resource_type="market_datasets",
            resource_public_id=str(row["dataset_id"]))
        return 201, envelope
