from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.app import create_app
from furniscope_api.auth import AuthenticatedPrincipal, require_admin, require_user_or_admin
from furniscope_api.config import ApiSettings
from furniscope_api.errors import BusinessError
from furniscope_api.logging import redact
from furniscope_api.schemas import SuccessEnvelope


class FakeMappingResult:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    def mappings(self) -> "FakeMappingResult":
        return self

    def one_or_none(self) -> dict | None:
        return self._row


class FakeSession:
    def __init__(self, row: dict | None) -> None:
        self.row = row

    async def execute(self, *_args, **_kwargs) -> FakeMappingResult:
        return FakeMappingResult(self.row)

    async def rollback(self) -> None:
        return None


class FakeDatabase:
    def __init__(self, row: dict | None, *, ready: bool = True) -> None:
        self.row = row
        self.ready = ready
        self.closed = False

    async def session(self) -> AsyncIterator[FakeSession]:
        yield FakeSession(self.row)

    async def ping(self) -> None:
        if not self.ready:
            raise ConnectionError("database unavailable")

    async def close(self) -> None:
        self.closed = True


def key_pair() -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def settings(public_key: str) -> ApiSettings:
    return ApiSettings(
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost/furniscope_test",
        furniscope_jwt_public_keys_json=json.dumps({"test-key": public_key}),
    )


def token(private_key: str, *, role_code: str = "user", kid: str = "test-key") -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": "furniscope-api",
            "aud": "furniscope-web",
            "sub": "7",
            "user_id": 7,
            "tenant_id": 11,
            "role_code": role_code,
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=15),
            "jti": str(uuid4()),
        },
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )


def build_test_app(*, row: dict | None, ready: bool = True):
    private_key, public_key = key_pair()
    database = FakeDatabase(row, ready=ready)
    app = create_app(settings(public_key), database=database)

    @app.get("/_test/user")
    async def user_probe(
        request: Request,
        principal: AuthenticatedPrincipal = Depends(require_user_or_admin),
    ) -> SuccessEnvelope[dict[str, int | str]]:
        return SuccessEnvelope(
            data={"user_id": principal.user_id, "tenant_id": principal.tenant_id, "role_code": principal.role_code},
            request_id=request.state.request_id,
        )

    @app.get("/_test/admin")
    async def admin_probe(
        request: Request,
        principal: AuthenticatedPrincipal = Depends(require_admin),
    ) -> SuccessEnvelope[dict[str, int | str]]:
        return SuccessEnvelope(
            data={"user_id": principal.user_id, "tenant_id": principal.tenant_id, "role_code": principal.role_code},
            request_id=request.state.request_id,
        )

    @app.get("/_test/failure")
    async def failure_probe() -> None:
        raise BusinessError("TEST_CONFLICT", "测试冲突", status_code=409)

    return app, database, private_key


def active_row(role_code: str = "user") -> dict:
    return {
        "user_id": 7,
        "tenant_id": 11,
        "role_code": role_code,
        "user_status": "active",
        "tenant_status": "active",
    }


def test_openapi_and_success_envelope_and_request_id() -> None:
    app, database, _ = build_test_app(row=active_row())
    request_id = str(uuid4())
    with TestClient(app) as client:
        openapi = client.get("/openapi.json")
        live = client.get("/health/live", headers={"X-Request-ID": request_id})
    assert openapi.status_code == 200
    assert "/health/live" in openapi.json()["paths"]
    assert live.status_code == 200
    assert live.headers["X-Request-ID"] == request_id
    assert live.json()["success"] is True
    assert live.json()["request_id"] == request_id
    assert live.json()["data"] == {"status": "ok"}
    assert database.closed is True


def test_uniform_failure_envelope() -> None:
    app, _, _ = build_test_app(row=active_row())
    with TestClient(app) as client:
        response = client.get("/_test/failure")
    assert response.status_code == 409
    assert response.json()["success"] is False
    assert response.json()["error"] == {"code": "TEST_CONFLICT", "message": "测试冲突", "details": []}
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_readiness_failure_is_enveloped() -> None:
    app, _, _ = build_test_app(row=active_row(), ready=False)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_missing_token_returns_401() -> None:
    app, _, _ = build_test_app(row=active_row())
    with TestClient(app) as client:
        response = client.get("/_test/user")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_TOKEN_INVALID"


def test_user_cannot_use_admin_dependency() -> None:
    app, _, private_key = build_test_app(row=active_row("user"))
    with TestClient(app) as client:
        response = client.get("/_test/admin", headers={"Authorization": f"Bearer {token(private_key)}"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ADMIN_REQUIRED"


def test_admin_and_tenant_context_are_database_backed() -> None:
    app, _, private_key = build_test_app(row=active_row("admin"))
    with TestClient(app) as client:
        response = client.get(
            "/_test/admin", headers={"Authorization": f"Bearer {token(private_key, role_code='admin')}"}
        )
    assert response.status_code == 200
    assert response.json()["data"] == {"user_id": 7, "tenant_id": 11, "role_code": "admin"}


def test_unknown_kid_and_disabled_user_are_rejected() -> None:
    disabled = active_row()
    disabled["user_status"] = "disabled"
    app, _, private_key = build_test_app(row=disabled)
    with TestClient(app) as client:
        unknown_key = client.get(
            "/_test/user", headers={"Authorization": f"Bearer {token(private_key, kid='unknown')}"}
        )
        disabled_user = client.get(
            "/_test/user", headers={"Authorization": f"Bearer {token(private_key)}"}
        )
    assert unknown_key.status_code == 401
    assert unknown_key.json()["error"]["code"] == "AUTH_TOKEN_INVALID"
    assert disabled_user.status_code == 403
    assert disabled_user.json()["error"]["code"] == "USER_DISABLED"


def test_redaction_removes_tokens_keys_passwords_and_review_text() -> None:
    marker = "sk-secret-marker-123456"
    value = {
        "Authorization": "Bearer abc.def.ghi",
        "password": "plain",
        "api_key": marker,
        "review_text": "full customer review",
        "message": f"upstream rejected {marker}",
    }
    serialized = json.dumps(redact(value))
    assert marker not in serialized
    assert "abc.def.ghi" not in serialized
    assert "plain" not in serialized
    assert "full customer review" not in serialized


def test_analysis_task_openapi_contract_paths_and_operation_ids() -> None:
    app, _, _ = build_test_app(row=active_row())
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
    expected = {
        ("/api/v1/analysis-tasks", "post"): "API-INS-01",
        ("/api/v1/analysis-tasks/{task_uuid}:start", "post"): "API-INS-02",
        ("/api/v1/analysis-tasks/{task_uuid}", "get"): "API-INS-03",
        ("/api/v1/analysis-tasks/{task_uuid}/result", "get"): "API-INS-04",
    }
    for (path, method), operation_id in expected.items():
        assert schema["paths"][path][method]["operationId"] == operation_id
