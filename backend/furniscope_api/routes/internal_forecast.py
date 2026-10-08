"""Platform-only forecast artifact registration and tenant deployment."""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from ..dependencies import AdminDatabaseSession
from ..errors import BusinessError
from ..repositories.forecast_repository import ForecastRepository
from ..schemas import SuccessEnvelope
from ..services.forecast_catalog import ForecastCatalog
from ..services.forecast_publication import ForecastPublication
from ..services.forecast_runtime import coerce_training_date
from .internal_model import _authorize

router = APIRouter(prefix="/internal/v1/forecast", tags=["Internal Forecast"],
                   include_in_schema=False)


class ForecastDeploymentCreate(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    state_uri: str = Field(default="server-managed://default", min_length=1, max_length=1000)
    model_scope: Literal["shared_base", "tenant_private"] = "shared_base"
    route_policy: dict = Field(default_factory=lambda: {
        "standard": "v4", "cold_start": ["baseline", "reference_sku"]})
    model_config = ConfigDict(extra="forbid")


@router.post("/tenants/{tenant_id}/deploy", operation_id="API-IFRC-01")
async def deploy_forecast_model(tenant_id: int, body: ForecastDeploymentCreate,
                                request: Request, session: AdminDatabaseSession,
                                token: str | None = Header(default=None, alias="X-Internal-Token")):
    _authorize(request, token)
    tenant_exists = (await session.execute(text(
        "SELECT EXISTS(SELECT 1 FROM furniscope.tenants WHERE id=:tenant)"),
        {"tenant": tenant_id})).scalar_one()
    if not tenant_exists:
        raise BusinessError("TENANT_NOT_FOUND", "租户不存在", status_code=404)
    owner_tenant_id = tenant_id if body.model_scope == "tenant_private" else None
    candidate = {
        "model_uuid": "candidate", "state_checksum": "pending", "state_uri": body.state_uri,
        "version": body.version, "model_scope": body.model_scope,
        "owner_tenant_id": owner_tenant_id,
    }
    try:
        runtime = request.app.state.forecast_runtime.resolve(
            tenant_id=tenant_id, deployment=candidate)
        metadata = runtime.metadata()
        if not metadata.get("ready"):
            raise RuntimeError(metadata.get("error", "model is not ready"))
        sku_rows = await runtime.list_skus(None, 5000)
        candidate["state_checksum"] = metadata["state_checksum"]
        request.app.state.forecast_runtime.remember_verified(
            tenant_id=tenant_id, deployment=candidate, runtime=runtime)
    except RuntimeError as exc:
        raise BusinessError("FORECAST_ARTIFACT_INVALID", str(exc), status_code=422) from exc
    metrics = {key: metadata.get(key) for key in (
        "sku_count", "granularities", "trained_at", "reported_backtest", "data_quality")}
    data_through = coerce_training_date(metadata.get("data_through"))
    try:
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                              {"key": 7_040_000 + tenant_id})
        current = await ForecastRepository().active_deployment(session, tenant_id=tenant_id)
        catalog_service = ForecastCatalog()
        preview = await catalog_service.preview(session, tenant_id, sku_rows, context=body.state_uri)
        if body.model_scope == "tenant_private" or preview["conflicts"]:
            catalog_service.require_valid(preview)
        model = (await session.execute(text("""INSERT INTO furniscope.forecast_models
            (model_code,owner_tenant_id,model_scope,version,engine,state_uri,state_checksum,
             status,training_data_through,metrics)
            VALUES('sales_forecast',:owner,:scope,:version,:engine,:uri,:checksum,'active',
                   CAST(:data_through AS date),CAST(:metrics AS jsonb))
            ON CONFLICT(model_code,version,state_checksum) DO UPDATE SET updated_at=now()
            RETURNING id,model_uuid::text,version,engine,state_checksum,owner_tenant_id,model_scope"""), {
                "owner": owner_tenant_id, "scope": body.model_scope, "version": body.version,
                "engine": metadata["engine"], "uri": body.state_uri,
                "checksum": metadata["state_checksum"], "data_through": data_through,
                "metrics": json.dumps(metrics),
            })).mappings().one()
        if model["owner_tenant_id"] != owner_tenant_id or model["model_scope"] != body.model_scope:
            raise BusinessError("FORECAST_MODEL_SCOPE_CONFLICT", "同版本模型的归属范围冲突", status_code=409)
        deployment = await ForecastPublication().activate(
            session, tenant_id=tenant_id, model_id=model["id"], user_id=None,
            catalog=preview["items"],
            expected_deployment_id=current["deployment_id"] if current else None,
            source="platform_deployment")
        await session.execute(text("""INSERT INTO furniscope.audit_logs
            (tenant_id,action_code,resource_type,resource_id,request_id,after_snapshot)
            VALUES(:tenant,'platform.forecast.deploy','forecast_model_deployment',:resource,
                   :request_id,CAST(:snapshot AS jsonb))"""), {
                "tenant": tenant_id, "resource": model["id"],
                "request_id": request.state.request_id,
                "snapshot": json.dumps({"model_uuid": model["model_uuid"],
                                         "version": model["version"],
                                         "model_scope": model["model_scope"],
                                         "state_checksum": model["state_checksum"]}),
            })
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return SuccessEnvelope(data={
        "tenant_id": tenant_id, "deployment_uuid": deployment["deployment_uuid"],
        "status": "active", "model_uuid": model["model_uuid"],
        "version": model["version"], "model_scope": model["model_scope"],
        "state_checksum": model["state_checksum"],
        "catalog_skus": deployment["catalog_skus"],
        "catalog_pairs": deployment["catalog_pairs"],
    }, request_id=request.state.request_id)
