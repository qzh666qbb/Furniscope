import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from furniscope_api.config import ApiSettings
from furniscope_api.database import Database, bind_tenant_session
from furniscope_api.repositories.conversation_repository import ConversationRepository
from furniscope_api.repositories.knowledge_repository import KnowledgeRepository
from furniscope_api.repositories.memory_repository import DEFAULT_MEMORY_POLICY, MemoryRepository
from furniscope_api.schemas.context_lifecycle import CustomerMemoryPolicy, TurnCreateRequest
from furniscope_api.services.conversation_context import ConversationContextService
from furniscope_api.services.conversation_state import (
    classify_intent,
    detect_context_conflicts,
    extract_markets,
    resolve_conversation_state,
)
from furniscope_api.services.customer_memory import extract_memory_candidates
from furniscope_api.services.idempotency_service import canonical_request_hash
from furniscope_api.services.knowledge_service import (
    KnowledgeService,
    chunk_knowledge_text,
    cosine_similarity,
    lexical_match_score,
)
from furniscope_api.services.turn_service import TurnService


def test_memory_change_extracts_structured_cost_candidate():
    rows = extract_memory_candidates("成本上限改成 50 美元")
    assert rows == [{
        "memory_key": "unit_cost_limit",
        "memory_value": {
            "value": "50 美元",
            "amount": 50,
            "currency": "USD",
            "operator": "lte",
        },
        "confidence": 0.92,
    }]


def test_automatic_memory_extraction_is_disabled_by_default():
    settings = ApiSettings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://localhost/test",
    )
    assert settings.memory_auto_extract_enabled is False
    assert CustomerMemoryPolicy().auto_extract is False
    assert DEFAULT_MEMORY_POLICY["auto_extract"] is False


def test_knowledge_chunks_keep_page_and_overlap():
    chunks = chunk_knowledge_text("[page:12]\n" + "德国阻燃认证。" * 200, max_chars=120, overlap_chars=20)
    assert len(chunks) > 1
    assert all(item["page"] == 12 for item in chunks)
    assert [item["chunk_index"] for item in chunks] == list(range(len(chunks)))


def test_cosine_similarity_is_bounded():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0
    assert cosine_similarity([], []) == 0.0


def test_lexical_match_supports_chinese_and_sku_queries():
    text = "销量预测需要先确认SKU映射，并执行标准建模流程。"
    assert lexical_match_score("销量 预测 SKU", text) == 1.0
    assert lexical_match_score("销量预测流程是什么", text) >= 0.35
    assert lexical_match_score("德国认证", text) == 0.0


@pytest.mark.asyncio
async def test_knowledge_index_falls_back_to_lexical_without_embedding_key():
    workbook = Workbook()
    workbook.active.append(["topic", "content"])
    workbook.active.append(["SKU", "HF-A0393 支持面料和颜色定制"])
    output = BytesIO()
    workbook.save(output)

    class Repository:
        completed = None

        async def load_index_job(self, *_args, **_kwargs):
            return {
                "status": "queued",
                "document_id": 11,
                "document_version_id": 12,
                "filename": "hefeng-products.xlsx",
                "mime_type": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                ),
                "source_content": output.getvalue(),
            }

        async def start_index_job(self, *_args, **_kwargs):
            return True

        async def set_document_status(self, *_args, **_kwargs):
            return None

        async def complete_index_job(self, *_args, **kwargs):
            self.completed = kwargs

        async def fail_index_job(self, *_args, **_kwargs):
            raise AssertionError("lexical fallback must not fail the index job")

    class Client:
        def __init__(self, _settings):
            pass

        async def embeddings(self, _texts):
            raise RuntimeError("MODEL_ROUTER_KEY_MISSING")

        async def close(self):
            return None

    class Session:
        async def commit(self):
            return None

        async def rollback(self):
            return None

    repository = Repository()
    settings = ApiSettings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://localhost/test",
    )
    await KnowledgeService(
        settings,
        repository=repository,
        model_client_factory=Client,
    ).process_index_job(
        Session(),
        tenant_id=1,
        index_job_uuid="00000000-0000-4000-8000-000000000001",
    )

    assert repository.completed["embedding_model"] is None
    assert repository.completed["embedding_dimensions"] is None
    assert repository.completed["chunks"]
    assert all(item["embedding"] is None for item in repository.completed["chunks"])
    assert "HF-A0393" in repository.completed["extracted_text"]


