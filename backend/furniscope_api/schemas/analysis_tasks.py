"""API-INS-01 through API-INS-04 schemas locked by API V3."""

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal["draft", "queued", "running", "waiting_human", "partial_succeeded", "succeeded", "failed", "cancelled"]
ExternalStage = Literal["understanding_product", "researching_market", "evaluating_opportunity", "generating_recommendation", "completed"]


class AnalysisTaskCreateRequest(BaseModel):
    job_name: str = Field(min_length=1, max_length=200)
    job_type: Literal["product_market_fit", "product_improvement"]
    product_id: int = Field(gt=0)
    product_profile_version_id: int = Field(gt=0)
    dataset_id: int = Field(gt=0)
    target_country: str = Field(pattern=r"^[A-Z]{2}$")
    target_platform: str = Field(min_length=1, max_length=32)
    analysis_currency: str = Field(pattern=r"^[A-Z]{3}$")
    analysis_config: dict[str, Any]
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class AnalysisTaskCreated(BaseModel):
    task_uuid: UUID
    job_name: str
    job_type: str
    status: TaskStatus
    stage: ExternalStage
    progress_percent: float = Field(ge=0, le=100)
    workspace_uuid: UUID | None = None
    report_uuid: UUID | None


class AnalysisTaskArchiveRequest(BaseModel):
    task_uuids: list[UUID] = Field(min_length=1, max_length=100)
    model_config = ConfigDict(extra="forbid")


class AnalysisTaskArchiveResponse(BaseModel):
    archived: int = Field(ge=0)


class AnalysisTaskListItem(BaseModel):
    task_uuid: UUID
    job_name: str
    job_type: str
    status: TaskStatus
    stage: ExternalStage
    progress_percent: float = Field(ge=0, le=100)
    product_id: int
    product_sku: str
    product_name: str
    target_country: str
    target_platform: str
    report_uuid: UUID | None
    source: str | None = None
    workspace_uuid: UUID | None = None
    created_at: datetime
    updated_at: datetime


class AnalysisTaskStarted(BaseModel):
    task_uuid: UUID
    status: TaskStatus
    stage: ExternalStage
    progress_percent: float = Field(ge=0, le=100)
    checkpoint_stage: str | None
    retryable: bool


class StageRunSummary(BaseModel):
    stage_code: str
    attempt_no: int = Field(ge=1)
    status: str
    started_at: datetime | None
    ended_at: datetime | None
    retryable: bool | None
    error_code: str | None = None
    error_message: str | None = None


class PartialFailureSummary(BaseModel):
    unit_type: str
    failed_count: int = Field(gt=0)
    total_count: int = Field(gt=0)
    impact: str
    retryable: bool


class UserConfirmationProjection(BaseModel):
    confirmation_id: UUID
    confirmation_type: str
    question: str
    recommended_option: str
    options: list[dict[str, Any]]
    evidence_refs: list[dict[str, Any]]
    impact: dict[str, Any] | str
    checkpoint_stage: str
    expires_at: datetime | None


class ConfirmationAnswerRequest(BaseModel):
    selected_option: str = Field(min_length=1, max_length=64)
    user_input: Any = None
    model_config = ConfigDict(extra="forbid")


class ConfirmationAnswerAccepted(BaseModel):
    confirmation_id: UUID
    event_uuid: UUID
    task_uuid: UUID
    status: Literal["queued"] = "queued"
    already_accepted: bool


class ConfirmationListItem(UserConfirmationProjection):
    task_uuid: UUID
    task_status: TaskStatus
    created_at: datetime


class AnalysisTaskStatusResponse(BaseModel):
    task_uuid: UUID
    status: TaskStatus
    stage: ExternalStage
    progress_percent: float = Field(ge=0, le=100)
    job_name: str | None = None
    product_id: int | None = None
    failure_message: str | None = None
    stage_runs: list[StageRunSummary]
    partial_failures: list[PartialFailureSummary]
    checkpoint_stage: str | None
    retryable: bool
    user_confirmation: UserConfirmationProjection | None
    report_uuid: UUID | None
    analysis_config: dict[str, Any] = Field(default_factory=dict)


class ReportSummary(BaseModel):
    title: str
    executive_summary: str
    decision_recommendation: str
    overall_opportunity_score: float
    overall_confidence: float


class DataScopeSummary(BaseModel):
    target_country: str
    target_platform: str
    data_start_date: date | None
    data_end_date: date
    listing_count: int = Field(ge=0)
    valid_review_count: int = Field(ge=0)
    limitations: list[Any]


class CompetitorSummary(BaseModel):
    competitor_set_version: int = Field(ge=0)
    direct: int = Field(ge=0)
    benchmark: int = Field(ge=0)
    substitute: int = Field(ge=0)
    excluded: int = Field(ge=0)
    available_review_count: int = Field(ge=0)


class InsightClusterSummary(BaseModel):
    cluster_id: int
    cluster_name: str
    sentiment: str
    importance_score: float
    cluster_confidence: float


class OpportunitySummary(BaseModel):
    opportunity_id: int
    opportunity_code: str
    title: str
    base_score: float
    confidence: float
    recommendation_level: str


class RecommendationSummary(BaseModel):
    recommendation_id: int
    opportunity_id: int
    recommendation_type: str
    recommended_action: str
    priority: str
    confidence: float


class AnalysisTaskResultResponse(BaseModel):
    task_uuid: UUID
    report_uuid: UUID
    report_summary: ReportSummary
    data_scope: DataScopeSummary
    competitor_summary: CompetitorSummary
    insight_clusters: list[InsightClusterSummary]
    opportunities: list[OpportunitySummary]
    recommendations: list[RecommendationSummary]
    partial_failures: list[PartialFailureSummary]
    forecast_summary: dict[str, Any] | None = None
