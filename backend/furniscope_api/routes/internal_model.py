"""Authenticated service-only model routing endpoints."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from ..errors import BusinessError
from ..schemas import SuccessEnvelope
from ..services.model_router_client import ServiceModelRouterClient

router = APIRouter(prefix="/internal/v1/model-router", tags=["Internal Model Router"],
                   include_in_schema=False)


class StructuredRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(min_length=1, max_length=50)
    output_schema: dict[str, Any] = Field(default_factory=dict, alias="schema")
    model_id: str | None = None
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EmbeddingRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=10)
    dimensions: int = Field(default=1024, ge=1024, le=1024)
    model_config = ConfigDict(extra="forbid")


class RerankRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    candidates: list[str] = Field(min_length=1, max_length=500)
    model_config = ConfigDict(extra="forbid")


def _authorize(request: Request, token: str | None) -> None:
    configured = request.app.state.settings.internal_service_token
    if configured is None or token is None or not hmac.compare_digest(token, configured.get_secret_value()):
        raise BusinessError("INTERNAL_AUTH_REQUIRED", "内部服务鉴权失败", status_code=401)


@router.post("/invoke", operation_id="API-MDL-01")
async def invoke(body: StructuredRequest, request: Request,
                 token: str | None = Header(default=None, alias="X-Internal-Token")):
    _authorize(request, token)
    client = ServiceModelRouterClient(request.app.state.settings)
    try:
        value = await client.structured(messages=body.messages, output_type=dict,
                                        model_id=body.model_id)
    except RuntimeError as exc:
        raise BusinessError("MODEL_ROUTER_UNAVAILABLE", str(exc), status_code=503) from exc
    finally:
        await client.close()
    required = body.output_schema.get("required", [])
    if not isinstance(value, dict) or any(key not in value for key in required):
        raise BusinessError("MODEL_SCHEMA_INVALID", "模型输出缺少必填字段", status_code=502)
    return SuccessEnvelope(data={"output": value}, request_id=request.state.request_id)


@router.post("/embeddings", operation_id="API-MDL-02")
async def embeddings(body: EmbeddingRequest, request: Request,
                     token: str | None = Header(default=None, alias="X-Internal-Token")):
    _authorize(request, token)
    client = ServiceModelRouterClient(request.app.state.settings)
    try:
        result = await client.embeddings(body.texts, body.dimensions)
    except (RuntimeError, ValueError) as exc:
        raise BusinessError("MODEL_ROUTER_UNAVAILABLE", str(exc), status_code=503) from exc
    finally:
        await client.close()
    return SuccessEnvelope(data=result,
                           request_id=request.state.request_id)


@router.post("/rerank", operation_id="API-MDL-03")
async def rerank(body: RerankRequest, request: Request,
                 token: str | None = Header(default=None, alias="X-Internal-Token")):
    _authorize(request, token)
    client = ServiceModelRouterClient(request.app.state.settings)
    try:
        result = await client.rerank(body.query, body.candidates)
    except (RuntimeError, ValueError) as exc:
        raise BusinessError("MODEL_ROUTER_UNAVAILABLE", str(exc), status_code=503) from exc
    finally:
        await client.close()
    return SuccessEnvelope(data=result,
                           request_id=request.state.request_id)
