from __future__ import annotations

import sys
import types

import pytest

from backend.furniscope_agent.external_toolbox import (
    AuthorizedDataRequired,
    ExternalCapabilityUnavailable,
    ExternalFurnitureToolbox,
    _cosine_similarity,
    _semantic_cluster_groups,
    _taxonomy_cluster_groups,
    enterprise_fit_for_cluster,
)
from backend.furniscope_agent.demo_support import demo_initial_state


class FakePool:
    def __init__(self, source_type: str = "licensed_provider",
                 authorization_reference: str | None = "licensed-test-fixture") -> None:
        self.source_type = source_type
        self.authorization_reference = authorization_reference

    async def fetchrow(self, _query, *_args):
        return {"source_type": self.source_type, "status": "ready", "listing_count": 2,
                "valid_review_count": 1, "profile_status": "confirmed",
                "authorization_reference": self.authorization_reference}

    async def fetch(self, query, *_args):
        if "product_attributes" in query:
            return [{"id": 7, "attribute_code": "material", "value": "wood",
                     "unit": None, "confidence": .9}]
        if "market_listings" in query:
            return [{"id": 10, "captured_at": None}, {"id": 11, "captured_at": None}]
        return [{"id": 20, "reviewed_at": None}]


class CompetitorPool:
    async def fetchrow(self, _query, *_args):
        return {"sku": "HF-1", "name": "扶手椅", "category_code": "sofa"}

    async def fetch(self, query, *_args):
        if "product_attributes" in query:
            return [{"attribute_code": "material", "value": "linen"}]
        return [{"id": 10, "title": "Linen armchair", "description": "compact sofa chair"}]


class FailingModelClient:
    async def embeddings(self, _texts):
        raise RuntimeError("router unavailable")


@pytest.mark.asyncio
async def test_external_toolbox_reads_authorized_records_and_never_labels_demo():
    state = demo_initial_state()
    toolbox = ExternalFurnitureToolbox(FakePool())
    preflight = await toolbox.execute("preflight", state)
    context = await toolbox.execute("load_product_context", state)
    quality = await toolbox.execute("data_quality", state)
    assert preflight.output_ref["data_class"] == "authorized_market_data"
    assert context.state_update["product_context_ref"]["attribute_ids"] == [7]
    assert quality.state_update["valid_review_ids"] == [20]
    with pytest.raises(ExternalCapabilityUnavailable):
        await toolbox.execute("unknown_capability", state)


@pytest.mark.asyncio
async def test_external_toolbox_rejects_missing_authorization():
    with pytest.raises(AuthorizedDataRequired, match="授权"):
        await ExternalFurnitureToolbox(FakePool(authorization_reference=None)).execute(
            "preflight", demo_initial_state()
        )


@pytest.mark.asyncio
async def test_competitor_rule_recall_never_reuses_failed_model_client():
    state = demo_initial_state()
    state["valid_listing_ids"] = [10]
    toolbox = ExternalFurnitureToolbox(CompetitorPool(), model_client=FailingModelClient())

    with pytest.raises(RuntimeError, match="router unavailable"):
        await toolbox.execute("competitor_embedding", state)

    fallback = await toolbox.execute("competitor_rule_recall", state)
    assert fallback.output_ref["method"] == "deterministic_token_similarity_v1"
    assert fallback.output_ref["scores"][0]["listing_id"] == 10


def test_enterprise_fit_uses_confirmed_category_market_and_capability():
    score, confidence, detail = enterprise_fit_for_cluster(
        {
            "profile_version": 3,
            "profile": {
                "primary_categories": ["chair"],
                "export_markets": ["US"],
                "profile_completeness": .9,
                "confirmed_at": "2026-01-01", "confirmed_by": 1,
            },
            "opportunity_policy": {"version": 1, "required_capabilities": [
                {"capability_type": "material", "capability_code": "solid_wood"}]},
            "capabilities": [
                {
                    "capability_type": "material",
                    "capability_code": "solid_wood",
                    "availability": "yes",
                    "source_type": "confirmed_user",
                },
            ],
        },
        category_code="chair",
        market_country="US",
        taxonomy_code="material",
    )

    assert score == 100
    assert confidence > .8
    assert detail["blocked"] is False
    assert detail["profile_version"] == 3


def test_enterprise_fit_hard_gates_explicitly_unavailable_capability():
    score, _, detail = enterprise_fit_for_cluster(
        {
            "profile": {"primary_categories": ["chair"], "export_markets": ["US"]},
            "opportunity_policy": {"version": 1, "required_capabilities": [
                {"capability_type": "packaging", "capability_code": "drop_test"}]},
            "capabilities": [
                {
                    "capability_type": "packaging",
                    "capability_code": "drop_test",
                    "availability": "no",
                    "source_type": "confirmed_user",
                },
            ],
        },
        category_code="chair",
        market_country="US",
        taxonomy_code="packaging",
    )

    assert score <= 25
    assert detail["blocked"] is True


def test_taxonomy_cluster_fallback_ranks_representatives_by_confidence():
    rows = [
        {"id": 1, "taxonomy_code": "comfort", "extraction_confidence": .7},
        {"id": 2, "taxonomy_code": "comfort", "extraction_confidence": .95},
        {"id": 3, "taxonomy_code": "assembly", "extraction_confidence": .8},
    ]

    groups = _taxonomy_cluster_groups(rows)

    assert len(groups) == 2
    comfort = next(group for group in groups if group["taxonomy_code"] == "comfort")
    assert comfort["representative_ids"] == [2, 1]
    assert comfort["similarities"] == {1: 1.0, 2: 1.0}


def test_semantic_clusters_stay_inside_taxonomy_and_rank_central_evidence(monkeypatch):
    class FakeAgglomerativeClustering:
        def __init__(self, **kwargs):
            assert kwargs == {
                "n_clusters": None,
                "metric": "cosine",
                "linkage": "average",
                "distance_threshold": .28,
            }

        def fit_predict(self, vectors):
            return types.SimpleNamespace(tolist=lambda: [0, 0, 1][:len(vectors)])

    sklearn_module = types.ModuleType("sklearn")
    cluster_module = types.ModuleType("sklearn.cluster")
    cluster_module.AgglomerativeClustering = FakeAgglomerativeClustering
    monkeypatch.setitem(sys.modules, "sklearn", sklearn_module)
    monkeypatch.setitem(sys.modules, "sklearn.cluster", cluster_module)
    rows = [
        {"id": 1, "taxonomy_code": "comfort", "extraction_confidence": .7},
        {"id": 2, "taxonomy_code": "comfort", "extraction_confidence": .95},
        {"id": 3, "taxonomy_code": "comfort", "extraction_confidence": .8},
        {"id": 4, "taxonomy_code": "assembly", "extraction_confidence": .9},
    ]
    vectors = [[1.0, 0.0], [.95, .05], [0.0, 1.0], [1.0, 0.0]]

    groups = _semantic_cluster_groups(rows, vectors)

    assert len(groups) == 3
    comfort_pair = next(group for group in groups if len(group["rows"]) == 2)
    assert comfort_pair["taxonomy_code"] == "comfort"
    assert comfort_pair["representative_ids"][0] == 2
    assert all(0 <= score <= 1 for score in comfort_pair["similarities"].values())
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
