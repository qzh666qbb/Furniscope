"""Controlled semantic-query contracts for deterministic enterprise metrics."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

DataMetric = Literal[
    "sales_units",
    "average_daily_sales",
    "sales_revenue",
    "average_selling_price",
    "active_days",
    "sku_count",
    "site_count",
    "inventory_units",
    "average_inventory",
    "stockout_days",
]
DataDimension = Literal["sku", "site"]
DataGrain = Literal["total", "daily", "weekly", "monthly"]


class DataQueryFilters(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    relative_days: int | None = Field(default=None, ge=1, le=3660)
    skus: list[str] = Field(default_factory=list, max_length=50)
    sites: list[str] = Field(default_factory=list, max_length=20)
    statuses: list[
        Literal["active", "out_of_stock", "discontinued", "unknown"]
    ] = Field(default_factory=list, max_length=4)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_filters(self) -> "DataQueryFilters":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not be after date_to")
        if self.relative_days and (self.date_from or self.date_to):
            raise ValueError("relative_days cannot be combined with explicit dates")
        self.skus = list(dict.fromkeys(item.upper() for item in self.skus if item))
        self.sites = list(dict.fromkeys(item.upper() for item in self.sites if item))
        self.statuses = list(dict.fromkeys(self.statuses))
        return self


class DataQueryPlan(BaseModel):
    metrics: list[DataMetric] = Field(min_length=1, max_length=4)
    grain: DataGrain = "total"
    group_by: list[DataDimension] = Field(default_factory=list, max_length=2)
    filters: DataQueryFilters = Field(default_factory=DataQueryFilters)
    data_version_uuid: UUID | None = None
    limit: int = Field(default=100, ge=1, le=500)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_plan(self) -> "DataQueryPlan":
        self.metrics = list(dict.fromkeys(self.metrics))
        self.group_by = list(dict.fromkeys(self.group_by))
        sales = {
            "sales_units",
            "average_daily_sales",
            "sales_revenue",
            "average_selling_price",
            "active_days",
            "sku_count",
            "site_count",
        }
        families = {"sales" if metric in sales else "inventory" for metric in self.metrics}
        if len(families) != 1:
            raise ValueError("one query cannot mix sales and inventory metric families")
        return self


class DataQueryExecuteRequest(BaseModel):
    plan: DataQueryPlan
    workspace_uuid: UUID | None = None
    model_config = ConfigDict(extra="forbid")


class DataMetricItem(BaseModel):
    metric_code: DataMetric
    display_name: str
    description: str
    source_kind: Literal["sales", "inventory"]
    value_type: Literal["decimal", "integer", "currency"]
    unit: str | None = None


class DataQuerySourceVersion(BaseModel):
    version_uuid: UUID
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_kind: Literal["sales", "inventory"]
    filename: str
    confirmed_at: datetime


class DataQueryResult(BaseModel):
    query_uuid: UUID
    plan: DataQueryPlan
    resolved_filters: dict[str, Any] = Field(default_factory=dict)
    source_version: DataQuerySourceVersion
    columns: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    row_count: int = Field(ge=0, le=500)
    limited: bool = False
    limitations: list[str] = Field(default_factory=list)
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DataQueryAuditItem(DataQueryResult):
    requested_by: int
    duration_ms: int = Field(ge=0)
    created_at: datetime
