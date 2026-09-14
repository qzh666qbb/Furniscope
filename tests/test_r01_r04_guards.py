from __future__ import annotations

from pathlib import Path
import json
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.config import ApiSettings
from furniscope_api.errors import BusinessError
from furniscope_api.schemas.workspaces import WorkspaceMessageCreateRequest
from furniscope_api.services.authorized_signals import AuthorizedSignalService, finalize_collect_run
from furniscope_api.services.competitor_tracking import CompetitorTrackingService
from furniscope_api.services.parse_service import ParseService, _parse_error_code


def _settings(**overrides) -> ApiSettings:
    payload = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://test:test@localhost/test",
        "analysis_worker_mode": "external",
        "analysis_tool_mode": "external",
        "product_parse_mode": "model",
        "aliyun_model_router_api_key": "",
        "deepseek_api_key": "",
    }
    payload.update(overrides)
    return ApiSettings(_env_file=None, **payload)


def test_model_parse_rejects_empty_key_before_job() -> None:
    settings = _settings(aliyun_model_router_api_key="   ")
    assert settings.has_model_router_key() is False
    with pytest.raises(BusinessError) as error:
        ParseService(settings).require_model_parse_ready()
    assert error.value.code == "MODEL_ROUTER_KEY_MISSING"
    assert error.value.status_code == 503


def test_model_parse_allows_configured_key() -> None:
    settings = _settings(aliyun_model_router_api_key="test-model-router-key")
    assert settings.has_model_router_key() is True
    ParseService(settings).require_model_parse_ready()


def test_model_parse_allows_deepseek_fallback_key() -> None:
    settings = _settings(aliyun_model_router_api_key="", deepseek_api_key="test-deepseek-key")
    assert settings.has_model_router_key() is True
    ParseService(settings).require_model_parse_ready()


def test_demo_parse_skips_model_key_guard() -> None:
    ParseService(_settings(product_parse_mode="demo", aliyun_model_router_api_key="")).require_model_parse_ready()


def test_parse_error_code_maps_missing_key_runtime_error() -> None:
    assert _parse_error_code(RuntimeError("MODEL_ROUTER_KEY_MISSING")) == "MODEL_ROUTER_KEY_MISSING"
    assert _parse_error_code(RuntimeError("other")) == "DOCUMENT_SCHEMA_INVALID"


def test_workspace_message_kind_context_is_accepted() -> None:
    payload = WorkspaceMessageCreateRequest(
        role="assistant",
        content="已选择产品 Compact Sofa（SKU-1）。",
        message_kind="context",
    )
    assert payload.message_kind == "context"


def test_workspace_message_kind_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        WorkspaceMessageCreateRequest(role="user", content="hello", message_kind="system_prompt")


def test_own_listing_requires_exact_sku_or_asin() -> None:
    service = CompetitorTrackingService()
    catalog = [{"sku": "HF-A0396-1", "name": "Compact Sofa"}]
    assert service._listing_is_own(catalog, "HF-A0396-1", "CSV Compact Sofa") is True
    assert service._listing_is_own(catalog, "hf-a0396-1", "other title") is True
    assert service._listing_is_own(catalog, "CSV-SOFA-002", "CSV Compact Sofa") is False
    assert service._listing_is_own(catalog, "B0OWN12345", "Includes HF-A0396-1 in the title") is False
    assert service._listing_is_own(catalog, "", "Compact Sofa") is False


def test_zero_result_collect_is_not_succeeded() -> None:
    status, error = finalize_collect_run(
        status="succeeded", fetched=0, inserted=0, listing_asins=[], error_summary=None,
    )
    assert status == "failed"
    assert "零结果" in (error or "")


def test_empty_asin_payload_is_not_succeeded() -> None:
    status, error = finalize_collect_run(
        status="succeeded", fetched=2, inserted=0, listing_asins=[], error_summary=None,
    )
    assert status == "failed"
    assert error


def test_collect_with_written_listing_stays_succeeded() -> None:
    status, error = finalize_collect_run(
        status="succeeded", fetched=1, inserted=1, listing_asins=["B0ABC12345"], error_summary=None,
    )
    assert status == "succeeded"
    assert error is None


def test_failed_collect_status_is_preserved() -> None:
    status, error = finalize_collect_run(
        status="failed", fetched=0, inserted=0, listing_asins=[], error_summary="SOURCE_NO_RECORDS",
    )
    assert status == "failed"
    assert error == "SOURCE_NO_RECORDS"


def test_authorized_pack_url_reads_hefeng_file() -> None:
    service = AuthorizedSignalService(_settings())
    url = service._validate_source_url("pack://authorized/HeFeng-AUTH-AMZ-US-SOFA-2026Q3")
    assert url == "pack://authorized/HeFeng-AUTH-AMZ-US-SOFA-2026Q3"
    payload = json.loads(service._read_url(url, None))
    assert payload["listings"][0]["platform_listing_id"] == "SYN-HF-001"
    assert payload["listings"][0]["sale_price"] == 129.0


def test_authorized_pack_url_rejects_unknown_and_escape() -> None:
    service = AuthorizedSignalService(_settings())
    with pytest.raises(BusinessError) as missing:
        service._validate_source_url("pack://authorized/does-not-exist")
    assert missing.value.code == "SOURCE_PACK_NOT_FOUND"
    with pytest.raises(BusinessError) as escaped:
        service._validate_source_url("pack://authorized/../secrets")
    assert escaped.value.code == "SOURCE_URL_INVALID"

