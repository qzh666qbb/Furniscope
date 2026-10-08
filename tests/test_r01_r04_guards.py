from __future__ import annotations

from pathlib import Path
import json
import sys
import types

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.config import ApiSettings
from furniscope_api.errors import BusinessError
from furniscope_api.schemas.workspaces import WorkspaceMessageCreateRequest
from furniscope_api.services.authorized_signals import (
    AuthorizedSignalService,
    _matches_policy_category,
    finalize_collect_run,
)
from furniscope_api.services.competitor_tracking import CompetitorTrackingService
from furniscope_api.services.parse_service import ParseService, _parse_error_code
from furniscope_api.services.demo_storage import LocalObjectStorage, S3ObjectStorage


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


def test_local_object_storage_enforces_tenant_prefix(tmp_path) -> None:
    storage = LocalObjectStorage(_settings(demo_storage_root=str(tmp_path)))
    key, digest = storage.store(
        tenant_id=7,
        filename="sales.csv",
        mime_type="text/csv",
        content=b"date,sales\n2026-10-01,3\n",
    )
    assert key.startswith("7/")
    assert len(digest) == 64
    assert storage.read(tenant_id=7, key=key).startswith(b"date,sales")
    with pytest.raises(BusinessError, match="不属于本企业"):
        storage.read(tenant_id=8, key=key)


def test_s3_storage_requires_explicit_credentials() -> None:
    with pytest.raises(ValueError, match="OBJECT_STORAGE_ENDPOINT"):
        _settings(storage_backend="s3")


def test_production_rejects_local_or_insecure_object_storage() -> None:
    production = {
        "app_env": "production",
        "database_require_runtime_role_separation": True,
        "furniscope_jwt_private_key": "private",
        "furniscope_jwt_public_keys_json": "{}",
    }
    with pytest.raises(ValueError, match="Production requires STORAGE_BACKEND=s3"):
        _settings(**production)
    with pytest.raises(ValueError, match="requires TLS"):
        _settings(
            **production,
            storage_backend="s3",
            object_storage_endpoint="storage.internal:9000",
            object_storage_access_key="access",
            object_storage_secret_key="secret",
            object_storage_secure=False,
        )


def test_s3_object_storage_enforces_tenant_prefix(monkeypatch) -> None:
    objects: dict[tuple[str, str], bytes] = {}

    class FakeResponse:
        def __init__(self, content: bytes) -> None:
            self.content = content

        def read(self) -> bytes:
            return self.content

        def close(self) -> None:
            pass

        def release_conn(self) -> None:
            pass

    class FakeMinio:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def bucket_exists(self, _bucket: str) -> bool:
            return True

        def put_object(self, bucket: str, key: str, stream, size: int, **_kwargs) -> None:
            objects[(bucket, key)] = stream.read(size)

        def get_object(self, bucket: str, key: str) -> FakeResponse:
            return FakeResponse(objects[(bucket, key)])

    fake_minio = types.ModuleType("minio")
    fake_minio.Minio = FakeMinio
    monkeypatch.setitem(sys.modules, "minio", fake_minio)
    storage = S3ObjectStorage(
        _settings(
            storage_backend="s3",
            object_storage_endpoint="storage.internal:9000",
            object_storage_access_key="access",
            object_storage_secret_key="secret",
            object_storage_bucket="furniscope-data",
        )
    )
    key, digest = storage.store(
        tenant_id=7,
        filename="sales.csv",
        mime_type="text/csv",
        content=b"date,sales\n2026-10-01,3\n",
    )
    assert key.startswith("7/")
    assert len(digest) == 64
    assert storage.read(tenant_id=7, key=key).startswith(b"date,sales")
    with pytest.raises(BusinessError, match="不属于本企业"):
        storage.read(tenant_id=8, key=key)


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
    assert payload["listings"][0]["platform_listing_id"] == "PROC-HF-001"
    assert payload["listings"][0]["sale_price"] == 129.0


def test_authorized_pack_url_rejects_unknown_and_escape() -> None:
    service = AuthorizedSignalService(_settings())
    with pytest.raises(BusinessError) as missing:
        service._validate_source_url("pack://authorized/does-not-exist")
    assert missing.value.code == "SOURCE_PACK_NOT_FOUND"
    with pytest.raises(BusinessError) as escaped:
        service._validate_source_url("pack://authorized/../secrets")
    assert escaped.value.code == "SOURCE_URL_INVALID"


def test_official_policy_json_accepts_federal_register_results() -> None:
    service = AuthorizedSignalService(_settings())
    entries = service._parse_policy_payload(
        {"source_type": "official_json"},
        json.dumps({
            "results": [{
                "document_number": "2026-12345",
                "title": "Furniture safety certification update",
                "abstract": "New certification requirements for furniture.",
                "html_url": "https://www.federalregister.gov/documents/2026-12345",
                "publication_date": "2026-09-20",
            }],
        }),
    )

    assert entries[0]["external_id"] == "2026-12345"
    assert entries[0]["summary"].startswith("New certification")
    assert entries[0]["published_at"].year == 2026


def test_policy_category_filter_rejects_unrelated_recalls() -> None:
    assert _matches_policy_category(
        "sofa",
        "reclining chair battery packs recalled due to fire and burn hazards",
    )
    assert not _matches_policy_category(
        "sofa",
        "water bead toys recalled due to ingestion hazard",
    )
    assert _matches_policy_category(
        "furniture",
        "safety standard for clothing storage units and dressers",
    )
