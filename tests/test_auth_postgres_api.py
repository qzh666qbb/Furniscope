from __future__ import annotations

import asyncio
import csv
import hashlib
from datetime import datetime, timezone
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4

import asyncpg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
from openpyxl import Workbook
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.app import create_app
from furniscope_api.config import ApiSettings
from furniscope_api.database import Database
from furniscope_api.security.password import PasswordService
from furniscope_api.services.model_router_client import ServiceModelRouterClient
from furniscope_api.services.authorized_signals import AuthorizedSignalService
from furniscope_api.services.notifications import NotificationService
from furniscope_api.worker import JobHandlers


TEST_DSN = os.getenv("FURNISCOPE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="FURNISCOPE_TEST_DATABASE_URL is not configured")
PASSWORD = f"FurniScope-Test-{uuid4()}!"


def _keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


async def _seed() -> dict:
    connection = await asyncpg.connect(TEST_DSN)
    suffix = uuid4().hex[:10]
    password_hash = PasswordService().hash(PASSWORD)
    try:
        tenant_id = await connection.fetchval(
            "INSERT INTO furniscope.tenants(tenant_code,name,status) VALUES($1,'认证测试企业','active') RETURNING id",
            f"AUTH_{suffix.upper()}",
        )
        suspended_tenant_id = await connection.fetchval(
            "INSERT INTO furniscope.tenants(tenant_code,name,status) VALUES($1,'暂停认证测试企业','suspended') RETURNING id",
            f"AUTH_S_{suffix.upper()}",
        )
        other_tenant_id = await connection.fetchval(
            "INSERT INTO furniscope.tenants(tenant_code,name,status) VALUES($1,'其他认证测试企业','active') RETURNING id",
            f"AUTH_O_{suffix.upper()}",
        )
        users = {}
        for label, role, status, target_tenant in (
            ("user", "user", "active", tenant_id),
            ("admin", "admin", "active", tenant_id),
            ("disabled", "user", "disabled", tenant_id),
            ("suspended", "user", "active", suspended_tenant_id),
            ("other", "user", "active", other_tenant_id),
        ):
            email = f"auth-{label}-{suffix}@example.invalid"
            user_id = await connection.fetchval(
                """
                INSERT INTO furniscope.users(tenant_id,email,password_hash,name,role_code,status)
                VALUES($1,$2,$3,$4,$5,$6) RETURNING id
                """,
                target_tenant,
                email,
                password_hash,
                f"认证测试{label}",
                role,
                status,
            )
            users[label] = {"user_id": user_id, "tenant_id": target_tenant, "email": email}
        return {"tenant_id": tenant_id, "suspended_tenant_id": suspended_tenant_id,
                "other_tenant_id": other_tenant_id, "users": users}
    finally:
        await connection.close()


async def _cleanup(seed: dict) -> None:
    connection = await asyncpg.connect(TEST_DSN)
    try:
        user_ids = [item["user_id"] for item in seed["users"].values()]
        tenant_ids = [seed["tenant_id"], seed["suspended_tenant_id"], seed["other_tenant_id"]]
        await connection.execute("DELETE FROM furniscope.notification_events WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.notification_channels WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.competitor_change_alerts WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.competitor_listing_snapshots WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.competitor_watch_targets WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.forecast_results WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.forecast_runs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.forecast_jobs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.forecast_model_deployments WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.forecast_training_runs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.tenant_sku_catalog WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.tenant_data_sources WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.forecast_models WHERE version LIKE 'tenant-test-%' AND NOT EXISTS (SELECT 1 FROM furniscope.forecast_model_deployments d WHERE d.model_id=forecast_models.id)")
        await connection.execute("DELETE FROM furniscope.policy_alerts WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.policy_sources WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.sentiment_events WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.authorized_collect_runs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.authorized_collect_sources WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.audit_logs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.api_idempotency_records WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.analysis_workspace_messages WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("UPDATE furniscope.analysis_workspaces SET last_analysis_task_id=NULL WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.analysis_workspaces WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.analysis_reports WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.workflow_control_events WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.user_confirmations WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.workflow_partial_failures WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.workflow_checkpoints WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.product_recommendations WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.review_aspects WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.analysis_tasks WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.ai_model_runs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.task_stage_runs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.reviews WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.market_listings WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.market_datasets WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.product_parse_job_files WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.product_parse_jobs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("UPDATE furniscope.products SET current_profile_version_id=NULL WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.product_attributes WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.file_assets WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.product_profile_versions WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.products WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.auth_sessions WHERE user_id=ANY($1::bigint[])", user_ids)
        await connection.execute("DELETE FROM furniscope.password_reset_requests WHERE user_id=ANY($1::bigint[])", user_ids)
        await connection.execute("DELETE FROM furniscope.registration_applications WHERE reviewed_by=ANY($1::bigint[])", user_ids)
        await connection.execute("DELETE FROM furniscope.users WHERE id=ANY($1::bigint[])", user_ids)
        await connection.execute(
            "DELETE FROM furniscope.tenants WHERE id=ANY($1::bigint[])",
            tenant_ids,
        )
    finally:
        await connection.close()


@pytest.fixture(scope="module")
def auth_environment():
    seed = asyncio.run(_seed())
    private_pem, public_pem = _keys()
    settings = ApiSettings(
        app_env="test",
        database_url=TEST_DSN.replace("postgresql://", "postgresql+asyncpg://", 1),
        furniscope_jwt_private_key=private_pem,
        furniscope_jwt_public_keys_json=json.dumps({"auth-test-kid": public_pem}),
        internal_service_token="test-internal-token",
        demo_storage_root=tempfile.mkdtemp(prefix="furniscope-api-test-"),
        product_parse_mode="model",
        analysis_worker_mode="external",
        analysis_tool_mode="external",
        aliyun_model_router_api_key="test-model-router-key",
    )
    app = create_app(settings, Database(settings))
    with TestClient(app) as client:
        yield seed, client, (private_pem, public_pem)
    asyncio.run(_cleanup(seed))


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _admin_login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/admin/login", json={"email": email, "password": password})


async def _restore_password(user_id: int) -> None:
    connection = await asyncpg.connect(TEST_DSN)
    try:
        await connection.execute(
            "UPDATE furniscope.users SET password_hash=$1, updated_at=now() WHERE id=$2",
            PasswordService().hash(PASSWORD),
            user_id,
        )
    finally:
        await connection.close()


def test_login_access_claims_and_server_resolved_tenant(auth_environment) -> None:
    seed, client, (_, public_pem) = auth_environment
    user = seed["users"]["user"]
    response = _login(client, user["email"].upper())
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["expires_in"] == 900
    assert data["token_type"] == "Bearer"
    assert data["user"]["role_code"] == "user"
    header = jwt.get_unverified_header(data["access_token"])
    assert header == {"alg": "RS256", "kid": "auth-test-kid", "typ": "JWT"}
    claims = jwt.decode(
        data["access_token"], public_pem, algorithms=["RS256"],
        issuer="furniscope-api", audience="furniscope-web"
    )
    assert claims["sub"] == str(user["user_id"])
    assert claims["tenant_id"] == user["tenant_id"]
    assert claims["role_code"] == "user"
    assert all(name in claims for name in ("iat", "nbf", "exp", "jti"))
    assert 895 <= claims["exp"] - claims["iat"] <= 900


def test_wrong_and_unknown_credentials_are_indistinguishable(auth_environment) -> None:
    seed, client, _ = auth_environment
    wrong = _login(client, seed["users"]["user"]["email"], "wrong-password")
    unknown = _login(client, f"unknown-{uuid4()}@example.invalid", "wrong-password")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"] == unknown.json()["error"]
    assert wrong.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_enterprise_password_reset_updates_password_and_revokes_sessions(auth_environment) -> None:
    seed, client, _ = auth_environment
    user = seed["users"]["user"]
    login = _login(client, user["email"])
    assert login.status_code == 200
    refresh_token = login.json()["data"]["refresh_token"]

    unknown = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": f"missing-{uuid4().hex[:8]}@example.invalid"},
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "AUTH_ACCOUNT_NOT_FOUND"

    admin_request = client.post(
        "/api/v1/auth/password-reset/request",
        json={"email": seed["users"]["admin"]["email"]},
    )
    assert admin_request.status_code == 403

    requested = client.post("/api/v1/auth/password-reset/request", json={"email": user["email"].upper()})
    assert requested.status_code == 200
    payload = requested.json()["data"]
    assert payload["delivery"] == "on_screen"
    assert payload["email"] == user["email"]
    assert len(payload["reset_code"]) == 6
    new_password = f"Reset-Pass-{uuid4().hex[:8]}!"

    wrong = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"email": user["email"], "reset_code": "000000", "new_password": new_password},
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "AUTH_RESET_CODE_INVALID"

    confirmed = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"email": user["email"], "reset_code": payload["reset_code"], "new_password": new_password},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["data"]["reset"] is True

    reused = client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"email": user["email"], "reset_code": payload["reset_code"], "new_password": new_password},
    )
    assert reused.status_code == 401

    try:
        old_login = _login(client, user["email"])
        assert old_login.status_code == 401
        refreshed = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert refreshed.status_code in {401, 409}
        new_login = _login(client, user["email"], new_password)
        assert new_login.status_code == 200
    finally:
        asyncio.run(_restore_password(user["user_id"]))


def test_login_portals_reject_cross_role(auth_environment) -> None:
    seed, client, _ = auth_environment
    admin_on_enterprise = _login(client, seed["users"]["admin"]["email"])
    user_on_admin = _admin_login(client, seed["users"]["user"]["email"])
    assert admin_on_enterprise.status_code == 403
    assert admin_on_enterprise.json()["error"]["code"] == "AUTH_ADMIN_PORTAL_REQUIRED"
    assert user_on_admin.status_code == 403
    assert user_on_admin.json()["error"]["code"] == "AUTH_ENTERPRISE_PORTAL_REQUIRED"
    accepted = _admin_login(client, seed["users"]["admin"]["email"])
    assert accepted.status_code == 200
    assert accepted.json()["data"]["user"]["role_code"] == "admin"


