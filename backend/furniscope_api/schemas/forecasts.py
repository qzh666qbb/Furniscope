"""Forecast V4 HTTP contracts."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ForecastScenario(BaseModel):
    price: float | None = Field(default=None, ge=0)
    discount: float | None = Field(default=None, ge=0, le=1)
    inventory: float | None = Field(default=None, ge=0)
    is_promotion: bool = False
    promotion_impact: float | None = Field(default=None, gt=0)
    baseline: float | None = Field(default=None, ge=0)
    reference_sku: str | None = Field(default=None, min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ForecastJobCreateRequest(BaseModel):
    job_name: str = Field(min_length=1, max_length=200)
    product_id: int | None = Field(default=None, gt=0)
    analysis_task_uuid: UUID | None = None
    granularity: Literal["day", "week"]
    horizon: int = Field(ge=1, le=365)
    start_date: date | None = None
    skus: list[str] = Field(min_length=1, max_length=100)
    sites: list[str] = Field(min_length=1, max_length=20)
    scenario: ForecastScenario = Field(default_factory=ForecastScenario)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_scope(self) -> "ForecastJobCreateRequest":
        self.skus = list(dict.fromkeys(item.strip() for item in self.skus if item.strip()))
        self.sites = list(dict.fromkeys(item.strip().upper() for item in self.sites if item.strip()))
        if not self.skus or not self.sites:
            raise ValueError("skus and sites must contain non-empty values")
        max_horizon = 365 if self.granularity == "day" else 52
        if self.horizon > max_horizon:
            raise ValueError(f"{self.granularity} horizon cannot exceed {max_horizon}")
        return self


class ForecastJobSummary(BaseModel):
    job_uuid: UUID
    job_name: str
    status: Literal["draft", "queued", "running", "succeeded", "failed", "cancelled"]
    granularity: Literal["day", "week"]
    horizon: int
    skus: list[str]
    sites: list[str]
    progress_percent: float
    model_version: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ForecastPoint(BaseModel):
    sku: str
    site: str
    bucket_start: date
    bucket_end: date
    predicted_sales: float
    lower: float | None
    upper: float | None
    reliability: Literal["A", "B", "C", "D"]


class ForecastPairSummary(BaseModel):
    sku: str
    site: str
    total: float
    daily_average: float
    lower: float | None
    upper: float | None
    reliability: Literal["A", "B", "C", "D"]
    method: str | None = None
    segment: str | None = None
    validation_status: str | None = None
    validation_scope: str | None = None
    data_through: date | None = None


class ForecastResultResponse(BaseModel):
    job: ForecastJobSummary
    run_uuid: UUID
    model: dict
    metrics: dict
    summaries: list[ForecastPairSummary]
    points: list[ForecastPoint]


class ForecastModelStatus(BaseModel):
    enabled: bool
    ready: bool
    version: str | None = None
    engine: str | None = None
    data_through: date | None = None
    sku_count: int | None = None
    state_checksum: str | None = None
    granularities: list[str] = Field(default_factory=list)
    trained_at: dict[str, str | None] = Field(default_factory=dict)
    reported_backtest: dict = Field(default_factory=dict)
    data_quality: dict = Field(default_factory=dict)
    error: str | None = None
