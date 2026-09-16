"""Service-layer model gateway used by parsers and internal model endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import TypeAdapter

try:
    from furniscope_agent.quota import ModelQuotaExhausted, is_quota_exhausted
except ImportError:  # pytest from repo root
    from backend.furniscope_agent.quota import ModelQuotaExhausted, is_quota_exhausted

from ..config import ApiSettings

_MODEL_CALL_LIMITER = asyncio.Semaphore(4)


class ServiceModelRouterClient:
    def __init__(self, settings: ApiSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.client = client or httpx.AsyncClient()
        self.owns_client = client is None
        self.last_chat_provider: str | None = None
        self.last_chat_model: str | None = None
        self.last_input_tokens: int | None = None
        self.last_output_tokens: int | None = None

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    def _api_key(self) -> str:
        secret = self.settings.aliyun_model_router_api_key
        value = secret.get_secret_value().strip() if secret is not None else ""
        if not value:
            raise RuntimeError("MODEL_ROUTER_KEY_MISSING")
        return value

    def _chat_body(self, response: httpx.Response) -> str:
        try:
            return response.text
        except Exception:
            return ""

    async def structured(self, *, messages: list[dict[str, Any]], output_type,
                         model_id: str | None = None):
        routes = self.settings.chat_provider_routes()
        if not routes:
            raise RuntimeError("MODEL_ROUTER_KEY_MISSING")
        adapter = TypeAdapter(output_type)
        last_error: Exception | None = None
        for route in routes:
            try:
                return await self._structured_on_route(
                    route, messages=messages, adapter=adapter, model_id=model_id,
                )
            except ModelQuotaExhausted as exc:
                last_error = exc
                continue
        raise RuntimeError("MODEL_ROUTER_FAILED:ModelQuotaExhausted") from last_error

    async def stream_chat(self, *, messages: list[dict[str, Any]], model_id: str | None = None):
        routes = self.settings.chat_provider_routes()
        if not routes:
            raise RuntimeError("MODEL_ROUTER_KEY_MISSING")
        last_error: Exception | None = None
        for route in routes:
            yielded = False
            try:
                async for delta in self._stream_on_route(route, messages=messages, model_id=model_id):
                    yielded = True
                    yield delta
                if yielded:
                    return
            except ModelQuotaExhausted as exc:
                last_error = exc
                if yielded:
                    return
                continue
            except Exception as exc:
                last_error = exc
                if yielded:
                    return
                continue
        raise RuntimeError("MODEL_ROUTER_FAILED") from last_error

    async def _stream_on_route(self, route: dict[str, str], *, messages,
                               model_id: str | None, enable_thinking: bool = True):
        api_key = route["api_key"]
        url = f"{route['base_url'].rstrip('/')}/{route['chat_path'].lstrip('/')}"
        model = model_id or route["model_id"]
        timeout = httpx.Timeout(
            self.settings.aliyun_model_router_timeout_seconds,
            connect=10.0,
            read=self.settings.aliyun_model_router_timeout_seconds,
        )
        thinking_attempts = (True, False) if enable_thinking and "qwen" in str(model).lower() else (False,)
        last_error: Exception | None = None
        for use_thinking in thinking_attempts:
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": True,
            }
            if use_thinking:
                payload["enable_thinking"] = True
            async with _MODEL_CALL_LIMITER:
                try:
                    async with self.client.stream(
                        "POST", url,
                        headers={"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"},
                        json=payload,
                        timeout=timeout,
                    ) as response:
                        if response.status_code >= 400:
                            body_text = (await response.aread()).decode("utf-8", "ignore")
                            if is_quota_exhausted(response.status_code, body_text):
                                raise ModelQuotaExhausted(f"{route['provider']} quota exhausted")
                            if use_thinking and response.status_code == 400:
                                last_error = RuntimeError("MODEL_ROUTER_FAILED:HTTP400")
                                continue
                            raise RuntimeError(f"MODEL_ROUTER_FAILED:HTTP{response.status_code}")
                        async for line in response.aiter_lines():
                            if not line:
                                continue
                            raw = line[5:].strip() if line.startswith("data:") else ""
                            if not raw:
                                continue
                            if raw == "[DONE]":
                                return
                            try:
                                chunk = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            choices = chunk.get("choices") or []
                            if not choices:
                                usage = chunk.get("usage") or {}
                                if usage:
                                    self.last_chat_provider = route["provider"]
                                    self.last_chat_model = chunk.get("model") or model
                                    self.last_input_tokens = usage.get("prompt_tokens")
                                    self.last_output_tokens = usage.get("completion_tokens")
                                continue
                            delta = choices[0].get("delta") or {}
                            reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                            content = delta.get("content") or ""
                            if reasoning or content:
                                yield {"reasoning": reasoning, "content": content}
                        return
                except ModelQuotaExhausted:
                    raise
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                    last_error = RuntimeError(f"MODEL_ROUTER_FAILED:{type(exc).__name__}")
                    last_error.__cause__ = exc
                    if use_thinking:
                        continue
                    raise last_error from exc
        if last_error:
            raise last_error

    async def _structured_on_route(self, route: dict[str, str], *, messages, adapter,
                                  model_id: str | None):
        api_key = route["api_key"]
        url = f"{route['base_url'].rstrip('/')}/{route['chat_path'].lstrip('/')}"
        model = model_id or route["model_id"]
        last_error: Exception | None = None
        async with _MODEL_CALL_LIMITER:
            for attempt in range(self.settings.aliyun_model_router_max_retries + 1):
                try:
                    response = await self.client.post(
                        url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={
                            "model": model,
                            "messages": messages,
                            "response_format": {"type": "json_object"},
                        },
                        timeout=self.settings.aliyun_model_router_timeout_seconds,
                    )
                    body_text = self._chat_body(response)
                    if is_quota_exhausted(response.status_code, body_text):
                        raise ModelQuotaExhausted(
                            f"{route['provider']} quota exhausted"
                        )
                    response.raise_for_status()
                    payload = response.json()
                    content = payload["choices"][0]["message"]["content"]
                    if content.strip().startswith("```"):
                        content = content[content.find("\n") + 1:content.rfind("```")].strip()
                    parsed = adapter.validate_json(content)
                    usage = payload.get("usage") or {}
                    self.last_chat_provider = route["provider"]
                    self.last_chat_model = payload.get("model") or model
                    self.last_input_tokens = usage.get("prompt_tokens")
                    self.last_output_tokens = usage.get("completion_tokens")
                    return parsed
                except ModelQuotaExhausted:
                    raise
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError,
                        KeyError, ValueError, json.JSONDecodeError) as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and is_quota_exhausted(
                        exc.response.status_code, self._chat_body(exc.response),
                    ):
                        raise ModelQuotaExhausted(
                            f"{route['provider']} quota exhausted"
                        ) from exc
                    last_error = exc
                    if attempt < self.settings.aliyun_model_router_max_retries:
                        await asyncio.sleep(min(2 ** attempt, 4))
            raise RuntimeError(f"MODEL_ROUTER_FAILED:{type(last_error).__name__}") from last_error

    async def embeddings(self, texts: list[str], dimensions: int | None = None) -> dict[str, Any]:
        cleaned = [str(text).strip() for text in texts]
        if not cleaned or any(not text for text in cleaned):
            raise ValueError("Embedding input must contain 1-10 non-empty texts")
        dimensions = dimensions or self.settings.aliyun_model_router_embedding_dimensions
        if dimensions != self.settings.aliyun_model_router_embedding_dimensions:
            raise ValueError("Embedding dimensions must match the configured model dimensions")
        vectors: list[list[float]] = []
        total_tokens = 0
        model_name = self.settings.aliyun_model_router_embedding_model
        for offset in range(0, len(cleaned), 10):
            chunk = cleaned[offset:offset + 10]
            part = await self._embed_chunk(chunk, dimensions)
            vectors.extend(part["vectors"])
            total_tokens += int(part.get("total_tokens") or 0)
            model_name = part.get("model") or model_name
        return {
            "vectors": vectors,
            "dimensions": dimensions,
            "model": model_name,
            "total_tokens": total_tokens or None,
            "provider": "aliyun_bailian",
        }

    async def _embed_chunk(self, texts: list[str], dimensions: int) -> dict[str, Any]:
        body = await self._post_json(
            base_url=self.settings.aliyun_model_router_embedding_base_url,
            path=self.settings.aliyun_model_router_embedding_path,
            payload={
                "model": self.settings.aliyun_model_router_embedding_model,
                "input": texts,
                "dimensions": dimensions,
                "encoding_format": "float",
            },
        )
        rows = body.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise RuntimeError("MODEL_OUTPUT_SCHEMA_INVALID: embedding count mismatch")
        ordered: list[list[float] | None] = [None] * len(texts)
        for row in rows:
            index = row.get("index") if isinstance(row, dict) else None
            vector = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(index, int) or not 0 <= index < len(texts):
                raise RuntimeError("MODEL_OUTPUT_SCHEMA_INVALID: invalid embedding index")
            if not isinstance(vector, list) or len(vector) != dimensions:
                raise RuntimeError("MODEL_OUTPUT_SCHEMA_INVALID: invalid embedding dimensions")
            ordered[index] = [float(value) for value in vector]
        if any(vector is None for vector in ordered):
            raise RuntimeError("MODEL_OUTPUT_SCHEMA_INVALID: incomplete embedding response")
        return {
            "vectors": ordered,
            "model": body.get("model", self.settings.aliyun_model_router_embedding_model),
            "total_tokens": (body.get("usage") or {}).get("total_tokens"),
        }

    async def rerank(self, query: str, candidates: list[str], top_n: int | None = None) -> dict[str, Any]:
        if not query.strip() or not 1 <= len(candidates) <= 500:
            raise ValueError("Rerank input must contain a query and 1-500 candidates")
        top_n = top_n or len(candidates)
        if not 1 <= top_n <= len(candidates):
            raise ValueError("Rerank top_n must be within the candidate count")
        body = await self._post_json(
            base_url=self.settings.aliyun_model_router_rerank_base_url,
            path=self.settings.aliyun_model_router_rerank_path,
            payload={
                "model": self.settings.aliyun_model_router_rerank_model,
                "query": query,
                "documents": candidates,
                "top_n": top_n,
            },
        )
        rows = body.get("results")
        if not isinstance(rows, list):
            raise RuntimeError("MODEL_OUTPUT_SCHEMA_INVALID: rerank results missing")
        ranking = []
        for rank, row in enumerate(rows, start=1):
            index = row.get("index") if isinstance(row, dict) else None
            score = row.get("relevance_score") if isinstance(row, dict) else None
            if not isinstance(index, int) or not 0 <= index < len(candidates):
                raise RuntimeError("MODEL_OUTPUT_SCHEMA_INVALID: invalid rerank index")
            if not isinstance(score, (int, float)) or not 0 <= float(score) <= 1:
                raise RuntimeError("MODEL_OUTPUT_SCHEMA_INVALID: invalid rerank score")
            ranking.append({"index": index, "score": float(score), "rank": rank})
        return {
            "ranking": ranking,
            "model": body.get("model", self.settings.aliyun_model_router_rerank_model),
            "total_tokens": (body.get("usage") or {}).get("total_tokens"),
            "provider": "aliyun_bailian",
        }

    async def _post_json(self, *, base_url: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = self._api_key()
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        last_error: Exception | None = None
        async with _MODEL_CALL_LIMITER:
            for attempt in range(self.settings.aliyun_model_router_max_retries + 1):
                try:
                    response = await self.client.post(
                        url,
                        headers={"Authorization": f"Bearer {api_key}"},
                        json=payload,
                        timeout=self.settings.aliyun_model_router_timeout_seconds,
                    )
                    response.raise_for_status()
                    body = response.json()
                    if not isinstance(body, dict):
                        raise RuntimeError("MODEL_OUTPUT_SCHEMA_INVALID: response must be an object")
                    return body
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError,
                        ValueError, json.JSONDecodeError) as exc:
                    last_error = exc
                    if attempt < self.settings.aliyun_model_router_max_retries:
                        await asyncio.sleep(min(2 ** attempt, 4))
        raise RuntimeError(f"MODEL_ROUTER_FAILED:{type(last_error).__name__}") from last_error
