from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WatchCreateRequest(BaseModel):
    asin: str = Field(min_length=4, max_length=40)
    market_country: str = Field(pattern=r"^[A-Z]{2}$")
    platform: str = "amazon"
    dataset_id: int | None = None
    product_id: int | None = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WatchFromDatasetRequest(BaseModel):
    dataset_id: int
    product_id: int | None = None
    limit: int = Field(default=8, ge=1, le=20)
    model_config = ConfigDict(extra="forbid")


class WatchFromProductRequest(BaseModel):
    product_id: int
    dataset_id: int | None = None
    limit: int = Field(default=8, ge=1, le=20)
    model_config = ConfigDict(extra="forbid")


class WatchPatchRequest(BaseModel):
    compare_selected: bool | None = None
    product_id: int | None = None
    model_config = ConfigDict(extra="forbid")


class CatalogListingItem(BaseModel):
    listing_id: int
    dataset_id: int
    dataset_name: str
    asin: str
    title: str | None = None
    brand: str | None = None
    category_code: str | None = None
    sale_price: float | None = None
    currency: str | None = None
    rating: float | None = None
    review_count: int | None = None
    market_country: str | None = None
    platform: str | None = None
    watched: bool = False
    watch_id: int | None = None


class WatchFromUrlRequest(BaseModel):
    endpoint_url: str = Field(min_length=8, max_length=1000)
    product_id: int | None = None
    dataset_id: int | None = None
    market_country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WatchItem(BaseModel):
    watch_id: int
    asin: str
    title: str | None = None
    platform: str
    market_country: str
    dataset_id: int | None = None
    product_id: int | None = None
    product_sku: str | None = None
    product_name: str | None = None
    match_score: float | None = None
    compare_selected: bool = True
    category_code: str | None = None
    status: str
    sale_price: float | None = None
    list_price: float | None = None
    previous_price: float | None = None
    price_delta: float | None = None
    currency: str | None = None
    promo_label: str | None = None
    captured_at: datetime | None = None
    rating: float | None = None
    review_count: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SnapshotItem(BaseModel):
    snapshot_id: int
    captured_at: datetime
    source: str
    title: str | None = None
    sale_price: float | None = None
    list_price: float | None = None
    currency: str | None = None
    rating: float | None = None
    review_count: int | None = None
    image_urls: list[Any] = Field(default_factory=list)
    bullet_points: list[Any] = Field(default_factory=list)
    promo_label: str | None = None
    first_available_date: date | None = None
    fingerprint: str


class PricePoint(BaseModel):
    captured_at: datetime
    sale_price: float | None = None
    list_price: float | None = None
    currency: str | None = None
    promo_label: str | None = None


class AlertItem(BaseModel):
    alert_id: int
    watch_id: int
    asin: str
    title: str | None = None
    change_type: str
    change_label: str
    summary: str
    before_value: Any = None
    after_value: Any = None
    captured_at: datetime
    is_read: bool


class TrackingOverview(BaseModel):
    watch_count: int
    unread_alerts: int
    watches: list[WatchItem]
    alerts: list[AlertItem]
    rhythm: dict[str, Any]
    selected_watch_id: int | None = None
    price_series: list[PricePoint] = Field(default_factory=list)
    snapshots: list[SnapshotItem] = Field(default_factory=list)
