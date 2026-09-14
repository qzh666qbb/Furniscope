from __future__ import annotations

import pytest

from backend.furniscope_agent.external_toolbox import (
    AuthorizedDataRequired,
    ExternalCapabilityUnavailable,
    ExternalFurnitureToolbox,
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