@pytest.mark.asyncio
async def test_knowledge_search_rejects_below_relevance_threshold():
    class Repository:
        async def search_chunks(self, *_args, **_kwargs):
            return [{
                "chunk_uuid": "chunk-1",
                "document_uuid": "document-1",
                "filename": "weak.pdf",
                "page": 1,
                "chunk_text": "不相关内容",
                "document_version": 1,
                "embedding": [1.0, 0.0],
                "keyword_score": 0.0,
            }]

        async def search_vector_chunks(self, *_args, **_kwargs):
            return []

    class Client:
        def __init__(self, _settings):
            pass

        async def embeddings(self, _texts):
            return {"vectors": [[-1.0, 0.0]], "model": "test", "dimensions": 2}

        async def rerank(self, _query, _candidates, top_n=None):
            return {"ranking": [{"index": 0, "score": 0.2}]}

        async def close(self):
            return None

    settings = ApiSettings(
        _env_file=None,
        app_env="test",
        database_url="postgresql+asyncpg://localhost/test",
        knowledge_relevance_threshold=0.35,
    )
    matches = await KnowledgeService(
        settings,
        repository=Repository(),
        model_client_factory=Client,
    ).search(
        object(),
        tenant_id=1,
        user_id=1,
        workspace_id=None,
        query="德国认证",
        knowledge_base_uuids=["00000000-0000-4000-8000-000000000001"],
        document_types=[],
        top_k=5,
        persist_citations=False,
    )
    assert matches == []


def test_conversation_state_resolves_reference_and_market_switch():
    state, log = resolve_conversation_state(
        persisted={
            "revision": 3,
            "current_product_id": 12,
            "current_market": "US",
            "current_dataset_id": 8,
        },
        question="那刚才那个产品在德国呢？",
        products=[{
            "product_id": 12,
            "sku": "HF-A0590",
            "name": "云感沙发",
            "category_code": "sofa",
        }],
        datasets=[
            {
                "dataset_id": 8,
                "market_country": "US",
                "category_code": "sofa",
                "status": "ready",
            },
            {
                "dataset_id": 9,
                "market_country": "DE",
                "category_code": "sofa",
                "status": "ready",
            },
        ],
        configured_product_id=None,
        configured_dataset_ids=[8, 9],
        configured_task_uuids=[],
        requested_product_id=None,
        requested_dataset_id=None,
        requested_task_uuid=None,
    )
    assert state["current_product_id"] == 12
    assert state["current_market"] == "DE"
    assert state["current_dataset_id"] == 9
    assert state["resolved_references"][0]["resolved_to"] == 12
    assert any(item["field"] == "current_dataset_id" for item in log)


def test_conversation_state_tracks_comparison_correction_and_conflict():
    assert extract_markets("同时比较美国和德国") == ["US", "DE"]
    assert classify_intent("不是这个 SKU，是 HF-A0590") == "correct_context"
    state, _ = resolve_conversation_state(
        persisted={"revision": 1, "current_market": "US"},
        question="同时比较美国和德国",
        products=[],
        datasets=[],
        configured_product_id=None,
        configured_dataset_ids=[],
        configured_task_uuids=[],
        requested_product_id=None,
        requested_dataset_id=None,
        requested_task_uuid=None,
    )
    assert state["compared_markets"] == ["US", "DE"]
    conflicts = detect_context_conflicts(
        state=state,
        enterprise_profile={"export_markets": ["GB", {"code": "DE"}]},
        memories=[{"memory_type": "target_market", "value": {"value": "德国"}}],
    )
    assert conflicts[0]["selected_source"] == "current_user_or_workspace_state"
    assert [item["priority"] for item in conflicts[0]["values"]] == [100, 90, 80]
    assert conflicts[0]["values"][1]["value"] == ["DE", "GB"]


