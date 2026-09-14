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
from .quota import ModelQuotaExhausted, is_quota_exhausted


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
        fallback_base_url: str | None = None,
        fallback_api_key: str | None = None,
        fallback_chat_path: str | None = None,
        fallback_model_id: str | None = None,
        fallback_provider: str = "deepseek",
    ) -> None:
        if not api_key:
            raise ValueError("ALIYUN_MODEL_ROUTER_API_KEY is required")
        self._url = f"{base_url.rstrip('/')}/{chat_path.lstrip('/')}"
        self._api_key = api_key
        self._trace_writer = trace_writer
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._fallback_url = None
        if fallback_base_url and fallback_api_key:
            self._fallback_url = (
                f"{fallback_base_url.rstrip('/')}/{(fallback_chat_path or chat_path).lstrip('/')}"
            )
        self._fallback_api_key = fallback_api_key
        self._fallback_model_id = fallback_model_id
        self._fallback_provider = fallback_provider

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
        targets = [(self._url, self._api_key, model_id, "aliyun_token_plan")]
        if self._fallback_url and self._fallback_api_key:
            targets.append((
                self._fallback_url,
                self._fallback_api_key,
                self._fallback_model_id or model_id,
                self._fallback_provider,
            ))

        for target_index, (url, api_key, active_model, provider) in enumerate(targets):
            for retry_count in range(max_retries + 1):
                status = "failed"
                try:
                    response = await self._client.post(
                        url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": active_model,
                            "messages": messages,
                            "response_format": {"type": "json_object"},
                        },
                        timeout=timeout_seconds,
                    )
                    body_text = response.text
                    if is_quota_exhausted(response.status_code, body_text):
                        status = "quota_exhausted"
                        raise ModelQuotaExhausted("Model Router quota exhausted")
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
                    if not isinstance(content, str):
                        raise TypeError("Model response content must be a string")
                    content = self._strip_json_fence(content)
                    parsed = adapter.validate_json(content)
                    usage = body.get("usage", {})
                    await self._trace_writer(ModelCallTrace(
                        provider=provider,
                        model_id=body.get("model", active_model),
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
                except ModelQuotaExhausted as exc:
                    last_error = exc
                    await self._write_failure_trace(
                        active_model, task_type, input_hash, prompt_version,
                        output_schema_version, started, retry_count, status,
                        provider=provider,
                    )
                    break
                except httpx.TimeoutException as exc:
                    status = "timeout"
                    last_error = ModelTimeoutError(str(exc))
                except (ValidationError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    status = "schema_failed"
                    last_error = ModelSchemaError(str(exc))
                except (ModelRateLimitedError, TransientInfrastructureError) as exc:
                    last_error = exc
                except ContentBlockedError:
                    await self._write_failure_trace(
                        active_model, task_type, input_hash, prompt_version,
                        output_schema_version, started, retry_count, status,
                        provider=provider,
                    )
                    raise
                except httpx.HTTPError as exc:
                    last_error = TransientInfrastructureError(str(exc))

                if retry_count < max_retries:
                    await asyncio.sleep(min(2 ** retry_count, 4))
                    continue
                await self._write_failure_trace(
                    active_model, task_type, input_hash, prompt_version,
                    output_schema_version, started, retry_count, status,
                    provider=provider,
                )
            else:
                break
            if not isinstance(last_error, ModelQuotaExhausted) or target_index == len(targets) - 1:
                break
        assert last_error is not None
        raise last_error

    @staticmethod
    def _strip_json_fence(content: str) -> str:
        """Accept the occasional fenced JSON response from compatible chat models."""
        value = content.strip()
        if value.startswith("```") and value.endswith("```"):
            first_newline = value.find("\n")
            if first_newline != -1:
                value = value[first_newline + 1:-3].strip()
        return value

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
        *,
        provider: str = "aliyun_token_plan",
    ) -> None:
        await self._trace_writer(ModelCallTrace(
            provider=provider, model_id=model_id,
            task_type=task_type, input_hash=input_hash,
            prompt_version=prompt_version, output_schema_version=schema_version,
            input_tokens=None, output_tokens=None,
            latency_ms=int((time.monotonic() - started) * 1000),
            retry_count=retry_count, schema_valid=False, status=status,
        ))
