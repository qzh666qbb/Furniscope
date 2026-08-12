"""Aliyun Model Router adapter with bounded retry and structured-output checks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from .errors import (
    ContentBlockedError,
    ModelRateLimitedError,
    ModelSchemaError,
    ModelTimeoutError,
    TransientInfrastructureError,
)


@dataclass(slots=True)
class ModelCallTrace:
    provider: str
    model_id: str
    task_type: str
    input_hash: str
    prompt_version: str | None
    output_schema_version: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    retry_count: int
    schema_valid: bool
    status: str


class ModelRouterClient:
    """OpenAI-compatible transport; exact route is runtime configuration."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        chat_path: str,
        trace_writer: Callable[[ModelCallTrace], Awaitable[None]],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("ALIYUN_MODEL_ROUTER_API_KEY is required")
        self._url = f"{base_url.rstrip('/')}/{chat_path.lstrip('/')}"
        self._api_key = api_key
        self._trace_writer = trace_writer
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def structured_generate(
        self,
        *,
        model_id: str,
        task_type: str,
        messages: list[dict[str, str]],
        output_model: type[BaseModel] | TypeAdapter[Any],
        prompt_version: str | None,
        output_schema_version: str,
        timeout_seconds: float,
        max_retries: int,
    ) -> Any:
        serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        input_hash = hashlib.sha256(serialized.encode()).hexdigest()
        adapter = output_model if isinstance(output_model, TypeAdapter) else TypeAdapter(output_model)
        started = time.monotonic()
        last_error: Exception | None = None

        for retry_count in range(max_retries + 1):
            status = "failed"
            try:
                response = await self._client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": model_id,
                        "messages": messages,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=timeout_seconds,
                )
                if response.status_code == 429:
                    status = "rate_limited"
                    raise ModelRateLimitedError("Model Router rate limited the request")
                if response.status_code in {408, 500, 502, 503, 504}:
                    status = "timeout" if response.status_code == 408 else "failed"
                    raise TransientInfrastructureError(f"Model Router HTTP {response.status_code}")
                if response.status_code in {400, 403}:
                    status = "content_blocked"
                    raise ContentBlockedError("Model Router rejected request content")
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
                parsed = adapter.validate_json(content)
                usage = body.get("usage", {})
                await self._trace_writer(ModelCallTrace(
                    provider="aliyun_model_router",
                    model_id=body.get("model", model_id),
                    task_type=task_type,
                    input_hash=input_hash,
                    prompt_version=prompt_version,
                    output_schema_version=output_schema_version,
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    retry_count=retry_count,
                    schema_valid=True,
                    status="succeeded",
                ))
                return parsed
            except httpx.TimeoutException as exc:
                status = "timeout"
                last_error = ModelTimeoutError(str(exc))
            except (ValidationError, json.JSONDecodeError, KeyError, IndexError) as exc:
                status = "schema_failed"
                last_error = ModelSchemaError(str(exc))
            except (ModelRateLimitedError, TransientInfrastructureError) as exc:
                last_error = exc
            except ContentBlockedError:
                await self._write_failure_trace(
                    model_id, task_type, input_hash, prompt_version,
                    output_schema_version, started, retry_count, status,
                )
                raise
            except httpx.HTTPError as exc:
                last_error = TransientInfrastructureError(str(exc))

            if retry_count < max_retries:
                await asyncio.sleep(min(2 ** retry_count, 4))
                continue
            await self._write_failure_trace(
                model_id, task_type, input_hash, prompt_version,
                output_schema_version, started, retry_count, status,
            )
        assert last_error is not None
        raise last_error

    async def _write_failure_trace(
        self,
        model_id: str,
        task_type: str,
        input_hash: str,
        prompt_version: str | None,
        schema_version: str,
        started: float,
        retry_count: int,
        status: str,
    ) -> None:
        await self._trace_writer(ModelCallTrace(
            provider="aliyun_model_router", model_id=model_id,
            task_type=task_type, input_hash=input_hash,
            prompt_version=prompt_version, output_schema_version=schema_version,
            input_tokens=None, output_tokens=None,
            latency_ms=int((time.monotonic() - started) * 1000),
            retry_count=retry_count, schema_valid=False, status=status,
        ))