@pytest.mark.skipif(
    not os.getenv("FURNISCOPE_CONTEXT_TEST_DATABASE_URL"),
    reason="set FURNISCOPE_CONTEXT_TEST_DATABASE_URL to run PostgreSQL lifecycle test",
)
def test_postgres_memory_version_and_atomic_turn():
    async def run():
        suffix = uuid4().hex[:10]
        settings = ApiSettings(
            _env_file=None,
            app_env="test",
            database_url=os.environ["FURNISCOPE_CONTEXT_TEST_DATABASE_URL"].replace(
                "postgresql://", "postgresql+asyncpg://"
            ),
        )
        database = Database(settings)
        tenant_id = None
        try:
            async with database.session_factory() as session:
                tenant_id = int(await session.scalar(text("""
                    INSERT INTO furniscope.tenants(tenant_code,name,status)
                    VALUES(:code,'Context lifecycle test','active') RETURNING id
                """), {"code": f"CTX_{suffix.upper()}"}))
                user_id = int(await session.scalar(text("""
                    INSERT INTO furniscope.users(
                      tenant_id,email,password_hash,name,role_code,status
                    )
                    VALUES(:tenant_id,:email,'test-hash','Context tester','user','active')
                    RETURNING id
                """), {"tenant_id": tenant_id, "email": f"context-{suffix}@example.com"}))
                other_user_id = int(await session.scalar(text("""
                    INSERT INTO furniscope.users(
                      tenant_id,email,password_hash,name,role_code,status
                    )
                    VALUES(:tenant_id,:email,'test-hash','Other tester','user','active')
                    RETURNING id
                """), {"tenant_id": tenant_id, "email": f"context-other-{suffix}@example.com"}))
                workspace_id = int(await session.scalar(text("""
                    INSERT INTO furniscope.analysis_workspaces(
                      tenant_id,workspace_uuid,name,created_by
                    )
                    VALUES(:tenant_id,CAST(:uuid AS uuid),'Context test',:user_id)
                    RETURNING id
                """), {
                    "tenant_id": tenant_id,
                    "uuid": str(uuid4()),
                    "user_id": user_id,
                }))
                await session.commit()
                await bind_tenant_session(session, tenant_id)

                memories = MemoryRepository()
                first = await memories.create_candidate(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    memory_type="target_market",
                    value={"value": "美国"},
                    confidence=0.92,
                    source_message_uuid=None,
                    scope="workspace",
                    retention_days=365,
                )
                first = await memories.confirm(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_uuid=first["memory_uuid"],
                )
                second = await memories.create_candidate(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    memory_type="target_market",
                    value={"value": "德国"},
                    confidence=0.92,
                    source_message_uuid=None,
                    scope="workspace",
                    retention_days=365,
                )
                assert second["supersedes_memory_uuid"] == first["memory_uuid"]
                second = await memories.confirm(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_uuid=second["memory_uuid"],
                )
                history = await memories.history(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_uuid=second["memory_uuid"],
                )
                assert [item["status"] for item in history] == ["confirmed", "superseded"]
                expired = await memories.create_candidate(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    memory_type="budget",
                    value={"amount": 1000, "currency": "USD"},
                    confidence=0.95,
                    source_message_uuid=None,
                    scope="workspace",
                    retention_days=365,
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                    expires_at_set=True,
                )
                await memories.confirm(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_uuid=expired["memory_uuid"],
                )
                active_memories, _ = await memories.list(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    offset=0,
                    limit=100,
                    workspace_uuid=await session.scalar(text("""
                        SELECT workspace_uuid::text FROM furniscope.analysis_workspaces
                         WHERE id=:workspace_id
                    """), {"workspace_id": workspace_id}),
                    active_only=True,
                )
                assert {item["memory_type"] for item in active_memories} == {"target_market"}, {
                    "active": active_memories,
                    "target": second,
                    "expired": expired,
                }
                restricted_marker = f"DO_NOT_EXPOSE_{suffix}"
                restricted = await memories.create_candidate(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    memory_type="customer_preference",
                    value={"commercial_strategy": restricted_marker},
                    confidence=0.99,
                    source_message_uuid=None,
                    scope="workspace",
                    retention_days=365,
                    sensitivity="restricted",
                )
                await memories.confirm(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    memory_uuid=restricted["memory_uuid"],
                )
                workspace_uuid = await session.scalar(text("""
                    SELECT workspace_uuid::text FROM furniscope.analysis_workspaces
                     WHERE id=:workspace_id
                """), {"workspace_id": workspace_id})
                context = await ConversationContextService(settings).build(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_uuid=workspace_uuid,
                    question="继续分析",
                    streaming=False,
                )
                assert restricted_marker not in "\n".join(
                    message["content"] for message in context.messages
                )
                assert all(
                    item["memory_uuid"] != restricted["memory_uuid"]
                    for item in context.confirmed_memories
                )
                assert any(
                    item["source_id"] == f"memory:{restricted['memory_uuid']}"
                    and item["trust_level"] == "restricted"
                    for item in context.truncated_sources
                )

                conversation = ConversationRepository()
                request_payload = {
                    "client_turn_id": str(uuid4()),
                    "question": "目标市场改成德国",
                    "product_id": None,
                    "dataset_id": None,
                    "task_uuid": None,
                }
                turn, created = await conversation.reserve_turn(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    client_turn_id=request_payload["client_turn_id"],
                    idempotency_key=f"turn-{suffix}",
                    request_hash=canonical_request_hash(request_payload),
                    request_payload=request_payload,
                    task_uuid=None,
                )
                assert created is True
                payload = await conversation.complete_turn(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    turn_uuid=turn["turn_uuid"],
                    question=request_payload["question"],
                    answer="已记录为待确认记忆。",
                    message_kind="text",
                    task_uuid=None,
                    context_revision=None,
                    context_configuration={},
                    context_sources=[],
                    estimated_tokens=0,
                    truncated_sources=[],
                    context_hash="a" * 64,
                    citations=[],
                    suggested_actions=[],
                    resolved_state={
                        "current_product_id": None,
                        "current_product_label": None,
                        "current_market": "DE",
                        "compared_markets": [],
                        "current_dataset_id": None,
                        "current_task_uuid": None,
                        "current_analysis_stage": None,
                        "pending_confirmation": None,
                        "last_user_intent": "update_context",
                        "resolved_references": [],
                    },
                    state_revision=0,
                    resolution_log=[],
                    context_conflicts=[],
                )
                await session.commit()
                assert payload["answer"] == "已记录为待确认记忆。"
                message_count = int(await session.scalar(text("""
                    SELECT count(*) FROM furniscope.analysis_workspace_messages
                     WHERE tenant_id=:tenant_id AND turn_uuid=CAST(:turn_uuid AS uuid)
                """), {"tenant_id": tenant_id, "turn_uuid": turn["turn_uuid"]}))
                assert message_count == 2
                replay, replay_created = await conversation.reserve_turn(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    client_turn_id=request_payload["client_turn_id"],
                    idempotency_key=f"turn-{suffix}",
                    request_hash=canonical_request_hash(request_payload),
                    request_payload=request_payload,
                    task_uuid=None,
                )
                assert replay_created is False
                assert replay["response_payload"]["answer"] == "已记录为待确认记忆。"
                saved_state = await conversation.get_state(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
                assert saved_state["revision"] == 1
                assert saved_state["current_market"] == "DE"

                pending_payload = {
                    **request_payload,
                    "client_turn_id": str(uuid4()),
                    "question": "继续分析",
                }
                pending, pending_created = await conversation.reserve_turn(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    client_turn_id=pending_payload["client_turn_id"],
                    idempotency_key=f"pending-{suffix}",
                    request_hash=canonical_request_hash(pending_payload),
                    request_payload=pending_payload,
                    task_uuid=None,
                )
                assert pending_created is True
                blocked, blocked_created = await conversation.reserve_turn(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    client_turn_id=str(uuid4()),
                    idempotency_key=f"blocked-{suffix}",
                    request_hash="b" * 64,
                    request_payload={"question": "并发问题"},
                    task_uuid=None,
                )
                assert blocked_created is False
                assert blocked["blocking_turn_uuid"] == pending["turn_uuid"]
                await conversation.fail_turn(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    turn_uuid=pending["turn_uuid"],
                    error_code="TEST_INTERRUPTION",
                )
                retried, retry_created = await conversation.reserve_turn(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    client_turn_id=pending_payload["client_turn_id"],
                    idempotency_key=f"pending-{suffix}",
                    request_hash=canonical_request_hash(pending_payload),
                    request_payload=pending_payload,
                    task_uuid=None,
                )
                assert retry_created is True
                assert retried["status"] == "pending"
                await conversation.cancel_turn(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    turn_uuid=retried["turn_uuid"],
                )

                service = TurnService(settings)
                body = TurnCreateRequest(
                    client_turn_id=uuid4(),
                    question="成本上限改成 50 美元",
                )
                reserved, created = await service.reserve(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_uuid=await session.scalar(text("""
                        SELECT workspace_uuid::text FROM furniscope.analysis_workspaces
                         WHERE id=:workspace_id
                    """), {"workspace_id": workspace_id}),
                    idempotency_key=f"service-{suffix}",
                    body=body,
                )
                assert created is True
                await session.commit()
                completed = await service.answer(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_uuid=await session.scalar(text("""
                        SELECT workspace_uuid::text FROM furniscope.analysis_workspaces
                         WHERE id=:workspace_id
                    """), {"workspace_id": workspace_id}),
                    turn_uuid=reserved["turn_uuid"],
                    body=body,
                )
                await session.commit()
                assert completed["answer"]
                assert completed["memory_candidates"] == []

                class FakeModelClient:
                    def __init__(self, _settings):
                        pass

                    async def embeddings(self, texts):
                        return {
                            "vectors": [[1.0, float("德国" in value)] for value in texts],
                            "dimensions": 2,
                            "model": "test-embedding-v1",
                        }

                    async def rerank(self, _query, candidates, top_n=None):
                        count = min(top_n or len(candidates), len(candidates))
                        return {
                            "ranking": [
                                {"index": index, "score": 1 - index * 0.01}
                                for index in range(count)
                            ]
                        }

                    async def close(self):
                        return None

                knowledge = KnowledgeRepository()
                base = await knowledge.create_base(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    name=f"Compliance {suffix}",
                    description="Test knowledge base",
                    visibility="user",
                )
                owner_bases, owner_total = await knowledge.list_bases(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    offset=0,
                    limit=100,
                )
                other_bases, other_total = await knowledge.list_bases(
                    session,
                    tenant_id=tenant_id,
                    user_id=other_user_id,
                    offset=0,
                    limit=100,
                )
                assert owner_total == 1
                assert owner_bases[0]["visibility"] == "user"
                assert other_total == 0
                assert other_bases == []
                workbook = Workbook()
                workbook.active.append(["market", "requirement"])
                workbook.active.append(["Germany", "德国软体家具需要阻燃认证"])
                output = BytesIO()
                workbook.save(output)
                queued = await KnowledgeService(
                    settings,
                    model_client_factory=FakeModelClient,
                ).queue_document(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    knowledge_base_uuid=base["knowledge_base_uuid"],
                    filename="compliance.xlsx",
                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    content=output.getvalue(),
                    document_type="certification",
                )
                await session.commit()
                await KnowledgeService(
                    settings,
                    model_client_factory=FakeModelClient,
                ).process_index_job(
                    session,
                    tenant_id=tenant_id,
                    index_job_uuid=queued["index_job_uuid"],
                )
                indexed = await knowledge.get_document(
                    session,
                    tenant_id=tenant_id,
                    knowledge_base_id=int(base["id"]),
                    document_uuid=queued["document_uuid"],
                )
                assert indexed["status"] == "ready"
                version_rows, version_total = await knowledge.list_document_versions(
                    session,
                    tenant_id=tenant_id,
                    knowledge_base_id=int(base["id"]),
                    document_uuid=queued["document_uuid"],
                    offset=0,
                    limit=100,
                )
                assert version_total == 1
                assert version_rows[0]["is_current"] is True
                first_version = await knowledge.get_document_version(
                    session,
                    tenant_id=tenant_id,
                    knowledge_base_id=int(base["id"]),
                    document_uuid=queued["document_uuid"],
                    version=1,
                )
                assert "德国软体家具需要阻燃认证" in first_version["extracted_text"]
                queued_v2 = await KnowledgeService(
                    settings,
                    model_client_factory=FakeModelClient,
                ).queue_document(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    knowledge_base_uuid=base["knowledge_base_uuid"],
                    filename="compliance.xlsx",
                    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    content=output.getvalue(),
                    document_type="certification",
                    document_id=int(indexed["id"]),
                )
                await session.commit()
                await KnowledgeService(
                    settings,
                    model_client_factory=FakeModelClient,
                ).process_index_job(
                    session,
                    tenant_id=tenant_id,
                    index_job_uuid=queued_v2["index_job_uuid"],
                )
                version_rows, version_total = await knowledge.list_document_versions(
                    session,
                    tenant_id=tenant_id,
                    knowledge_base_id=int(base["id"]),
                    document_uuid=queued["document_uuid"],
                    offset=0,
                    limit=100,
                )
                assert version_total == 2
                assert [item["version"] for item in version_rows] == [2, 1]
                assert version_rows[0]["is_current"] is True
                indexed = await knowledge.get_document(
                    session,
                    tenant_id=tenant_id,
                    knowledge_base_id=int(base["id"]),
                    document_uuid=queued["document_uuid"],
                )
                embedding_index = (await session.execute(text("""
                    SELECT e.embedding_dimensions,
                           e.tableoid::regclass::text AS partition_name
                      FROM furniscope.knowledge_chunk_embeddings e
                      JOIN furniscope.knowledge_chunks c
                        ON c.id=e.chunk_id AND c.tenant_id=e.tenant_id
                     WHERE e.tenant_id=:tenant_id AND c.document_id=:document_id
                       AND c.document_version_id=:document_version_id
                """), {
                    "tenant_id": tenant_id,
                    "document_id": indexed["id"],
                    "document_version_id": indexed["document_version_id"],
                })).mappings().one()
                assert embedding_index["embedding_dimensions"] == 2
                assert embedding_index["partition_name"].startswith(
                    "knowledge_chunk_embeddings_p"
                )
                matches = await KnowledgeService(
                    settings,
                    model_client_factory=FakeModelClient,
                ).search(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    query="德国有什么认证要求",
                    knowledge_base_uuids=[base["knowledge_base_uuid"]],
                    document_types=["certification"],
                    top_k=3,
                )
                await session.commit()
                assert matches[0]["document_uuid"] == queued["document_uuid"]
                assert matches[0]["citation_uuid"]
                assert await knowledge.archive_document(
                    session,
                    tenant_id=tenant_id,
                    knowledge_base_id=int(base["id"]),
                    document_uuid=queued["document_uuid"],
                )
                assert int(await session.scalar(text("""
                    SELECT count(*) FROM furniscope.knowledge_chunks
                     WHERE tenant_id=:tenant_id AND document_id=:document_id
                """), {
                    "tenant_id": tenant_id,
                    "document_id": indexed["id"],
                })) == 0
                assert int(await session.scalar(text("""
                    SELECT count(*) FROM furniscope.knowledge_chunk_embeddings
                     WHERE tenant_id=:tenant_id
                """), {"tenant_id": tenant_id})) == 0
        finally:
            if tenant_id is not None:
                async with database.session_factory() as cleanup:
                    await cleanup.execute(
                        text("DELETE FROM furniscope.analysis_workspaces WHERE tenant_id=:tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                    await cleanup.execute(
                        text("DELETE FROM furniscope.knowledge_bases WHERE tenant_id=:tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                    await cleanup.execute(
                        text("DELETE FROM furniscope.users WHERE tenant_id=:tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                    await cleanup.execute(
                        text("DELETE FROM furniscope.tenants WHERE id=:tenant_id"),
                        {"tenant_id": tenant_id},
                    )
                    await cleanup.commit()
            await database.close()

    asyncio.run(run())
