import pytest

from backend.furniscope_api.errors import BusinessError
from backend.furniscope_api.services.market_intelligence import (
    MarketIntelligenceService,
    build_pricing_recommendation,
)
from backend.furniscope_api.schemas.market_intelligence import PricingSimulationRequest
from pydantic import ValidationError


def test_pricing_recommendation_applies_cost_and_margin_floor():
    result = build_pricing_recommendation(
        [90, 100, 110, 120],
        currency="USD",
        unit_cost=70,
        target_margin=0.4,
        review_growth_sensitivity=-1.2,
    )

    assert result["status"] == "cost_above_market"
    assert result["price_floor"] == 116.67
    assert result["recommended_price"] == 116.67
    assert result["promo_floor"] == 107.33
    assert result["promo_floor_components"] == {
        "market_p25": 97.5,
        "max_discount_floor": 107.33,
        "promo_margin_floor": 87.5,
    }
    assert result["decision_support"]["binding_constraint"]["code"] == "max_discount_floor"
    assert result["decision_support"]["regular"]["gross_profit_per_unit"] == 46.67
    assert result["decision_support"]["regular"]["gross_margin"] == 0.4
    assert result["decision_support"]["promotion"]["gross_profit_per_unit"] == 37.33
    assert len(result["decision_support"]["scenarios"]) == 4
    assert result["sensitivity_status"] == "exploratory_proxy"
    assert result["review_growth_price_sensitivity"] == -1.2


def test_pricing_recommendation_never_claims_profit_without_cost():
    result = build_pricing_recommendation(
        [80, 100, 120],
        currency="USD",
        unit_cost=None,
        target_margin=None,
        review_growth_sensitivity=None,
    )

    assert result["status"] == "market_anchor_only"
    assert result["market_median"] == 100
    assert result["price_floor"] is None
    assert result["sensitivity_status"] == "insufficient_history"


def test_pricing_recommendation_labels_planning_cost():
    result = build_pricing_recommendation(
        [120, 180, 240],
        currency="USD",
        unit_cost=80,
        target_margin=0.35,
        review_growth_sensitivity=None,
        unit_cost_basis="planning_assumption",
        target_margin_basis="planning_assumption",
    )

    assert result["status"] == "planning_anchor"
    assert result["cost_basis"] == "planning_assumption"
    assert result["target_margin_basis"] == "planning_assumption"
    assert "确认实际单位成本" in result["message"]


def test_pricing_recommendation_rejects_empty_market_sample():
    result = build_pricing_recommendation(
        [],
        currency=None,
        unit_cost=50,
        target_margin=0.3,
        review_growth_sensitivity=None,
    )

    assert result["status"] == "insufficient_market_data"
    assert result["sample_size"] == 0


def test_pricing_recommendation_blocks_unknown_or_mixed_currency():
    result = build_pricing_recommendation(
        [80, 100, 120],
        currency=None,
        unit_cost=50,
        target_margin=0.3,
        review_growth_sensitivity=None,
    )

    assert result["status"] == "currency_conflict"
    assert result["currency"] is None
    assert result["sample_size"] == 3
    assert "recommended_price" not in result
    assert "price_floor" not in result


def test_pricing_recommendation_uses_explicit_promotion_rules():
    conservative = build_pricing_recommendation(
        [100, 150, 200, 250, 300],
        currency="USD",
        unit_cost=80,
        target_margin=0.35,
        promo_margin_floor=0.25,
        max_discount_rate=0.05,
        review_growth_sensitivity=None,
    )
    aggressive = build_pricing_recommendation(
        [100, 150, 200, 250, 300],
        currency="USD",
        unit_cost=80,
        target_margin=0.35,
        promo_margin_floor=0.10,
        max_discount_rate=0.30,
        review_growth_sensitivity=None,
    )

    assert conservative["recommended_price"] == 200
    assert conservative["promo_floor"] == 190
    assert aggressive["promo_floor"] == 150
    assert conservative["provenance"]["max_discount_rate"] == "policy_default"


def test_pricing_recommendation_requires_three_market_prices():
    result = build_pricing_recommendation(
        [100, 120],
        currency="USD",
        unit_cost=60,
        target_margin=0.3,
        review_growth_sensitivity=None,
    )

    assert result["status"] == "insufficient_market_data"
    assert result["sample_size"] == 2
    assert result["minimum_sample_size"] == 3
    assert "recommended_price" not in result


def test_pricing_request_rejects_zero_cost_before_service_call():
    try:
        PricingSimulationRequest(
            dataset_id=31,
            unit_cost=0,
            target_margin=0.35,
            promo_margin_floor=0.2,
            max_discount_rate=0.08,
        )
    except ValidationError as error:
        assert "unit_cost" in str(error)
    else:
        raise AssertionError("zero unit cost must be rejected")


def test_pricing_groups_use_structured_category_and_reject_unknown_group():
    rows = [
        {
            "sale_price": price,
            "currency": "USD",
            "comparator_group": code,
            "comparator_group_label": label,
        }
        for code, label, prices in (
            ("recliner", "躺椅", [100, 120, 140, 160]),
            ("armchair", "扶手椅", [80, 90, 100]),
            ("single", "单样本", [200]),
        )
        for price in prices
    ]

    groups = MarketIntelligenceService._pricing_group_catalog(rows)

    assert [group["group_code"] for group in groups] == ["recliner", "armchair"]
    assert groups[0]["sample_size"] == 4
    assert groups[0]["market_median"] == 130
    selected = MarketIntelligenceService._select_pricing_group(
        groups,
        comparator_group=None,
    )
    assert selected["group_code"] == "recliner"
    assert len(MarketIntelligenceService._prices_for_group(rows, selected)) == 4
    with pytest.raises(BusinessError, match="可比竞品组不存在"):
        MarketIntelligenceService._select_pricing_group(
            groups,
            comparator_group="unknown",
        )
