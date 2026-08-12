from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.errors import BusinessError
from furniscope_api.services.idempotency_service import IdempotencyService, canonical_request_hash


TEST_DSN = os.getenv("FURNISCOPE_TEST_DATABASE_URL", "").replace("postgresql://", "postgresql+asyncpg://", 1)
pytestmark = pytest.mark.skipif(not TEST_DSN, reason="FURNISCOPE_TEST_DATABASE_URL is not configured")


@pytest_asyncio.fixture
async def identity_and_sessions():
    engine = create_async_engine(TEST_DSN, connect_args={"server_settings": {"search_path": "furniscope,public"}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    """
                    SELECT u.tenant_id,u.id AS user_id
                      FROM users u JOIN tenants t ON t.id=u.tenant_id
                     WHERE u.status='active' AND t.status='active'
                     ORDER BY u.id LIMIT 1
                    """
                )
            )
        ).mappings().one()
    try:
        yield int(row["tenant_id"]), int(row["user_id"]), factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_request_replays_and_different_hash_conflicts(identity_and_sessions) -> None:
    tenant_id, user_id, factory = identity_and_sessions
    service = IdempotencyService()
    key = f"pytest-{uuid4()}"
    route = "API-TEST-IDEMPOTENCY"
    payload = {"sku": "SOFA-001", "attributes": {"width": 220}}

    async with factory() as session, session.begin():
        first = await service.begin(
            session,
            tenant_id=tenant_id,
            actor_user_id=user_id,
            route_code=route,
            http_method="POST",
            idempotency_key=key,
            request_payload=payload,
        )
        assert first.action == "execute"
        await service.finish(
            session,
            tenant_id=tenant_id,
            record_id=first.record_id,
            response_status=201,
            response_body={"success": True, "data": {"product_id": 1}, "token": "must-not-persist"},
            resource_type="product",
            resource_public_id="1",
        )

    async with factory() as session, session.begin():
        replay = await service.begin(
            session,
            tenant_id=tenant_id,
            actor_user_id=user_id,
            route_code=route,
            http_method="POST",
            idempotency_key=key,
            request_payload={"attributes": {"width": 220}, "sku": "SOFA-001"},
        )
        assert replay.action == "replay"
        assert replay.response_status == 201
        assert replay.response_body["token"] == "[REDACTED]"

    async with factory() as session, session.begin():
        with pytest.raises(BusinessError) as error:
            await service.begin(
                session,
                tenant_id=tenant_id,
                actor_user_id=user_id,
                route_code=route,
                http_method="POST",
                idempotency_key=key,
                request_payload={"sku": "DIFFERENT"},
            )
        assert error.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_processing_and_concurrent_key_have_single_winner(identity_and_sessions) -> None:
    tenant_id, user_id, factory = identity_and_sessions
    service = IdempotencyService()
    route = "API-TEST-CONCURRENT"
    key = f"pytest-{uuid4()}"
    payload = {"job": "market-analysis"}

    async def contender() -> str:
        async with factory() as session:
            try:
                async with session.begin():
                    decision = await service.begin(
                        session,
                        tenant_id=tenant_id,
                        actor_user_id=user_id,
                        route_code=route,
                        http_method="POST",
                        idempotency_key=key,
                        request_payload=payload,
                    )
                return decision.action
            except BusinessError as error:
                return error.code

    results = await asyncio.gather(contender(), contender())
    assert sorted(results) == ["IDEMPOTENCY_IN_PROGRESS", "execute"]

    async with factory() as session:
        count = await session.scalar(
            text(
                """
                SELECT count(*) FROM api_idempotency_records
                 WHERE tenant_id=:tenant_id AND route_code=:route AND idempotency_key=:key
                """
            ),
            {"tenant_id": tenant_id, "route": route, "key": key},
        )
        assert count == 1


def test_hash_normalization_redacts_sensitive_values() -> None:
    first = canonical_request_hash(
        {"name": "sofa", "password": "one", "Authorization": "Bearer one", "nested": {"x": 1}}
    )
    second = canonical_request_hash(
        {"nested": {"x": 1}, "Authorization": "Bearer two", "password": "two", "name": "sofa"}
    )
    assert first == second
    assert canonical_request_hash({"message": "Bearer abc.def.ghi sk-secret-marker-123"}) == canonical_request_hash(
        {"message": "Bearer different.value.here sk-another-marker-456"}
    )


def test_authentication_routes_are_explicitly_excluded() -> None:
    async def scenario() -> None:
        service = IdempotencyService()
        with pytest.raises(ValueError, match="Authentication routes"):
            await service.begin(
                None,
                tenant_id=1,
                actor_user_id=1,
                route_code="API-AUTH-01",
                http_method="POST",
                idempotency_key="key",
                request_payload={},
            )

    asyncio.run(scenario())
