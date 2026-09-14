"""Market dataset read API schemas defined by API V3."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FieldMappingItem(BaseModel):
    source_field: str = Field(min_length=1, max_length=200)
    target_entity: str = Field(min_length=1, max_length=100)
    target_field: str = Field(min_length=1, max_length=100)
    transform_rule: str | None = None
    mapping_status: str
    model_config = ConfigDict(extra="forbid")


class DatasetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    platform: str
    market_country: str = Field(pattern=r"^[A-Z]{2}$")
    category_code: str
    data_start_date: date | None = None
    data_end_date: date
    source_type: str
    source_name: str = Field(min_length=1, max_length=200)
    authorization_reference: str | None = None
    field_mapping: list[FieldMappingItem] = Field(default_factory=list)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DatasetCreateResponse(BaseModel):
    dataset_id: int
    name: str
    status: str
    platform: str
    market_country: str
    category_code: str
    version_no: int


class DatasetImportAccepted(BaseModel):
    dataset_id: int
    status: str
    accepted_at: datetime


class DatasetListItem(BaseModel):
    dataset_id: int
    name: str
    platform: str
    market_country: str
    category_code: str
    source_type: str
    source_name: str
    status: str
    listing_count: int
    review_count: int
    valid_review_count: int
    quality_score: float
    data_start_date: date | None
    data_end_date: date
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version_no: int | None = None
    quality_report: dict[str, Any] = Field(default_factory=dict)


class DatasetDetail(DatasetListItem):
    version_no: int
    marketplace_code: str | None
    authorization_reference: str | None
    field_mapping: list[dict[str, Any]]
    quality_report: dict[str, Any]
    limitations: list[Any]


class DatasetListingPreview(BaseModel):
    listing_id: int
    platform_listing_id: str
    title: str
    brand: str | None
    category_code: str
    currency: str
    sale_price: float
    list_price: float | None = None
    rating: float | None
    review_count: int | None
    captured_at: datetime
    first_available_date: date | None
    market_country: str | None = None
    normalized_attributes: dict[str, Any]


class DatasetReviewPreview(BaseModel):
    review_id: int
    platform_review_id: str
    listing_title: str
    rating: float | None
    content_original: str
    language_code: str
    reviewer_location: str | None = None
    sentiment: str | None = None
    reviewed_at: datetime | None
    verified_purchase: bool | None
    is_valid: bool
    invalid_reason: str | None


class DatasetBoundTask(BaseModel):
    task_uuid: str
    job_name: str
    status: str
    product_sku: str | None = None
    product_name: str | None = None
    report_uuid: str | None = None
    created_at: datetime | None = None