def test_disabled_user_and_suspended_tenant(auth_environment) -> None:
    seed, client, _ = auth_environment
    disabled = _login(client, seed["users"]["disabled"]["email"])
    suspended = _login(client, seed["users"]["suspended"]["email"])
    assert disabled.status_code == 403
    assert disabled.json()["error"]["code"] == "USER_DISABLED"
    assert suspended.status_code == 403
    assert suspended.json()["error"]["code"] == "TENANT_SUSPENDED"


def test_refresh_rotation_replay_revokes_family_and_tokens_are_hash_only(auth_environment) -> None:
    seed, client, _ = auth_environment
    login = _login(client, seed["users"]["user"]["email"])
    first_refresh = login.json()["data"]["refresh_token"]
    access_token = login.json()["data"]["access_token"]
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
    assert rotated.status_code == 200
    second_refresh = rotated.json()["data"]["refresh_token"]
    assert second_refresh != first_refresh
    assert "user" not in rotated.json()["data"]

    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "AUTH_TOKEN_REUSE_DETECTED"

    async def assertions() -> None:
        connection = await asyncpg.connect(TEST_DSN)
        try:
            rows = await connection.fetch(
                "SELECT refresh_token_hash,revoked_at,revoke_reason FROM furniscope.auth_sessions WHERE user_id=$1 ORDER BY id DESC LIMIT 2",
                seed["users"]["user"]["user_id"],
            )
            assert len(rows) == 2
            assert all(len(row["refresh_token_hash"]) == 64 for row in rows)
            assert all(first_refresh not in row["refresh_token_hash"] and second_refresh not in row["refresh_token_hash"] for row in rows)
            assert all(row["revoked_at"] is not None and row["revoke_reason"] == "reuse_detected" for row in rows)
            dump = await connection.fetchval(
                "SELECT string_agg(to_jsonb(s)::text,'') FROM furniscope.auth_sessions s WHERE user_id=$1",
                seed["users"]["user"]["user_id"],
            )
            assert access_token not in dump and first_refresh not in dump and second_refresh not in dump
        finally:
            await connection.close()

    asyncio.run(assertions())


def test_expired_refresh_token_is_rejected_and_revoked(auth_environment) -> None:
    seed, client, _ = auth_environment
    plain = f"expired-{uuid4()}-refresh-token-value-with-enough-length"

    async def insert_expired() -> None:
        import hashlib
        connection = await asyncpg.connect(TEST_DSN)
        try:
            await connection.execute(
                """
                INSERT INTO furniscope.auth_sessions
                  (tenant_id,user_id,refresh_token_hash,token_family_uuid,created_at,expires_at)
                VALUES($1,$2,$3,$4,now()-interval '2 days',now()-interval '1 day')
                """,
                seed["users"]["user"]["tenant_id"],
                seed["users"]["user"]["user_id"],
                hashlib.sha256(plain.encode()).hexdigest(),
                uuid4(),
            )
        finally:
            await connection.close()

    asyncio.run(insert_expired())
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": plain})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REFRESH_TOKEN_EXPIRED"


def test_wrong_issuer_audience_and_expired_access_tokens(auth_environment) -> None:
    seed, client, (private_pem, _) = auth_environment
    user = seed["users"]["user"]

    def malformed(*, issuer="furniscope-api", audience="furniscope-web", expired=False):
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "iss": issuer, "aud": audience, "sub": str(user["user_id"]),
                "user_id": user["user_id"], "tenant_id": user["tenant_id"],
                "role_code": "user", "iat": now, "nbf": now,
                "exp": now.replace(year=now.year - 1) if expired else now.replace(year=now.year + 1),
                "jti": str(uuid4()),
            },
            private_pem, algorithm="RS256", headers={"kid": "auth-test-kid"},
        )

    for access_token in (
        malformed(issuer="wrong-issuer"),
        malformed(audience="wrong-audience"),
        malformed(expired=True),
    ):
        response = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_TOKEN_INVALID"


def test_current_user_for_user_and_admin_and_no_tenant_override(auth_environment) -> None:
    seed, client, _ = auth_environment
    logins = (
        ("user", _login(client, seed["users"]["user"]["email"])),
        ("admin", _admin_login(client, seed["users"]["admin"]["email"])),
    )
    for role, login in logins:
        token = login.json()["data"]["access_token"]
        response = client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token}"},
            params={"tenant_id": seed["suspended_tenant_id"]},
        )
        assert response.status_code == 200
        assert response.json()["data"]["user_id"] == seed["users"][role]["user_id"]
        assert response.json()["data"]["role_code"] == role
        assert response.json()["data"]["tenant"]["tenant_code"].startswith("AUTH_")


