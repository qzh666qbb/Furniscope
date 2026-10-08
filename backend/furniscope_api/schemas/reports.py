"""Dashboard and decision-report read projections."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DashboardSummary(BaseModel):
    products: int = Field(ge=0)
    running_tasks: int = Field(ge=0)
    pending_confirmations: int = Field(ge=0)
    pending_confirmation_tasks: int = Field(default=0, ge=0)
    failed_tasks: int = Field(default=0, ge=0)
    conflicted_products: int = Field(default=0, ge=0)
    reports: int = Field(ge=0)
    forecast_jobs: int = Field(ge=0)
    insight_snapshot: "DashboardInsightSnapshot | None" = None


class DashboardInsightSnapshot(BaseModel):
    task_uuid: UUID
    report_uuid: UUID
    report_title: str
    product_sku: str
    product_name: str
    data_class: str
    data_source: str | None = None
    analyzed_at: datetime
    pain_point_count: int = Field(ge=0)
    high_frequency_issue_count: int = Field(ge=0)
    competitor_count: int = Field(ge=0)
    price_band_low: float | None = Field(default=None, ge=0)
    price_band_high: float | None = Field(default=None, ge=0)
    price_currency: str | None = None
    recommendation_count: int = Field(ge=0)
    high_priority_recommendation_count: int = Field(ge=0)
    opportunity_count: int = Field(ge=0)
    priority_opportunity_count: int = Field(ge=0)


class ReportListItem(BaseModel):
    report_uuid: UUID
    task_uuid: UUID
    title: str
    product_id: int
    product_sku: str
    product_name: str
    job_name: str | None = None
    target_country: str
    target_platform: str
    overall_opportunity_score: float
    overall_confidence: float
    decision_recommendation: str
    data_class: str
    created_at: datetime


class ReportArchiveRequest(BaseModel):
    report_uuids: list[UUID] = Field(min_length=1, max_length=100)
    model_config = ConfigDict(extra="forbid")


class ReportArchiveResponse(BaseModel):
    archived: int = Field(ge=0)


class ReportDetail(ReportListItem):
    executive_summary: str
    data_scope_snapshot: dict[str, Any]
    product_profile_snapshot: dict[str, Any]
    enterprise_profile_snapshot: dict[str, Any]
    target_user_summary: dict[str, Any] | None
    price_summary: dict[str, Any] | None
    risk_summary: dict[str, Any] | list[Any]
    pending_validation_items: list[Any]
    sections: list[dict[str, Any]]
    partial_failures_snapshot: list[Any]
    version_bundle: dict[str, Any]
    model_run: dict[str, Any]


class EvidenceItem(BaseModel):
    claim_type: str
    claim_id: int
    claim_path: str | None
    claim_category: str
    evidence_type: str
    evidence_id: int
    support_type: str
    relevance_score: float
    is_primary: bool
    display_order: int
    evidence_quote: str | None = None
    sentiment: str | None = None
    severity: str | None = None
    taxonomy_code: str | None = None
    listing_title: str | None = None
    listing_brand: str | None = None
    listing_price: float | None = None
