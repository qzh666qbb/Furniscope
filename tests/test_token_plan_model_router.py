from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from backend.furniscope_agent.model_router import ModelRouterClient
from backend.furniscope_api.config import ApiSettings


class Output(BaseModel):
    status: str


def test_token_plan_openai_compatible_request_and_trace() -> None:
    async def scenario() -> None:
        traces = []

        async def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == (
                "https://token-plan.cn-beijing.maas.aliyuncs.com/"
                "compatible-mode/v1/chat/completions"
            )
            assert request.headers["Authorization"] == "Bearer test-only-key"
            body = json.loads(request.content)
            assert body["model"] == "qwen3.7-plus"
            assert body["response_format"] == {"type": "json_object"}
            return httpx.Response(200, json={
                "model": "qwen3.7-plus",
                "choices": [{"message": {"content": "```json\n{\"status\":\"ok\"}\n```"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
            })

        async def write_trace(trace) -> None:
            traces.append(trace)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = ModelRouterClient(
                base_url="https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
                api_key="test-only-key",
                chat_path="/chat/completions",
                trace_writer=write_trace,
                client=http_client,
            )
            result = await client.structured_generate(
                model_id="qwen3.7-plus", task_type="report",
                messages=[{"role": "user", "content": "return json"}],
                output_model=Output, prompt_version="test-v1",
                output_schema_version="test-output-v1", timeout_seconds=10,
                max_retries=0,
            )
        assert result.status == "ok"
        assert len(traces) == 1
        assert traces[0].provider == "aliyun_token_plan"
        assert traces[0].input_tokens == 12
        assert traces[0].output_tokens == 5
        assert traces[0].schema_valid is True

    asyncio.run(scenario())


def test_token_plan_demo_requires_secret() -> None:
    with pytest.raises(ValueError, match="ALIYUN_MODEL_ROUTER_API_KEY"):
        ApiSettings(
            _env_file=None,
            app_env="test",
            database_url="postgresql+asyncpg://test:test@localhost/test",
            analysis_worker_mode="token_plan_demo",
            aliyun_model_router_api_key="",
            deepseek_api_key="",
        )


def test_empty_model_router_secret_is_treated_as_missing() -> None:
    settings = ApiSettings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        analysis_worker_mode="external",
        analysis_tool_mode="external",
        aliyun_model_router_api_key="   ",
        deepseek_api_key="",
    )
    assert settings.aliyun_model_router_api_key is None
    assert settings.has_model_router_key() is False


def test_deepseek_key_alone_counts_as_configured() -> None:
    settings = ApiSettings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        analysis_worker_mode="external",
        analysis_tool_mode="external",
        aliyun_model_router_api_key="",
        deepseek_api_key="test-deepseek-key",
    )
    assert settings.has_model_router_key() is True
    assert settings.has_deepseek_key() is True
    assert settings.chat_provider_routes()[0]["provider"] == "deepseek"


def test_aliyun_quota_exhausted_falls_back_to_deepseek() -> None:
    from backend.furniscope_api.services.model_router_client import ServiceModelRouterClient

    settings = ApiSettings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        analysis_worker_mode="external",
        analysis_tool_mode="external",
        aliyun_model_router_api_key="aliyun-test-key",
        deepseek_api_key="deepseek-test-key",
        aliyun_model_router_max_retries=0,
    )

    async def scenario() -> None:
        hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            hosts.append(request.url.host or "")
            if "token-plan" in str(request.url):
                return httpx.Response(
                    403,
                    json={"error": {"code": "Arrearage", "message": "额度不足"}},
                )
            assert request.headers["Authorization"] == "Bearer deepseek-test-key"
            body = json.loads(request.content)
            assert body["model"] == "deepseek-chat"
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "{\"status\":\"ok\"}"}}],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = ServiceModelRouterClient(settings, client=http_client)
            result = await client.structured(
                messages=[{"role": "user", "content": "return json"}],
                output_type=Output,
            )
        assert result.status == "ok"
        assert client.last_chat_provider == "deepseek"
        assert "token-plan.cn-beijing.maas.aliyuncs.com" in hosts
        assert "api.deepseek.com" in hosts

    asyncio.run(scenario())


def test_event_gateway_is_the_default_without_embedding_claims() -> None:
    settings = ApiSettings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost/test",
    )
    assert settings.aliyun_model_router_chat_base_url == (
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    assert settings.aliyun_model_router_text_model == "qwen3.7-plus"