def test_openapi_contains_only_locked_auth_paths(auth_environment) -> None:
    _, client, _ = auth_environment
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/auth/login" in paths and "post" in paths["/api/v1/auth/login"]
    assert "/api/v1/auth/admin/login" in paths and "post" in paths["/api/v1/auth/admin/login"]
    assert "/api/v1/auth/refresh" in paths and "post" in paths["/api/v1/auth/refresh"]
    assert "/api/v1/users/me" in paths and "get" in paths["/api/v1/users/me"]
    assert "/api/v1/auth/password-reset/request" in paths and "post" in paths["/api/v1/auth/password-reset/request"]
    assert "/api/v1/auth/password-reset/confirm" in paths and "post" in paths["/api/v1/auth/password-reset/confirm"]
    assert paths["/api/v1/auth/login"]["post"]["operationId"] == "API-AUTH-01"
    assert paths["/api/v1/auth/admin/login"]["post"]["operationId"] == "API-AUTH-05"
    assert paths["/api/v1/auth/refresh"]["post"]["operationId"] == "API-AUTH-02"
    assert paths["/api/v1/users/me"]["get"]["operationId"] == "API-AUTH-03"
    assert "/internal/v1/forecast/tenants/{tenant_id}/deploy" not in paths
    assert "/api/v1/admin/model-routes/{task_type}" not in paths
    assert "/api/v1/admin/prompt-templates" not in paths
    assert "/api/v1/admin/users" not in paths
    assert "/api/v1/admin/data-sources" not in paths
    login_schema = paths["/api/v1/auth/login"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert "LoginRequest" in login_schema["$ref"]


def test_prometheus_metrics_expose_api_and_business_health(auth_environment) -> None:
    _, client, _ = auth_environment
    client.get("/health/live")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "furniscope_http_requests_total" in response.text
    assert "furniscope_http_request_duration_seconds_bucket" in response.text
    assert "furniscope_analysis_tasks" in response.text
    assert "furniscope_model_runs_total" in response.text
    assert "furniscope_db_pool_connections" in response.text


def test_login_refresh_do_not_write_api_idempotency(auth_environment) -> None:
    seed, client, _ = auth_environment
    _admin_login(client, seed["users"]["admin"]["email"])

    async def count() -> int:
        connection = await asyncpg.connect(TEST_DSN)
        try:
            return await connection.fetchval(
                "SELECT count(*) FROM furniscope.api_idempotency_records WHERE actor_user_id=ANY($1::bigint[]) AND route_code LIKE 'API-AUTH-%'",
                [item["user_id"] for item in seed["users"].values()],
            )
        finally:
            await connection.close()

    assert asyncio.run(count()) == 0


def test_product_create_idempotent_replay_list_and_admin_inheritance(
    auth_environment, monkeypatch,
) -> None:
    seed, client, _ = auth_environment
    user_token = _login(client, seed["users"]["user"]["email"]).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {user_token}", "Idempotency-Key": f"prd-{uuid4()}"}
    payload = {"sku": f"SOFA-{uuid4().hex[:8]}", "name": "模块化沙发", "category_code": "sofa", "description": "测试产品"}
    created = client.post("/api/v1/products", headers=headers, json=payload)
    replayed = client.post("/api/v1/products", headers=headers, json=payload)
    assert created.status_code == replayed.status_code == 201
    assert created.json() == replayed.json()
    product_id = created.json()["data"]["product_id"]

    listed = client.get("/api/v1/products", headers={"Authorization": f"Bearer {user_token}"}, params={"keyword": payload["sku"]})
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["product_id"] == product_id

    admin_token = _admin_login(client, seed["users"]["admin"]["email"]).json()["data"]["access_token"]
    admin_listed = client.get("/api/v1/products", headers={"Authorization": f"Bearer {admin_token}"})
    assert admin_listed.status_code == 403
    assert admin_listed.json()["error"]["code"] == "PERMISSION_DENIED"

    invalid = client.post("/api/v1/products", headers={**headers, "Idempotency-Key": f"bad-{uuid4()}"},
                          json={**payload, "sku": f"TABLE-{uuid4().hex[:8]}", "category_code": "table"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "PRODUCT_CATEGORY_INVALID"

    conflict_headers = {**headers, "Idempotency-Key": f"conflict-{uuid4()}"}
    conflict = client.post("/api/v1/products", headers=conflict_headers, json=payload)
    conflict_replay = client.post("/api/v1/products", headers=conflict_headers, json=payload)
    assert conflict.status_code == conflict_replay.status_code == 409
    assert conflict.json() == conflict_replay.json()
    assert conflict.json()["error"]["code"] == "PRODUCT_SKU_CONFLICT"

    detail = client.get(f"/api/v1/products/{product_id}", headers={"Authorization": f"Bearer {user_token}"})
    assert detail.status_code == 200 and detail.json()["data"]["profile_version"] is None
    etag = detail.headers["etag"]
    updated = client.patch(f"/api/v1/products/{product_id}",
        headers={"Authorization": f"Bearer {user_token}", "If-Match": etag},
        json={"attributes": [
            {"attribute_code": "overall_width_cm", "attribute_name": "整体宽度", "value_type": "number",
             "attribute_value": 218, "unit": "cm", "source_type": "user_input", "confidence": 1,
             "confirmation_status": "unconfirmed"},
            {"attribute_code": "primary_material", "attribute_name": "主材", "value_type": "string",
             "attribute_value": "solid_wood", "source_type": "user_input", "confidence": 1,
             "confirmation_status": "unconfirmed"}
        ]})
    assert updated.status_code == 200
    profile_id = updated.json()["data"]["profile_version_id"]
    assert profile_id and updated.headers["etag"] == updated.json()["data"]["resource_version"]
    confirmed_headers = {"Authorization": f"Bearer {user_token}", "If-Match": updated.headers["etag"],
                         "Idempotency-Key": f"confirm-{uuid4()}"}
    confirm_body = {"profile_version_id": profile_id,
                    "confirmed_attribute_codes": ["overall_width_cm", "primary_material"]}
    confirmed = client.post(f"/api/v1/products/{product_id}/profile:confirm", headers=confirmed_headers, json=confirm_body)
    confirmed_replay = client.post(f"/api/v1/products/{product_id}/profile:confirm", headers=confirmed_headers, json=confirm_body)
    assert confirmed.status_code == confirmed_replay.status_code == 200
    assert confirmed.json() == confirmed_replay.json()
    assert confirmed.json()["data"]["status"] == "confirmed"

    async def extracted_product_document(_self, *, output_type, **_kwargs):
        return output_type.model_validate({
            "product_name": payload["name"],
            "sku": payload["sku"],
            "attributes": [
                {"attribute_code": "sku", "value": payload["sku"], "confidence": 1,
                 "evidence_text": payload["sku"]},
                {"attribute_code": "category", "value": "sofa", "confidence": .98,
                 "evidence_text": "产品品类 | 沙发"},
                {"attribute_code": "primary_material", "value": "科技布 / 实木框架",
                 "confidence": .96, "evidence_text": "材质工艺 | 科技布 / 实木框架"},
            ],
        })

    monkeypatch.setattr(ServiceModelRouterClient, "structured", extracted_product_document)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "产品参数"
    sheet.append(["SKU", payload["sku"]])
    sheet.append(["产品品类", "沙发"])
    sheet.append(["产品尺寸", "218 × 95 × 86 cm"])
    sheet.append(["材质工艺", "科技布 / 实木框架"])
    xlsx = BytesIO()
    workbook.save(xlsx)
    xlsx_content = xlsx.getvalue()

    parse_headers = {"Authorization": f"Bearer {user_token}", "Idempotency-Key": f"parse-{uuid4()}"}
    parsed = client.post(f"/api/v1/products/{product_id}/assets:parse", headers=parse_headers,
        data={"source_type": "document", "parse_config": "{}"},
        files=[("files", ("sofa-spec.xlsx", xlsx_content,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))])
    replayed_parse = client.post(f"/api/v1/products/{product_id}/assets:parse", headers=parse_headers,
        data={"source_type": "document", "parse_config": "{}"},
        files=[("files", ("sofa-spec.xlsx", xlsx_content,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))])
    assert parsed.status_code == replayed_parse.status_code == 202
    assert parsed.json() == replayed_parse.json()
    job = client.get(f"/api/v1/product-parse-jobs/{parsed.json()['data']['parse_job_id']}",
                     headers={"Authorization": f"Bearer {user_token}"})
    assert job.status_code == 200
    assert job.json()["data"]["status"] == "succeeded"
    assert job.json()["data"]["summary"] == {"file_count": 1, "succeeded_file_count": 1, "failed_file_count": 0}


def test_dataset_list_detail_and_cross_tenant_isolation(auth_environment) -> None:
    seed, client, _ = auth_environment

    async def insert_dataset() -> int:
        connection = await asyncpg.connect(TEST_DSN)
        try:
            return await connection.fetchval("""
                INSERT INTO furniscope.market_datasets
                  (tenant_id,name,platform,market_country,category_code,data_start_date,data_end_date,
                   source_type,source_name,field_mapping,status,quality_report,limitations,created_by)
                VALUES($1,$2,'amazon','US','sofa',CURRENT_DATE-30,CURRENT_DATE,
                       'licensed_provider','HeFeng 授权市场数据包','[]','ready','{}','[]',$3)
                RETURNING id
            """, seed["tenant_id"], f"API数据集-{uuid4().hex[:8]}", seed["users"]["user"]["user_id"])
        finally:
            await connection.close()

    dataset_id = asyncio.run(insert_dataset())
    token = _login(client, seed["users"]["user"]["email"]).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    listed = client.get("/api/v1/market-datasets", headers=headers, params={"platform": "amazon", "market_country": "US"})
    detail = client.get(f"/api/v1/market-datasets/{dataset_id}", headers=headers)
    missing = client.get("/api/v1/market-datasets/999999999", headers=headers)
    assert listed.status_code == 200
    assert any(item["dataset_id"] == dataset_id for item in listed.json()["data"]["items"])
    assert detail.status_code == 200 and detail.json()["data"]["dataset_id"] == dataset_id
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "DATASET_NOT_FOUND"

    create_headers = {**headers, "Idempotency-Key": f"dataset-{uuid4()}"}
    create_payload = {"name": f"授权沙发市场-{uuid4().hex[:8]}", "platform": "amazon",
        "market_country": "US", "category_code": "sofa", "data_start_date": "2026-01-01",
        "data_end_date": "2026-08-01", "source_type": "enterprise_export",
        "source_name": "HeFeng 企业授权导出",
        "authorization_reference": "HeFeng-AUTH-TEST-SOFA", "field_mapping": []}
    created = client.post("/api/v1/market-datasets", headers=create_headers, json=create_payload)
    replayed = client.post("/api/v1/market-datasets", headers=create_headers, json=create_payload)
    assert created.status_code == replayed.status_code == 201
    assert created.json() == replayed.json()
    assert created.json()["data"]["status"] == "uploaded"

    denied = client.post("/api/v1/market-datasets", headers={**headers, "Idempotency-Key": f"dataset-denied-{uuid4()}"},
        json={**create_payload, "name": f"拒绝合成-{uuid4().hex[:8]}", "source_type": "demo_synthetic"})
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "DATASET_SCOPE_INVALID"

    created_dataset_id = created.json()["data"]["dataset_id"]
    market_payload = {"listings": [{"platform_listing_id": "HF-TEST-SOFA-001",
        "title": "Modular Sofa", "currency": "USD", "sale_price": 899.0,
        "captured_at": "2026-08-01T00:00:00Z", "normalized_attributes": {"seat_count": {"value": 3}}}],
        "reviews": [{"platform_review_id": "HF-TEST-REV-001", "platform_listing_id": "HF-TEST-SOFA-001",
                     "rating": 4.0, "content_original": "Authorized review: comfortable modular sofa.",
                     "language_code": "en"}]}
    import_headers = {**headers, "Idempotency-Key": f"import-{uuid4()}"}
    imported = client.post(f"/api/v1/market-datasets/{created_dataset_id}/imports", headers=import_headers,
        data={"deduplication_strategy": "platform_id_latest", "field_mapping": "[]"},
        files=[("files", ("hefeng-market.json", json.dumps(market_payload).encode(), "application/json"))])
    assert imported.status_code == 202 and imported.json()["data"]["status"] == "validating"
    imported_detail = client.get(f"/api/v1/market-datasets/{created_dataset_id}", headers=headers)
    assert imported_detail.status_code == 200
    assert imported_detail.json()["data"]["status"] == "ready"
    assert imported_detail.json()["data"]["listing_count"] == 1
    assert imported_detail.json()["data"]["valid_review_count"] == 1

    listings = client.get(
        f"/api/v1/market-datasets/{created_dataset_id}/listings",
        headers=headers,
        params={"q": "Modular"},
    )
    reviews = client.get(
        f"/api/v1/market-datasets/{created_dataset_id}/reviews",
        headers=headers,
        params={"sentiment": "positive"},
    )
    assert listings.status_code == 200
    assert listings.json()["data"]["total"] == 1
    assert listings.json()["data"]["items"][0]["platform_listing_id"] == "HF-TEST-SOFA-001"
    assert reviews.status_code == 200
    assert reviews.json()["data"]["total"] == 1
    assert reviews.json()["data"]["items"][0]["content_original"].startswith("Authorized review")

    csv_buffer = StringIO()
    csv_writer = csv.DictWriter(csv_buffer, fieldnames=[
        "record_type", "platform_listing_id", "title", "currency", "sale_price",
        "captured_at", "platform_review_id", "rating", "content_original",
        "language_code", "reviewer_location",
    ])
    csv_writer.writeheader()
    csv_writer.writerow({"record_type": "listing", "platform_listing_id": "CSV-SOFA-002",
        "title": "CSV Compact Sofa", "currency": "USD", "sale_price": "599",
        "captured_at": "2026-08-02T00:00:00Z"})
    csv_writer.writerow({"record_type": "review", "platform_listing_id": "CSV-SOFA-002",
        "platform_review_id": "CSV-REV-001", "rating": "5",
        "content_original": "The compact size fits my apartment and assembly was straightforward.",
        "language_code": "en", "reviewer_location": "US"})
    csv_import = client.post(
        f"/api/v1/market-datasets/{created_dataset_id}/imports",
        headers={**headers, "Idempotency-Key": f"csv-import-{uuid4()}"},
        data={"deduplication_strategy": "platform_id_latest", "field_mapping": "[]"},
        files=[("files", ("market-update.csv", csv_buffer.getvalue().encode(), "text/csv"))],
    )
    assert csv_import.status_code == 202
    csv_detail = client.get(f"/api/v1/market-datasets/{created_dataset_id}", headers=headers)
    assert csv_detail.status_code == 200
    assert csv_detail.json()["data"]["listing_count"] == 1
    assert csv_detail.json()["data"]["valid_review_count"] == 1
    assert csv_detail.json()["data"]["version_no"] >= 2
    replaced = client.get(f"/api/v1/market-datasets/{created_dataset_id}/listings", headers=headers)
    assert replaced.json()["data"]["items"][0]["platform_listing_id"] == "CSV-SOFA-002"

    watched = client.post(
        "/api/v1/competitor-tracking/watches/from-dataset",
        headers=headers,
        json={"dataset_id": created_dataset_id, "limit": 1},
    )
    assert watched.status_code == 200 and len(watched.json()["data"]) == 1
    watch_id = watched.json()["data"][0]["watch_id"]
    assert watched.json()["data"][0]["compare_selected"] is True
    catalog = client.get("/api/v1/competitor-tracking/catalog", headers=headers, params={"q": "CSV"})
    assert catalog.status_code == 200
    catalog_items = catalog.json()["data"]["items"]
    assert catalog_items and catalog_items[0]["asin"] == "CSV-SOFA-002"
    assert catalog_items[0]["watched"] is True
    patched = client.patch(
        f"/api/v1/competitor-tracking/watches/{watch_id}",
        headers=headers,
        json={"compare_selected": False},
    )
    assert patched.status_code == 200 and patched.json()["data"]["compare_selected"] is False
    product_headers = {**headers, "Idempotency-Key": f"watch-prd-{uuid4()}"}
    product = client.post("/api/v1/products", headers=product_headers,
                          json={"sku": f"SOFA-{uuid4().hex[:8]}", "name": "Compact Sofa", "category_code": "sofa"})
    assert product.status_code == 201
    matched = client.post(
        "/api/v1/competitor-tracking/watches/from-product",
        headers=headers,
        json={"product_id": product.json()["data"]["product_id"], "dataset_id": created_dataset_id, "limit": 3},
    )
    assert matched.status_code == 200 and matched.json()["data"][0]["compare_selected"] is True
    overview = client.get("/api/v1/competitor-tracking/overview", headers=headers)
    prices = client.get(f"/api/v1/competitor-tracking/watches/{watch_id}/prices", headers=headers)
    snapshots = client.get(f"/api/v1/competitor-tracking/watches/{watch_id}/snapshots", headers=headers)
    refreshed = client.post("/api/v1/competitor-tracking/refresh", headers=headers)
    assert overview.status_code == 200 and overview.json()["data"]["watch_count"] == 1
    assert prices.status_code == 200 and len(prices.json()["data"]) >= 1
    assert snapshots.status_code == 200 and len(snapshots.json()["data"]) >= 1
    assert refreshed.status_code == 200 and refreshed.json()["data"]["snapshot_rows"] >= 1
    stopped = client.delete(f"/api/v1/competitor-tracking/watches/{watch_id}", headers=headers)
    assert stopped.status_code == 200 and stopped.json()["data"]["deleted"] is True

    async def dataset_audit_counts() -> dict[str, int]:
        connection = await asyncpg.connect(TEST_DSN)
        try:
            rows = await connection.fetch(
                """SELECT action_code,count(*) count FROM furniscope.audit_logs
                     WHERE tenant_id=$1 AND resource_type='market_dataset' AND resource_id=$2
                     GROUP BY action_code""",
                seed["tenant_id"], created_dataset_id,
            )
            return {row["action_code"]: row["count"] for row in rows}
        finally:
            await connection.close()

    assert asyncio.run(dataset_audit_counts()) == {
        "dataset.create": 1,
        "dataset.import.accept": 2,
    }

    deleted = client.delete(f"/api/v1/market-datasets/{created_dataset_id}", headers=headers)
    deleted_detail = client.get(f"/api/v1/market-datasets/{created_dataset_id}", headers=headers)
    assert deleted.status_code == 200 and deleted.json()["data"]["deleted"] is True
    assert deleted_detail.status_code == 404


def test_authorized_market_signals_and_policy_alerts(auth_environment, monkeypatch) -> None:
    seed, client, _ = auth_environment
    user = seed["users"]["user"]
    token = _login(client, user["email"]).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    source_payload = {
        "name": f"授权市场JSON-{uuid4().hex[:8]}",
        "source_kind": "authorized_market_json",
        "endpoint_url": "https://signals.example.invalid/authorized-market.json",
        "platform": "amazon",
        "market_country": "US",
        "category_code": "sofa",
        "authorization_reference": "企业授权 API 合同 AUTH-2026-0914",
        "schedule_minutes": 720,
    }
    created = client.post("/api/v1/market-signals/sources", headers=headers, json=source_payload)
    assert created.status_code == 201
    source_id = created.json()["data"]["source_id"]
    assert created.json()["data"]["authorization_reference"].startswith("企业授权")

    dataset = client.post("/api/v1/market-datasets", headers={
        **headers, "Idempotency-Key": f"live-dataset-{uuid4()}"
    }, json={
        "name": f"授权实时沙发市场-{uuid4().hex[:8]}", "platform": "amazon",
        "market_country": "US", "category_code": "sofa", "data_start_date": "2026-09-01",
        "data_end_date": "2026-09-14", "source_type": "public_authorized",
        "source_name": "Amazon 授权主动采集",
        "authorization_reference": "企业授权 API 合同 AUTH-2026-0914",
        "field_mapping": [],
    })
    assert dataset.status_code == 201
    live_dataset_id = dataset.json()["data"]["dataset_id"]

    crawler = client.post("/api/v1/market-signals/sources", headers=headers, json={
        **source_payload,
        "name": f"Amazon评论主动采集-{uuid4().hex[:8]}",
        "source_kind": "amazon_review_page",
        "endpoint_url": "https://www.amazon.com/product-reviews/B0ABC12345",
        "dataset_id": live_dataset_id,
        "schedule_minutes": 15,
    })
    assert crawler.status_code == 201
    assert crawler.json()["data"]["source_kind"] == "amazon_review_page"
    crawler_id = crawler.json()["data"]["source_id"]

    pack = client.post("/api/v1/market-signals/sources", headers=headers, json={
        **source_payload,
        "name": f"HeFeng授权包-{uuid4().hex[:8]}",
        "source_kind": "authorized_market_json",
        "endpoint_url": "pack://authorized/HeFeng-AUTH-AMZ-US-SOFA-2026Q3",
        "authorization_reference": "HeFeng-AUTH-AMZ-US-SOFA-2026Q3",
        "schedule_minutes": 5,
    })
    assert pack.status_code == 201
    packed = client.post(
        f"/api/v1/market-signals/sources/{pack.json()['data']['source_id']}/fetch", headers=headers)
    assert packed.status_code == 200, packed.text
    assert packed.json()["data"]["status"] == "succeeded", packed.json()
    assert "SYN-HF-001" in packed.json()["data"]["listing_asins"]

    async def authorized_html(_self, _url, _auth_token_env):
        return """<script type="application/ld+json">
          {"@type":"Product","name":"Authorized Sofa","sku":"B0ABC12345",
           "offers":{"price":"429.00","priceCurrency":"USD"},
           "review":{"@type":"Review","identifier":"CRAWL-REV-1",
             "reviewBody":"The cushions are comfortable and the frame is sturdy.",
             "reviewRating":{"ratingValue":5},"datePublished":"2026-09-10"}}
        </script>"""

    monkeypatch.setattr(AuthorizedSignalService, "_fetch_text", authorized_html)
    crawled = client.post(f"/api/v1/market-signals/sources/{crawler_id}/fetch", headers=headers)
    assert crawled.status_code == 200
    assert crawled.json()["data"]["status"] == "succeeded"
    assert crawled.json()["data"]["fetched_count"] == 2
    replayed_crawl = client.post(f"/api/v1/market-signals/sources/{crawler_id}/fetch", headers=headers)
    assert replayed_crawl.status_code == 200
    assert replayed_crawl.json()["data"]["status"] == "succeeded"

    collected = client.post("/api/v1/market-signals/collect-url", headers=headers, json={
        "endpoint_url": "https://www.amazon.com/product-reviews/B0ABC12345",
    })
    assert collected.status_code == 200
    assert collected.json()["data"]["status"] == "succeeded"

    live_detail = client.get(f"/api/v1/market-datasets/{live_dataset_id}", headers=headers)
    live_listings = client.get(f"/api/v1/market-datasets/{live_dataset_id}/listings", headers=headers)
    live_reviews = client.get(f"/api/v1/market-datasets/{live_dataset_id}/reviews", headers=headers)
    assert live_detail.status_code == 200
    assert live_detail.json()["data"]["status"] == "ready"
    assert live_detail.json()["data"]["listing_count"] == 1
    assert live_detail.json()["data"]["review_count"] == 1
    assert live_detail.json()["data"]["valid_review_count"] == 1
    assert len(live_listings.json()["data"]["items"]) == 1
    assert len(live_reviews.json()["data"]["items"]) == 1
    assert live_reviews.json()["data"]["items"][0]["content_original"].startswith("The cushions")

    async def amazon_guard_page(_self, _url, _auth_token_env):
        return "<html><script>window.rx={};var __rx_csd='guard';</script><title>Amazon.com</title></html>"

    monkeypatch.setattr(AuthorizedSignalService, "_fetch_text", amazon_guard_page)
    guarded = client.post(f"/api/v1/market-signals/sources/{crawler_id}/fetch", headers=headers)
    assert guarded.status_code == 200
    assert guarded.json()["data"]["status"] == "failed"
    assert guarded.json()["data"]["fetched_count"] == 0
    assert "Amazon 返回了访问防护或登录页面" in guarded.json()["data"]["error_summary"]

    forbidden = client.post("/api/v1/market-signals/sources", headers=headers, json={
        **source_payload,
        "name": f"内网采集源-{uuid4().hex[:8]}",
        "endpoint_url": "http://127.0.0.1:9000/internal.json",
    })
    assert forbidden.status_code == 422
    assert forbidden.json()["error"]["code"] == "SOURCE_URL_FORBIDDEN"

    events = [
        {"external_id": "SIG-REV-1", "asin": "SIG-SOFA-1", "rating": 5,
         "content_original": "Very comfortable and sturdy sofa.", "reviewer_location": "US"},
        {"external_id": "SIG-REV-2", "asin": "SIG-SOFA-1", "rating": 2,
         "content_original": "The cushion collapsed and has a terrible smell.", "reviewer_location": "US"},
    ]
    ingested = client.post("/api/v1/market-signals/sentiment-events", headers=headers,
                           json={"source_id": source_id, "events": events})
    replayed = client.post("/api/v1/market-signals/sentiment-events", headers=headers,
                           json={"source_id": source_id, "events": events})
    assert ingested.status_code == 201 and ingested.json()["data"]["inserted_count"] == 2
    assert replayed.status_code == 201 and replayed.json()["data"]["inserted_count"] == 0

    negative = client.get("/api/v1/market-signals/sentiment-events", headers=headers,
                          params={"sentiment": "negative", "q": "collapsed"})
    assert negative.status_code == 200
    assert len(negative.json()["data"]) == 1
    assert negative.json()["data"][0]["sentiment"] == "negative"

    feed = client.get("/api/v1/market-signals/sentiment-feed", headers=headers,
                      params={"origin": "live", "sentiment": "negative", "q": "collapsed"})
    assert feed.status_code == 200
    feed_data = feed.json()["data"]
    assert feed_data["total"] == 1 and feed_data["negative_count"] == 1
    assert feed_data["items"][0]["origin"] == "live"
    assert feed_data["items"][0]["sentiment"] == "negative"

    policy = client.post("/api/v1/market-signals/policy-sources", headers=headers, json={
        "name": f"官方政策源-{uuid4().hex[:8]}",
        "source_type": "official_rss",
        "source_url": "https://policy.example.invalid/furniture.xml",
        "market_country": "US",
        "category_code": "sofa",
        "keywords": ["furniture", "recall", "家具"],
        "authorization_reference": "官方公开 RSS 订阅记录 POLICY-2026-0914",
        "schedule_minutes": 30,
    })
    assert policy.status_code == 201
    assert policy.json()["data"]["source_type"] == "official_rss"
    assert policy.json()["data"]["schedule_minutes"] == 30

    channel = client.post("/api/v1/notification-channels", headers=headers, json={
        "name": f"Slack告警-{uuid4().hex[:8]}",
        "channel_type": "slack",
        "target_url": "https://hooks.example.invalid/furniscope",
        "events": ["competitor_alert", "policy_alert"],
    })
    assert channel.status_code == 201
    channel_id = channel.json()["data"]["channel_id"]
    queued = client.post(f"/api/v1/notification-channels/{channel_id}/test", headers=headers)
    assert queued.status_code == 202
    assert queued.json()["data"]["status"] == "pending"

    async def verify_due_scheduler() -> list[str]:
        settings = ApiSettings(
            app_env="test",
            database_url=TEST_DSN.replace("postgresql://", "postgresql+asyncpg://", 1),
            internal_service_token="scheduler-test-token",
            analysis_worker_mode="external",
            analysis_tool_mode="external",
            product_parse_mode="model",
        )
        database = Database(settings)

        class RecordingQueue:
            def __init__(self) -> None:
                self.kinds: list[str] = []

            async def enqueue(self, kind, payload, *, job_id=None):
                self.kinds.append(kind)

        queue = RecordingQueue()
        try:
            await JobHandlers(settings, database).enqueue_due_authorized_signals(queue)
            return queue.kinds
        finally:
            await database.close()

    scheduled_kinds = asyncio.run(verify_due_scheduler())
    assert "authorized_signal_fetch" in scheduled_kinds
    assert "policy_source_fetch" in scheduled_kinds

    def delivered_without_network(_self, _channel, _event):
        return None

    monkeypatch.setattr(NotificationService, "_post", delivered_without_network)

    async def verify_notification_delivery() -> dict[str, int]:
        settings = ApiSettings(
            app_env="test",
            database_url=TEST_DSN.replace("postgresql://", "postgresql+asyncpg://", 1),
            internal_service_token="notification-test-token",
        )
        database = Database(settings)
        try:
            async with database.session_factory() as session:
                result = await NotificationService(settings).deliver_pending(session)
                await session.commit()
                return result
        finally:
            await database.close()

    delivery = asyncio.run(verify_notification_delivery())
    assert delivery["delivered"] >= 1
    events_response = client.get("/api/v1/notification-channels/events", headers=headers)
    assert events_response.status_code == 200
    assert events_response.json()["data"][0]["status"] == "delivered"

    overview = client.get("/api/v1/market-signals/overview", headers=headers)
    assert overview.status_code == 200
    assert overview.json()["data"]["source_count"] == 3
    assert overview.json()["data"]["sentiment_count"] == 4
    assert overview.json()["data"]["policy_source_count"] == 1

    other_token = _login(client, seed["users"]["other"]["email"]).json()["data"]["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}
    other_sources = client.get("/api/v1/market-signals/sources", headers=other_headers)
    other_fetch = client.post(f"/api/v1/market-signals/sources/{source_id}/fetch", headers=other_headers)
    assert other_sources.status_code == 200
    assert all(item["source_id"] != source_id for item in other_sources.json()["data"])
    assert other_fetch.status_code == 404


def test_analysis_task_create_start_status_result_and_idempotency(auth_environment) -> None:
    seed, client, _ = auth_environment
    user = seed["users"]["user"]

    async def seed_scope() -> tuple[int, int, int, int, int]:
        connection = await asyncpg.connect(TEST_DSN)
        suffix = uuid4().hex[:8]
        try:
            product_id = await connection.fetchval(
                """INSERT INTO furniscope.products(tenant_id,sku,name,category_code,analysis_status,created_by)
                   VALUES($1,$2,'模块化科技布沙发','sofa','ready',$3) RETURNING id""",
                seed["tenant_id"], f"INS-SOFA-{suffix}", user["user_id"])
            profile_id = await connection.fetchval(
                """INSERT INTO furniscope.product_profile_versions
                   (tenant_id,product_id,version_no,schema_version,status,completeness_score,
                    source_summary,confirmed_by,confirmed_at)
                   VALUES($1,$2,1,'product-profile-v1','confirmed',1,
                          '{"data_class":"factory_confirmed"}'::jsonb,$3,now()) RETURNING id""",
                seed["tenant_id"], product_id, user["user_id"])
            for code, value in (
                ("material", "technical fabric solid wood frame"),
                ("style", "modern modular"),
                ("dimensions", "225 x 95 x 86 cm"),
                ("moq", "20"),
                ("factory_price_usd", "299"),
            ):
                await connection.execute(
                    """INSERT INTO furniscope.product_attributes
                       (tenant_id,profile_version_id,attribute_code,value,source_type,confidence,
                        confirmation_status)
                       VALUES($1,$2,$3,to_jsonb($4::text),'confirmed_structured',1,'confirmed')""",
                    seed["tenant_id"], profile_id, code, value,
                )
            await connection.execute(
                "UPDATE furniscope.products SET current_profile_version_id=$2 WHERE id=$1",
                product_id, profile_id)
            dataset_id = await connection.fetchval(
                """INSERT INTO furniscope.market_datasets
                   (tenant_id,name,platform,market_country,category_code,data_start_date,data_end_date,
                    source_type,source_name,authorization_reference,status,listing_count,
                    review_count,valid_review_count,quality_score,quality_report,limitations,created_by)
                   VALUES($1,$2,'amazon','US','sofa',CURRENT_DATE-30,CURRENT_DATE,
                          'licensed_provider','授权市场数据供应商','contract://market-data-test',
                          'ready',3,6,6,92,
                          '{"data_class":"authorized_market_data"}'::jsonb,
                          '["样本窗口为最近30天"]'::jsonb,$3) RETURNING id""",
                seed["tenant_id"], f"INS-DATA-{suffix}", user["user_id"])
            listing_ids = []
            for index, (title, price, rating) in enumerate((
                ("Modern modular fabric sofa", 599, 4.5),
                ("Compact apartment sofa", 499, 4.1),
                ("Solid wood frame sectional sofa", 699, 3.9),
            ), 1):
                listing_ids.append(await connection.fetchval(
                    """INSERT INTO furniscope.market_listings
                       (tenant_id,dataset_id,platform_listing_id,title,category_code,currency,
                        sale_price,rating,review_count,captured_at,normalized_attributes)
                       VALUES($1,$2,$3,$4,'sofa','USD',$5,$6,2,now(),$7::jsonb) RETURNING id""",
                    seed["tenant_id"], dataset_id, f"LIST-{suffix}-{index}", title, price, rating,
                    '{"material":{"value":"fabric"},"style":{"value":"modern modular"}}',
                ))
            review_texts = (
                "The modular sofa is comfortable and fits our small apartment.",
                "Assembly was easy and the instructions were clear.",
                "The package was damaged and one wooden leg was broken.",
                "Good size for a compact living room and looks beautiful.",
                "The seat is too firm and not comfortable for long use.",
                "Great fabric quality but the box had a strong chemical smell.",
            )
            for index, review_text in enumerate(review_texts):
                await connection.execute(
                    """INSERT INTO furniscope.reviews
                       (tenant_id,dataset_id,listing_id,platform_review_id,rating,content_original,
                        language_code,reviewed_at,is_valid,content_hash)
                       VALUES($1,$2,$3,$4,$5,$6,'en',CURRENT_DATE-($7::int*5),true,$8)""",
                    seed["tenant_id"], dataset_id, listing_ids[index % 3],
                    f"REV-{suffix}-{index}",
                    2 if "damaged" in review_text or "not comfortable" in review_text else 5,
                    review_text, index, hashlib.sha256(review_text.encode()).hexdigest(),
                )
            draft_profile_id = await connection.fetchval(
                """INSERT INTO furniscope.product_profile_versions
                   (tenant_id,product_id,version_no,schema_version,status,source_summary)
                   VALUES($1,$2,2,'synthetic-profile-v1','draft','{}'::jsonb) RETURNING id""",
                seed["tenant_id"], product_id)
            uploaded_dataset_id = await connection.fetchval(
                """INSERT INTO furniscope.market_datasets
                   (tenant_id,name,platform,market_country,category_code,data_end_date,source_type,
                    source_name,status,created_by)
                   VALUES($1,$2,'amazon','US','sofa',CURRENT_DATE,'enterprise_export',
                          '未就绪授权数据','uploaded',$3) RETURNING id""",
                seed["tenant_id"], f"INS-UPLOADED-{suffix}", user["user_id"])
            return product_id, profile_id, dataset_id, draft_profile_id, uploaded_dataset_id
        finally:
            await connection.close()

    product_id, profile_id, dataset_id, draft_profile_id, uploaded_dataset_id = asyncio.run(seed_scope())
    token = _login(client, user["email"]).json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    body = {"job_name": "授权沙发市场适配分析", "job_type": "product_market_fit",
            "product_id": product_id, "product_profile_version_id": profile_id,
            "dataset_id": dataset_id, "target_country": "US", "target_platform": "amazon",
            "analysis_currency": "USD", "analysis_config": {"schema_version": "analysis-v1"}}
    create_headers = {**auth, "Idempotency-Key": f"ins-create-{uuid4()}"}
    created = client.post("/api/v1/analysis-tasks", headers=create_headers, json=body)
    replay = client.post("/api/v1/analysis-tasks", headers=create_headers, json=body)
    assert created.status_code == replay.status_code == 201
    assert created.json() == replay.json()
    assert created.json()["data"]["status"] == "draft"
    task_uuid = created.json()["data"]["task_uuid"]

    async def task_create_audit_count() -> int:
        connection = await asyncpg.connect(TEST_DSN)
        try:
            return int(await connection.fetchval(
                """SELECT count(*) FROM furniscope.audit_logs a
                     JOIN furniscope.analysis_tasks t ON t.id=a.resource_id AND t.tenant_id=a.tenant_id
                     WHERE a.tenant_id=$1 AND t.task_uuid=$2::uuid
                       AND a.action_code='analysis_task.create'""",
                seed["tenant_id"], task_uuid,
            ))
        finally:
            await connection.close()

    assert asyncio.run(task_create_audit_count()) == 1

    not_ready = client.get(f"/api/v1/analysis-tasks/{task_uuid}/result", headers=auth)
    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["code"] == "TASK_RESULT_NOT_READY"
    unconfirmed = client.post("/api/v1/analysis-tasks",
        headers={**auth, "Idempotency-Key": f"unconfirmed-{uuid4()}"},
        json={**body, "product_profile_version_id": draft_profile_id})
    assert unconfirmed.status_code == 422
    assert unconfirmed.json()["error"]["code"] == "PRODUCT_PROFILE_NOT_CONFIRMED"
    dataset_not_ready = client.post("/api/v1/analysis-tasks",
        headers={**auth, "Idempotency-Key": f"dataset-not-ready-{uuid4()}"},
        json={**body, "dataset_id": uploaded_dataset_id})
    assert dataset_not_ready.status_code == 422
    assert dataset_not_ready.json()["error"]["code"] == "DATASET_NOT_READY"

    changed = client.post("/api/v1/analysis-tasks", headers=create_headers,
                          json={**body, "job_name": "冲突请求"})
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    start_headers = {**auth, "Idempotency-Key": f"ins-start-{uuid4()}"}
    started = client.post(f"/api/v1/analysis-tasks/{task_uuid}:start", headers=start_headers)
    replayed_start = client.post(f"/api/v1/analysis-tasks/{task_uuid}:start", headers=start_headers)
    assert started.status_code == replayed_start.status_code == 202
    assert started.json() == replayed_start.json()

    status = client.get(f"/api/v1/analysis-tasks/{task_uuid}", headers=auth,
                        params={"include_stage_runs": True, "stage_run_limit": 100})
    assert status.status_code == 200
    assert status.json()["data"]["status"] == "succeeded"
    assert status.json()["data"]["stage"] == "completed"
    assert status.json()["data"]["progress_percent"] == 100
    assert status.json()["data"]["report_uuid"]
    assert status.json()["data"]["stage_runs"]
    assert all("input_ref" not in item and "output_ref" not in item
               for item in status.json()["data"]["stage_runs"])

    task_list = client.get("/api/v1/analysis-tasks", headers=auth, params={"page_size": 5})
    assert task_list.status_code == 200
    listed_task = next(item for item in task_list.json()["data"]["items"]
                       if item["task_uuid"] == task_uuid)
    assert listed_task["product_id"] == product_id
    assert listed_task["status"] == "succeeded"
    assert listed_task["stage"] == "completed"
    assert listed_task["report_uuid"] == status.json()["data"]["report_uuid"]

    result = client.get(f"/api/v1/analysis-tasks/{task_uuid}/result", headers=auth)
    assert result.status_code == 200
    assert result.json()["data"]["report_uuid"] == status.json()["data"]["report_uuid"]
    assert result.json()["data"]["data_scope"]["limitations"] == ["样本窗口为最近30天"]

    for path in (
        "competitors", "review-aspects", "insight-clusters", "evidence",
        "opportunities", "recommendations",
    ):
        projection = client.get(f"/api/v1/analysis-tasks/{task_uuid}/{path}", headers=auth)
        assert projection.status_code == 200, projection.text
        projection_data = projection.json()["data"]
        assert projection_data["task_uuid"] == task_uuid
        assert projection_data["items"], f"{path} should contain persisted external analysis results"

    async def add_diagnostic_rows() -> int:
        connection = await asyncpg.connect(TEST_DSN)
        try:
            task_id = await connection.fetchval(
                "SELECT id FROM furniscope.analysis_tasks WHERE task_uuid=$1::uuid", task_uuid)
            run_id = await connection.fetchval(
                """INSERT INTO furniscope.task_stage_runs
                   (tenant_id,task_id,stage_code,attempt_no,idempotency_key,status,input_ref,
                    started_at,ended_at,error_code,error_message,retryable)
                   VALUES($1,$2,'preflight_check',99,$3,'failed','{}'::jsonb,now(),now(),
                          'SYNTHETIC_RETRYABLE','脱敏诊断信息',true) RETURNING id""",
                seed["tenant_id"], task_id, f"diagnostic-{uuid4()}")
            await connection.execute(
                """INSERT INTO furniscope.workflow_partial_failures
                   (tenant_id,task_id,stage_run_id,stage_code,unit_type,failed_unit_ids,
                    failed_count,total_count,impact,retryable)
                   VALUES($1,$2,$3,'review_extracting','review_batch',$4::jsonb,
                          1,10,'评论主题置信度降低',true)""",
                seed["tenant_id"], task_id, run_id, json.dumps(["batch-2"]))
            return task_id
        finally:
            await connection.close()

    task_id = asyncio.run(add_diagnostic_rows())
    user_detail = client.get(f"/api/v1/analysis-tasks/{task_uuid}", headers=auth,
                             params={"include_stage_runs": True, "stage_run_limit": 100})
    admin_token = _admin_login(client, seed["users"]["admin"]["email"]).json()["data"]["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    admin_denied = client.get(f"/api/v1/analysis-tasks/{task_uuid}",
        headers=admin_headers, params={"include_stage_runs": True, "stage_run_limit": 100})
    assert user_detail.status_code == 200
    assert admin_denied.status_code == 403
    assert admin_denied.json()["error"]["code"] == "PERMISSION_DENIED"
    admin_detail = client.get(f"/api/v1/admin/analysis-tasks/{task_uuid}/diagnostics",
        headers=admin_headers)
    assert admin_detail.status_code == 200
    user_failed = next(item for item in user_detail.json()["data"]["stage_runs"]
                       if item["attempt_no"] == 99)
    admin_failed = next(item for item in admin_detail.json()["data"]["stage_runs"]
                        if item["attempt_no"] == 99)
    assert user_failed.get("error_code") is None
    assert user_failed.get("error_message") == "脱敏诊断信息"
    assert admin_failed["error_code"] == "SYNTHETIC_RETRYABLE"
    assert user_detail.json()["data"]["partial_failures"][0]["failed_count"] == 1
    assert user_detail.json()["data"]["retryable"] is True

    other_token = _login(client, seed["users"]["other"]["email"]).json()["data"]["access_token"]
    cross_tenant = client.get(f"/api/v1/analysis-tasks/{task_uuid}",
                              headers={"Authorization": f"Bearer {other_token}"})
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["error"]["code"] == "TASK_NOT_FOUND"
    cross_tenant_insight = client.get(
        f"/api/v1/analysis-tasks/{task_uuid}/opportunities",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert cross_tenant_insight.status_code == 404
    assert cross_tenant_insight.json()["error"]["code"] == "TASK_NOT_FOUND"

    second_start = client.post(f"/api/v1/analysis-tasks/{task_uuid}:start",
                               headers={**auth, "Idempotency-Key": f"new-start-{uuid4()}"})
    assert second_start.status_code == 409
    assert second_start.json()["error"]["code"] == "TASK_ALREADY_STARTED"

    async def stage_count() -> int:
        connection = await asyncpg.connect(TEST_DSN)
        try:
            return await connection.fetchval(
                "SELECT count(*) FROM furniscope.task_stage_runs WHERE task_id=$1", task_id)
        finally:
            await connection.close()
    before = asyncio.run(stage_count())
    replay_once_more = client.post(f"/api/v1/analysis-tasks/{task_uuid}:start", headers=start_headers)
    assert replay_once_more.status_code == 202
    assert asyncio.run(stage_count()) == before

    async def add_confirmation() -> str:
        confirmation_id = str(uuid4())
        checkpoint_id = f"test-checkpoint-{uuid4()}"
        connection = await asyncpg.connect(TEST_DSN)
        try:
            await connection.execute(
                """INSERT INTO furniscope.workflow_checkpoints
                   (checkpoint_id,thread_id,tenant_id,task_id,stage_code,checkpoint_version,
                    state_snapshot,state_hash,is_safe_resume,status)
                   VALUES($1,$2,$3,$4,'opportunity_scoring',999,'{}'::jsonb,$5,true,'active')""",
                checkpoint_id, task_uuid, seed["tenant_id"], task_id, "a" * 64,
            )
            await connection.execute(
                """INSERT INTO furniscope.user_confirmations
                   (confirmation_id,tenant_id,task_id,confirmation_type,question,
                    recommended_option,options,evidence_refs,impact,checkpoint_stage,
                    idempotency_key,checkpoint_id)
                   VALUES($1::uuid,$2,$3,'low_confidence','是否继续低置信度机会评估？',
                          'continue_with_limit',$4::jsonb,$5::jsonb,$6::jsonb,
                          'opportunity_scoring',$7,$8)""",
                confirmation_id, seed["tenant_id"], task_id,
                json.dumps([{"code": "continue_with_limit", "label": "继续并标注限制"}]),
                json.dumps([{"evidence_type": "data_quality", "evidence_id": 1}]),
                json.dumps({"confidence_cap": 0.6}),
                f"confirmation:{confirmation_id}", checkpoint_id,
            )
            return confirmation_id
        finally:
            await connection.close()

    confirmation_id = asyncio.run(add_confirmation())
    confirmations = client.get("/api/v1/user-confirmations", headers=auth)
    assert confirmations.status_code == 200
    item = next(item for item in confirmations.json()["data"]["items"]
                if item["confirmation_id"] == confirmation_id)
    assert item["task_uuid"] == task_uuid
    assert item["recommended_option"] == "continue_with_limit"
    other_confirmations = client.get(
        "/api/v1/user-confirmations", headers={"Authorization": f"Bearer {other_token}"},
    )
    assert all(item["confirmation_id"] != confirmation_id
               for item in other_confirmations.json()["data"]["items"])


def test_analysis_task_scope_readiness_and_cross_tenant(auth_environment) -> None:
    seed, client, _ = auth_environment
    token = _login(client, seed["users"]["user"]["email"]).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": f"invalid-ins-{uuid4()}"}
    body = {"job_name": "非法范围", "job_type": "product_market_fit", "product_id": 999999999,
            "product_profile_version_id": 999999999, "dataset_id": 999999999,
            "target_country": "US", "target_platform": "amazon", "analysis_currency": "USD",
            "analysis_config": {}}
    missing = client.post("/api/v1/analysis-tasks", headers=headers, json=body)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    unknown = client.get(f"/api/v1/analysis-tasks/{uuid4()}",
                         headers={"Authorization": f"Bearer {token}"})
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "TASK_NOT_FOUND"


def test_tenant_admin_manages_sources_and_skus_without_cross_tenant_leak(auth_environment) -> None:
    seed, client, _ = auth_environment
    admin_token = _admin_login(client, seed["users"]["admin"]["email"]).json()["data"]["access_token"]
    user_token = _login(client, seed["users"]["user"]["email"]).json()["data"]["access_token"]
    other_token = _login(client, seed["users"]["other"]["email"]).json()["data"]["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    source = client.post("/api/v1/admin/data-sources", headers=admin_headers, json={
        "name": f"orders-{uuid4().hex[:8]}", "source_kind": "sales_history",
        "connection_type": "object_storage", "secret_ref": "vault://tenant/orders",
        "authorization_reference": "customer-contract-test", "safe_config": {"region": "cn"},
    })
    assert source.status_code == 201
    source_uuid = source.json()["data"]["source_uuid"]
    denied = client.post("/api/v1/admin/data-sources",
                         headers={"Authorization": f"Bearer {user_token}"}, json={
        "name": "denied", "source_kind": "sales_history", "connection_type": "upload",
        "authorization_reference": "test", "safe_config": {},
    })
    assert denied.status_code == 403

    sku = f"TENANT-SKU-{uuid4().hex[:6]}"
    product = client.post("/api/v1/products", headers={
        "Authorization": f"Bearer {user_token}", "Idempotency-Key": f"product-{uuid4()}"}, json={
        "sku": sku, "name": "预测目录测试产品", "category_code": "sofa",
    })
    assert product.status_code == 201
    upserted = client.put(f"/api/v1/admin/sku-catalog/{sku}/US", headers=admin_headers, json={
        "sku": sku, "site": "us", "category_code": "chair", "lifecycle_status": "active",
        "label_status": "complete", "history_weeks": 2, "model_eligible": False,
        "source_uuid": source_uuid, "attributes": {"collection": "launch"},
    })
    assert upserted.status_code == 200
    own_catalog = client.get("/api/v1/forecast/skus", headers={"Authorization": f"Bearer {user_token}"})
    assert any(item["sku"] == sku for item in own_catalog.json()["data"]["items"])
    other_catalog = client.get(
        "/api/v1/forecast/skus", headers={"Authorization": f"Bearer {other_token}"})
    assert all(item["sku"] != sku for item in other_catalog.json()["data"]["items"])
    cold_start = client.post("/api/v1/forecast-jobs", headers={
        "Authorization": f"Bearer {user_token}", "Idempotency-Key": f"forecast-{uuid4()}"}, json={
        "job_name": "cold start without evidence", "granularity": "week", "horizon": 2,
        "skus": [sku], "sites": ["US"], "scenario": {},
    })
    assert cold_start.status_code == 422
    assert cold_start.json()["error"]["code"] == "FORECAST_COLD_START_INPUT_REQUIRED"
    assert client.get("/api/v1/forecast/status").status_code == 401


def test_internal_forecast_deployment_is_explicit_and_tenant_scoped(auth_environment) -> None:
    seed, client, _ = auth_environment
    version = f"tenant-test-{uuid4().hex[:8]}"
    body = {"version": version, "state_uri": "server-managed://default",
            "model_scope": "shared_base"}
    denied = client.post(f"/internal/v1/forecast/tenants/{seed['tenant_id']}/deploy", json=body)
    assert denied.status_code == 401
    deployed = client.post(
        f"/internal/v1/forecast/tenants/{seed['tenant_id']}/deploy",
        headers={"X-Internal-Token": "test-internal-token"}, json=body)
    if deployed.status_code == 422:
        pytest.skip(deployed.json().get("error", {}).get("message") or "forecast assets unavailable")
    assert deployed.status_code == 200
    assert deployed.json()["data"]["catalog_skus"] <= 76

    user_token = _login(client, seed["users"]["user"]["email"]).json()["data"]["access_token"]
    other_token = _login(client, seed["users"]["other"]["email"]).json()["data"]["access_token"]
    training_history = client.get(
        "/api/v1/forecast/training-runs",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert training_history.status_code == 200
    status = client.get("/api/v1/forecast/status",
                        headers={"Authorization": f"Bearer {user_token}"})
    assert status.status_code == 200 and status.json()["data"]["ready"] is True
    user_headers = {"Authorization": f"Bearer {user_token}"}
    catalog = client.get("/api/v1/forecast/skus?limit=1", headers=user_headers).json()["data"]["items"]
    pair = catalog[0]
    created = client.post("/api/v1/forecast-jobs", headers={
        **user_headers, "Idempotency-Key": f"forecast-create-{uuid4()}"}, json={
        "job_name": "tenant deployed model", "granularity": "week", "horizon": 1,
        "product_id": pair["product_id"], "skus": [pair["sku"]], "sites": [pair["site"]],
        "scenario": {"baseline": 10},
    })
    assert created.status_code == 201
    job_uuid = created.json()["data"]["job_uuid"]
    started = client.post(f"/api/v1/forecast-jobs/{job_uuid}:start", headers={
        **user_headers, "Idempotency-Key": f"forecast-start-{uuid4()}"})
    assert started.status_code == 202
    for _ in range(100):
        job = client.get(f"/api/v1/forecast-jobs/{job_uuid}", headers=user_headers).json()["data"]
        if job["status"] in {"succeeded", "failed"}:
            break
        time.sleep(0.05)
    assert job["status"] == "succeeded", job
    result = client.get(f"/api/v1/forecast-jobs/{job_uuid}/result", headers=user_headers)
    assert result.status_code == 200
    assert result.json()["data"]["metrics"]["routing"][0]["strategy"] == "baseline_cold_start"
    other_status = client.get("/api/v1/forecast/status",
                              headers={"Authorization": f"Bearer {other_token}"})
    assert other_status.status_code == 200 and other_status.json()["data"]["ready"] is False
    other_result = client.get(f"/api/v1/forecast-jobs/{job_uuid}/result",
                              headers={"Authorization": f"Bearer {other_token}"})
    assert other_result.status_code == 404


def test_admin_user_and_prompt_endpoints_enforce_role_and_audit(auth_environment) -> None:
    seed, client, _ = auth_environment
    user_token = _login(client, seed["users"]["user"]["email"]).json()["data"]["access_token"]
    admin_token = _admin_login(client, seed["users"]["admin"]["email"]).json()["data"]["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}",
                     "X-Internal-Token": "test-internal-token"}

    denied = client.get("/api/v1/admin/users", headers=user_headers)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ADMIN_REQUIRED"

    listing = client.get("/api/v1/admin/users", headers=admin_headers)
    assert listing.status_code == 200
    platform_denied = client.get("/api/v1/admin/prompt-templates",
                                 headers={"Authorization": f"Bearer {admin_token}"})
    assert platform_denied.status_code == 401
    assert platform_denied.json()["error"]["code"] == "INTERNAL_AUTH_REQUIRED"
    target = next(item for item in listing.json()["data"]["items"]
                  if item["user_id"] == seed["users"]["user"]["user_id"])
    updated = client.patch(
        f"/api/v1/admin/users/{target['user_id']}",
        headers={**admin_headers, "If-Match": target["resource_version"]},
        json={"name": "认证测试普通用户（已审计）"},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["name"] == "认证测试普通用户（已审计）"

    code = f"admin_test_{uuid4().hex[:10]}"
    prompt_headers = {**admin_headers, "Idempotency-Key": f"prompt-{uuid4()}"}
    prompt_body = {"code": code, "version": "v1", "task_type": "report",
                   "template_content": "只根据已提供证据生成报告。", "status": "draft"}
    created = client.post(
        "/api/v1/admin/prompt-templates",
        headers=prompt_headers, json=prompt_body,
    )
    assert created.status_code == 201
    replayed_prompt = client.post("/api/v1/admin/prompt-templates", headers=prompt_headers, json=prompt_body)
    assert replayed_prompt.status_code == 201
    assert replayed_prompt.json()["data"]["id"] == created.json()["data"]["id"]
    prompts = client.get(f"/api/v1/admin/prompt-templates?code={code}", headers=admin_headers)
    assert prompts.status_code == 200
    assert prompts.json()["data"]["total"] == 1

    task_type = f"admin_test_{uuid4().hex[:8]}"

    async def seed_route() -> None:
        connection = await asyncpg.connect(TEST_DSN)
        try:
            await connection.execute(
                """INSERT INTO furniscope.model_route_configs
                   (task_type,config_version,primary_model_id,timeout_ms,max_retries,concurrency_limit,active)
                   VALUES($1,'v1','model-a',30000,1,2,true)""",
                task_type,
            )
        finally:
            await connection.close()

    asyncio.run(seed_route())
    route = client.get(f"/api/v1/admin/model-routes/{task_type}", headers=admin_headers)
    assert route.status_code == 200
    route_data = route.json()["data"]
    route_headers = {**admin_headers, "If-Match": route_data["resource_version"],
                     "Idempotency-Key": f"route-{uuid4()}"}
    route_body = {"primary_model_id": "model-b", "fallback_model_ids": ["model-a"],
                  "timeout_ms": 45000, "max_retries": 2, "batch_size": 20,
                  "concurrency_limit": 4, "compute_config": {"hard_limit": 1000}, "active": True}
    changed = client.put(
        f"/api/v1/admin/model-routes/{task_type}",
        headers=route_headers, json=route_body,
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["primary_model_id"] == "model-b"
    replayed_route = client.put(f"/api/v1/admin/model-routes/{task_type}", headers=route_headers, json=route_body)
    assert replayed_route.status_code == 200
    assert replayed_route.json()["data"]["updated_at"] == changed.json()["data"]["updated_at"]

    async def audit_count() -> int:
        connection = await asyncpg.connect(TEST_DSN)
        try:
            return await connection.fetchval(
                "SELECT count(*) FROM furniscope.audit_logs WHERE tenant_id=$1 AND action_code IN ('admin.user.update','admin.prompt.create','admin.model_route.update')",
                seed["tenant_id"],
            )
        finally:
            await connection.close()

    assert asyncio.run(audit_count()) >= 3


def test_admin_soft_deletes_enterprise_and_can_restore_it(auth_environment) -> None:
    seed, client, _ = auth_environment
    admin_token = _admin_login(client, seed["users"]["admin"]["email"]).json()["data"]["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    target_tenant_id = seed["other_tenant_id"]
    target_email = seed["users"]["other"]["email"]
    target_token = _login(client, target_email).json()["data"]["access_token"]
    deleted_version = None
    try:
        listing = client.get("/api/v1/admin/enterprise-users?page_size=100", headers=admin_headers)
        assert listing.status_code == 200
        target = next(item for item in listing.json()["data"]["items"]
                      if item["tenant_id"] == target_tenant_id)
        enabled = client.patch(
            f"/api/v1/admin/enterprise-users/{target_tenant_id}?user_id={target['user_id']}",
            headers={**admin_headers, "If-Match": target["resource_version"]},
            json={"entitlements": ["sales_forecast"]},
        )
        assert enabled.status_code == 200
        version = enabled.json()["data"]["resource_version"]

        removed = client.delete(
            f"/api/v1/admin/enterprise-users/{target_tenant_id}",
            headers={**admin_headers, "If-Match": version},
        )
        assert removed.status_code == 200
        removed_data = removed.json()["data"]
        assert removed_data["deleted"] is True
        assert removed_data["tenant_status"] == "closed"
        assert removed_data["disabled_account_count"] == 1
        deleted_version = removed_data["resource_version"]

        assert client.get("/api/v1/users/me", headers={
            "Authorization": f"Bearer {target_token}"}).status_code == 403
        assert _login(client, target_email).status_code == 403
        default_items = client.get(
            "/api/v1/admin/enterprise-users?page_size=100", headers=admin_headers,
        ).json()["data"]["items"]
        assert all(item["tenant_id"] != target_tenant_id for item in default_items)
        deleted_items = client.get(
            "/api/v1/admin/enterprise-users?status=closed&page_size=100", headers=admin_headers,
        ).json()["data"]["items"]
        assert any(item["tenant_id"] == target_tenant_id for item in deleted_items)
        model_items = client.get(
            "/api/v1/admin/forecast-models", headers=admin_headers,
        ).json()["data"]["items"]
        assert all(item["tenant_id"] != target_tenant_id for item in model_items)

        own = next(item for item in default_items if item["tenant_id"] == seed["tenant_id"])
        refused = client.delete(
            f"/api/v1/admin/enterprise-users/{seed['tenant_id']}",
            headers={**admin_headers, "If-Match": own["resource_version"]},
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "ADMIN_TENANT_DELETE_FORBIDDEN"
    finally:
        if deleted_version:
            restored = client.patch(
                f"/api/v1/admin/enterprise-users/{target_tenant_id}",
                headers={**admin_headers, "If-Match": deleted_version},
                json={"tenant_status": "active", "user_status": "active", "entitlements": []},
            )
            assert restored.status_code == 200


def test_registration_application_queue_reject_and_approve(auth_environment) -> None:
    seed, client, _ = auth_environment
    approve_email = f"register-ok-{uuid4().hex[:8]}@example.invalid"
    reject_email = f"register-no-{uuid4().hex[:8]}@example.invalid"
    password = "RegisterPass-2026"
    body = {
        "enterprise_name": "注册审批测试家具",
        "contact_name": "申请联系人",
        "email": approve_email,
        "password": password,
        "agreed": True,
    }
    created = client.post("/api/v1/auth/register", json=body)
    assert created.status_code == 201
    assert created.json()["data"]["status"] == "pending"
    assert "password" not in created.json()["data"]
    duplicate = client.post("/api/v1/auth/register", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "REGISTRATION_ALREADY_PENDING"
    existing = client.post("/api/v1/auth/register", json={**body, "email": seed["users"]["user"]["email"]})
    assert existing.status_code == 409
    assert existing.json()["error"]["code"] == "REGISTRATION_EMAIL_EXISTS"
    rejected = client.post("/api/v1/auth/register", json={**body, "email": reject_email})
    assert rejected.status_code == 201
    assert _login(client, approve_email, password).status_code == 401

    user_token = _login(client, seed["users"]["user"]["email"]).json()["data"]["access_token"]
    denied = client.get("/api/v1/admin/registration-applications",
                        headers={"Authorization": f"Bearer {user_token}"})
    assert denied.status_code == 403
    admin_token = _admin_login(client, seed["users"]["admin"]["email"]).json()["data"]["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    listing = client.get("/api/v1/admin/registration-applications?status=pending", headers=admin_headers)
    assert listing.status_code == 200
    items = listing.json()["data"]["items"]
    assert all("password_hash" not in item for item in items)
    approve_item = next(item for item in items if item["email"] == approve_email)
    reject_item = next(item for item in items if item["email"] == reject_email)

    refused = client.post(
        f"/api/v1/admin/registration-applications/{reject_item['id']}:reject",
        headers=admin_headers, json={"reason": "演示环境暂不接入"},
    )
    assert refused.status_code == 200
    assert refused.json()["data"]["status"] == "rejected"

    tenant_code = f"REG_{uuid4().hex[:8].upper()}"
    approved = client.post(
        f"/api/v1/admin/registration-applications/{approve_item['id']}:approve",
        headers={**admin_headers, "Idempotency-Key": f"approve-{uuid4()}"},
        json={"tenant_code": tenant_code, "entitlements": ["sales_forecast", "market_analysis"]},
    )
    assert approved.status_code == 200
    tenant_id = approved.json()["data"]["tenant_id"]
    user_id = approved.json()["data"]["user_id"]
    try:
        replayed = client.post(
            f"/api/v1/admin/registration-applications/{approve_item['id']}:approve",
            headers={**admin_headers, "Idempotency-Key": f"approve-{uuid4()}"},
            json={"tenant_code": tenant_code, "entitlements": ["sales_forecast"]},
        )
        assert replayed.status_code == 409
        entered = _login(client, approve_email, password)
        assert entered.status_code == 200
        assert entered.json()["data"]["user"]["role_code"] == "user"
    finally:
        async def cleanup_created() -> None:
            connection = await asyncpg.connect(TEST_DSN)
            try:
                await connection.execute(
                    "DELETE FROM furniscope.auth_sessions WHERE user_id=$1", user_id)
                await connection.execute(
                    "DELETE FROM furniscope.audit_logs WHERE tenant_id=$1", tenant_id)
                await connection.execute(
                    "DELETE FROM furniscope.registration_applications WHERE email=ANY($1::varchar[])",
                    [approve_email, reject_email],
                )
                await connection.execute(
                    "DELETE FROM furniscope.forecast_model_deployments WHERE tenant_id=$1", tenant_id)
                await connection.execute("DELETE FROM furniscope.users WHERE id=$1", user_id)
                await connection.execute("DELETE FROM furniscope.tenants WHERE id=$1", tenant_id)
            finally:
                await connection.close()

        asyncio.run(cleanup_created())
