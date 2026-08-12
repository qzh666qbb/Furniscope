from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

import asyncpg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.app import create_app
from furniscope_api.config import ApiSettings
from furniscope_api.database import Database
from furniscope_api.security.password import PasswordService


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
        await connection.execute("DELETE FROM furniscope.api_idempotency_records WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.analysis_reports WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.workflow_control_events WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.user_confirmations WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.workflow_partial_failures WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.workflow_checkpoints WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.ai_model_runs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.task_stage_runs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.analysis_tasks WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.reviews WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.market_listings WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.market_datasets WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.product_parse_job_files WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.product_parse_jobs WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.file_assets WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("UPDATE furniscope.products SET current_profile_version_id=NULL WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.product_attributes WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.product_profile_versions WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.products WHERE tenant_id=ANY($1::bigint[])", tenant_ids)
        await connection.execute("DELETE FROM furniscope.auth_sessions WHERE user_id=ANY($1::bigint[])", user_ids)
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
        demo_storage_root=tempfile.mkdtemp(prefix="furniscope-api-test-"),
    )
    app = create_app(settings, Database(settings))
    with TestClient(app) as client:
        yield seed, client, (private_pem, public_pem)
    asyncio.run(_cleanup(seed))


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


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
    for role in ("user", "admin"):
        login = _login(client, seed["users"][role]["email"])
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
    assert "/api/v1/auth/refresh" in paths and "post" in paths["/api/v1/auth/refresh"]
    assert "/api/v1/users/me" in paths and "get" in paths["/api/v1/users/me"]
    assert paths["/api/v1/auth/login"]["post"]["operationId"] == "API-AUTH-01"
    assert paths["/api/v1/auth/refresh"]["post"]["operationId"] == "API-AUTH-02"
    assert paths["/api/v1/users/me"]["get"]["operationId"] == "API-AUTH-03"
    login_schema = paths["/api/v1/auth/login"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert "LoginRequest" in login_schema["$ref"]


def test_login_refresh_do_not_write_api_idempotency(auth_environment) -> None:
    seed, client, _ = auth_environment
    _login(client, seed["users"]["admin"]["email"])

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


def test_product_create_idempotent_replay_list_and_admin_denial(auth_environment) -> None:
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

    admin_token = _login(client, seed["users"]["admin"]["email"]).json()["data"]["access_token"]
    denied = client.get("/api/v1/products", headers={"Authorization": f"Bearer {admin_token}"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "PERMISSION_DENIED"

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

    parse_headers = {"Authorization": f"Bearer {user_token}", "Idempotency-Key": f"parse-{uuid4()}"}
    parsed = client.post(f"/api/v1/products/{product_id}/assets:parse", headers=parse_headers,
        data={"source_type": "document", "parse_config": "{}"},
        files=[("files", ("sofa-spec.json", json.dumps({"synthetic": True}).encode(), "application/json"))])
    replayed_parse = client.post(f"/api/v1/products/{product_id}/assets:parse", headers=parse_headers,
        data={"source_type": "document", "parse_config": "{}"},
        files=[("files", ("sofa-spec.json", json.dumps({"synthetic": True}).encode(), "application/json"))])
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
                       'demo_synthetic','明确标识的合成测试数据','[]','ready','{}','[]',$3)
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
    create_payload = {"name": f"合成沙发市场-{uuid4().hex[:8]}", "platform": "amazon",
        "market_country": "US", "category_code": "sofa", "data_start_date": "2026-01-01",
        "data_end_date": "2026-08-01", "source_type": "demo_synthetic",
        "source_name": "FurniScope明确标识合成案例", "field_mapping": []}
    created = client.post("/api/v1/market-datasets", headers=create_headers, json=create_payload)
    replayed = client.post("/api/v1/market-datasets", headers=create_headers, json=create_payload)
    assert created.status_code == replayed.status_code == 201
    assert created.json() == replayed.json()
    assert created.json()["data"]["status"] == "uploaded"

    created_dataset_id = created.json()["data"]["dataset_id"]
    synthetic_payload = {"listings": [{"platform_listing_id": "SYN-SOFA-001",
        "title": "Synthetic Modular Sofa", "currency": "USD", "sale_price": 899.0,
        "captured_at": "2026-08-01T00:00:00Z", "normalized_attributes": {"seat_count": {"value": 3}}}],
        "reviews": [{"platform_review_id": "SYN-REV-001", "platform_listing_id": "SYN-SOFA-001",
                     "rating": 4.0, "content_original": "Synthetic review: comfortable modular sofa.",
                     "language_code": "en"}]}
    import_headers = {**headers, "Idempotency-Key": f"import-{uuid4()}"}
    imported = client.post(f"/api/v1/market-datasets/{created_dataset_id}/imports", headers=import_headers,
        data={"deduplication_strategy": "platform_id_latest", "field_mapping": "[]"},
        files=[("files", ("synthetic-market.json", json.dumps(synthetic_payload).encode(), "application/json"))])
    assert imported.status_code == 202 and imported.json()["data"]["status"] == "validating"
    imported_detail = client.get(f"/api/v1/market-datasets/{created_dataset_id}", headers=headers)
    assert imported_detail.status_code == 200
    assert imported_detail.json()["data"]["status"] == "ready"
    assert imported_detail.json()["data"]["listing_count"] == 1
    assert imported_detail.json()["data"]["valid_review_count"] == 1


def test_analysis_task_create_start_status_result_and_idempotency(auth_environment) -> None:
    seed, client, _ = auth_environment
    user = seed["users"]["user"]

    async def seed_scope() -> tuple[int, int, int, int, int]:
        connection = await asyncpg.connect(TEST_DSN)
        suffix = uuid4().hex[:8]
        try:
            product_id = await connection.fetchval(
                """INSERT INTO furniscope.products(tenant_id,sku,name,category_code,analysis_status,created_by)
                   VALUES($1,$2,'分析任务合成沙发','sofa','ready',$3) RETURNING id""",
                seed["tenant_id"], f"INS-SOFA-{suffix}", user["user_id"])
            profile_id = await connection.fetchval(
                """INSERT INTO furniscope.product_profile_versions
                   (tenant_id,product_id,version_no,schema_version,status,completeness_score,
                    source_summary,confirmed_by,confirmed_at)
                   VALUES($1,$2,1,'synthetic-profile-v1','confirmed',1,
                          '{"data_class":"synthetic_demo"}'::jsonb,$3,now()) RETURNING id""",
                seed["tenant_id"], product_id, user["user_id"])
            await connection.execute(
                "UPDATE furniscope.products SET current_profile_version_id=$2 WHERE id=$1",
                product_id, profile_id)
            dataset_id = await connection.fetchval(
                """INSERT INTO furniscope.market_datasets
                   (tenant_id,name,platform,market_country,category_code,data_start_date,data_end_date,
                    source_type,source_name,status,quality_report,limitations,created_by)
                   VALUES($1,$2,'amazon','US','sofa',CURRENT_DATE-30,CURRENT_DATE,
                          'demo_synthetic','明确标识的分析任务合成数据','ready',
                          '{"data_class":"synthetic_demo"}'::jsonb,
                          '["非真实市场数据"]'::jsonb,$3) RETURNING id""",
                seed["tenant_id"], f"INS-DATA-{suffix}", user["user_id"])
            draft_profile_id = await connection.fetchval(
                """INSERT INTO furniscope.product_profile_versions
                   (tenant_id,product_id,version_no,schema_version,status,source_summary)
                   VALUES($1,$2,2,'synthetic-profile-v1','draft','{}'::jsonb) RETURNING id""",
                seed["tenant_id"], product_id)
            uploaded_dataset_id = await connection.fetchval(
                """INSERT INTO furniscope.market_datasets
                   (tenant_id,name,platform,market_country,category_code,data_end_date,source_type,
                    source_name,status,created_by)
                   VALUES($1,$2,'amazon','US','sofa',CURRENT_DATE,'demo_synthetic',
                          '未就绪合成数据','uploaded',$3) RETURNING id""",
                seed["tenant_id"], f"INS-UPLOADED-{suffix}", user["user_id"])
            return product_id, profile_id, dataset_id, draft_profile_id, uploaded_dataset_id
        finally:
            await connection.close()

    product_id, profile_id, dataset_id, draft_profile_id, uploaded_dataset_id = asyncio.run(seed_scope())
    token = _login(client, user["email"]).json()["data"]["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    body = {"job_name": "合成沙发市场适配分析", "job_type": "product_market_fit",
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

    result = client.get(f"/api/v1/analysis-tasks/{task_uuid}/result", headers=auth)
    assert result.status_code == 200
    assert result.json()["data"]["report_uuid"] == status.json()["data"]["report_uuid"]
    assert result.json()["data"]["data_scope"]["limitations"] == ["非真实市场数据"]

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
    admin_token = _login(client, seed["users"]["admin"]["email"]).json()["data"]["access_token"]
    admin_detail = client.get(f"/api/v1/analysis-tasks/{task_uuid}",
        headers={"Authorization": f"Bearer {admin_token}"},
        params={"include_stage_runs": True, "stage_run_limit": 100})
    assert user_detail.status_code == admin_detail.status_code == 200
    user_failed = next(item for item in user_detail.json()["data"]["stage_runs"]
                       if item["attempt_no"] == 99)
    admin_failed = next(item for item in admin_detail.json()["data"]["stage_runs"]
                        if item["attempt_no"] == 99)
    assert user_failed.get("error_code") is None and user_failed.get("error_message") is None
    assert admin_failed["error_code"] == "SYNTHETIC_RETRYABLE"
    assert user_detail.json()["data"]["partial_failures"][0]["failed_count"] == 1
    assert user_detail.json()["data"]["retryable"] is True

    other_token = _login(client, seed["users"]["other"]["email"]).json()["data"]["access_token"]
    cross_tenant = client.get(f"/api/v1/analysis-tasks/{task_uuid}",
                              headers={"Authorization": f"Bearer {other_token}"})
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["error"]["code"] == "TASK_NOT_FOUND"

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
