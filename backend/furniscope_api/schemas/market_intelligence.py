"""Unified market-intelligence response contract."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MarketIntelligenceScope(BaseModel):
    dataset_id: int | None = None
    dataset_name: str | None = None
    market_country: str | None = None
    platform: str | None = None
    product_id: int | None = None
    task_uuid: str | None = None
    data_class: str


class MarketIntelligenceOverview(BaseModel):
    generated_at: datetime
    scope: MarketIntelligenceScope
    smart_selection: dict[str, Any]
    competitor_tracking: dict[str, Any]
    review_mining: dict[str, Any]
    pricing: dict[str, Any]
    compliance: dict[str, Any]
    data_gaps: list[str] = Field(default_factory=list)


class MarketOpportunity(BaseModel):
    opportunity_id: int
    opportunity_code: str
    title: str
    description: str
    target_country: str | None = None
    target_platform: str | None = None
    target_user_codes: list[Any] = Field(default_factory=list)
    usage_scenario_codes: list[Any] = Field(default_factory=list)
    status: str | None = None
    base_score: float | None = None
    confidence: float | None = None
    recommendation_level: str
    demand_heat_score: float | None = None
    demand_growth_score: float | None = None
    unmet_need_score: float | None = None
    competition_space_score: float | None = None
    profit_space_score: float | None = None
    enterprise_fit_score: float | None = None
    enterprise_fit_confidence: float | None = None
    manufacturing_fit: list[Any] | dict[str, Any] = Field(default_factory=list)
    market_score: float | None = None
    adjusted_score: float | None = None
    policy_snapshot: dict[str, Any] | None = None
    weight_config: dict[str, Any] = Field(default_factory=dict)
    scoring_version: str | None = None
    calculated_at: datetime | None = None
    task_uuid: str | None = None
    dataset_id: int | None = None
    primary_cluster_ids: list[Any] = Field(default_factory=list)
    feedback: dict[str, Any] | None = None


class CompetitorAlertSummary(BaseModel):
    alert_id: int
    watch_id: int
    dataset_id: int
    asin: str
    title: str | None = None
    change_type: str
    change_label: str
    severity: str
    summary: str
    is_read: bool
    detected_at: datetime


class CompetitorAlertDetail(CompetitorAlertSummary):
    platform: str
    market_country: str
    watch_status: str
    before_value: Any = None
    after_value: Any = None
    latest_snapshot: dict[str, Any] | None = None


class ReviewClusterSummary(BaseModel):
    cluster_id: int
    cluster_code: str
    taxonomy_code: str
    name: str
    summary: str
    sentiment_distribution: dict[str, Any] = Field(default_factory=dict)
    aspect_count: int
    review_count: int
    listing_count: int
    mention_rate: float
    importance_score: float
    cluster_confidence: float
    representative_aspect_ids: list[Any] = Field(default_factory=list)
    created_at: datetime


class ReviewEvidence(BaseModel):
    aspect_id: int
    review_id: int
    listing_id: int
    platform_listing_id: str
    listing_title: str
    brand: str | None = None
    rating: float | None = None
    title_original: str | None = None
    language_code: str
    reviewed_at: datetime | None = None
    verified_purchase: bool | None = None
    taxonomy_code: str
    sentiment: str
    severity: str | None = None
    evidence_quote: str
    extraction_confidence: float
    similarity_score: float
    is_representative: bool


class ReviewClusterDetail(ReviewClusterSummary):
    task_uuid: str
    dataset_id: int
    evidence: list[ReviewEvidence] = Field(default_factory=list)


class PricingSimulationRequest(BaseModel):
    dataset_id: int = Field(ge=1)
    comparator_group: str | None = Field(default=None, min_length=1, max_length=100)
    unit_cost: float | None = Field(default=None, gt=0)
    target_margin: float | None = Field(default=None, ge=0.05, le=0.85)
    promo_margin_floor: float = Field(default=0.20, ge=0, le=0.85)
    max_discount_rate: float = Field(default=0.08, ge=0, le=0.80)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
