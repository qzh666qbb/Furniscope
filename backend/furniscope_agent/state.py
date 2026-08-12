"""LangGraph state and document-locked enums."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict


TaskStatus = Literal[
    "draft", "queued", "running", "waiting_human", "partial_succeeded",
    "succeeded", "failed", "cancelled",
]
ExternalStage = Literal[
    "understanding_product", "researching_market", "evaluating_opportunity",
    "generating_recommendation", "completed",
]
InternalStage = Literal[
    "task_initializing", "preflight_check", "context_loading", "data_quality",
    "competitor_filtering", "competitor_embedding", "competitor_reranking",
    "user_confirmation", "review_preprocessing", "review_extracting",
    "need_clustering", "market_analytics", "opportunity_scoring",
    "strategy_generating", "evidence_auditing", "report_generating",
    "persisting", "completed",
]
ConfirmationType = Literal[
    "fact_conflict", "insufficient_data", "low_confidence",
    "high_risk_recommendation",
]


class UserConfirmation(TypedDict):
    confirmation_id: str
    confirmation_type: ConfirmationType
    question: str
    recommended_option: str
    options: list[dict[str, Any]]
    evidence_refs: list[dict[str, Any]]
    impact: dict[str, Any] | str
    checkpoint_stage: str
    expires_at: str | None


class PartialFailure(TypedDict):
    stage_code: str
    unit_type: str
    failed_unit_ids: list[str]
    failed_count: int
    total_count: int
    impact: str
    confidence_cap: float | None
    retryable: bool


class FurniScopeGraphState(TypedDict, total=False):
    task_id: int
    task_uuid: str
    tenant_id: int
    status: TaskStatus
    external_stage: ExternalStage
    internal_stage: InternalStage
    progress_percent: float
    product_id: int
    product_profile_version: int
    dataset_id: int
    target_market: dict[str, Any]
    version_bundle: dict[str, Any]
    analysis_config: dict[str, Any]
    product_context_ref: dict[str, Any]
    valid_listing_ids: list[int]
    valid_review_ids: list[int]
    competitor_set_version: int
    quality_flags: Annotated[list[dict[str, Any]], operator.add]
    trend_eligible: bool
    stage_results: dict[str, Any]
    user_confirmation: UserConfirmation | None
    retry_context: dict[str, Any]
    partial_failures: Annotated[list[PartialFailure], operator.add]
    fatal_error: dict[str, Any] | None
    cancel_requested: bool
    # Map/Reduce control references; never contain review text or vectors.
    review_batches: list[dict[str, Any]]
    review_batch: dict[str, Any]
    review_batch_results: Annotated[list[dict[str, Any]], operator.add]
    strategy_units: list[dict[str, Any]]
    strategy_unit: dict[str, Any]
    strategy_results: Annotated[list[dict[str, Any]], operator.add]
    analytics_results: Annotated[list[dict[str, Any]], operator.add]
    route_to_confirmation: bool
    confirmation_request: UserConfirmation | None
    report_ref: dict[str, Any]


EXTERNAL_STAGE_BY_INTERNAL: dict[str, ExternalStage] = {
    "task_initializing": "understanding_product",
    "preflight_check": "understanding_product",
    "context_loading": "understanding_product",
    "data_quality": "researching_market",
    "competitor_filtering": "researching_market",
    "competitor_embedding": "researching_market",
    "competitor_reranking": "researching_market",
    "user_confirmation": "researching_market",
    "review_preprocessing": "researching_market",
    "review_extracting": "researching_market",
    "need_clustering": "researching_market",
    "market_analytics": "researching_market",
    "opportunity_scoring": "evaluating_opportunity",
    "strategy_generating": "generating_recommendation",
    "evidence_auditing": "generating_recommendation",
    "report_generating": "generating_recommendation",
    "persisting": "generating_recommendation",
    "completed": "completed",
}


PROGRESS_BY_STAGE: dict[str, float] = {
    "task_initializing": 0, "preflight_check": 5, "context_loading": 15,
    "data_quality": 20, "competitor_filtering": 28,
    "competitor_embedding": 34, "competitor_reranking": 40,
    "review_preprocessing": 44, "review_extracting": 52,
    "need_clustering": 60, "market_analytics": 65,
    "opportunity_scoring": 80, "strategy_generating": 88,
    "evidence_auditing": 93, "report_generating": 97,
    "persisting": 99, "completed": 100,
}
