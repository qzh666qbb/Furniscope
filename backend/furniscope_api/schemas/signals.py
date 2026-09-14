from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AuthorizedSourceCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    source_kind: Literal[
        "authorized_market_json", "authorized_review_stream", "amazon_product_page",
        "amazon_review_page", "web_review_page",
    ]
    endpoint_url: str = Field(min_length=8, max_length=1000)
    platform: str = Field(default="amazon", max_length=40)
    market_country: str = Field(default="US", pattern=r"^[A-Za-z]{2}$")
    category_code: str = Field(default="sofa", max_length=80)
    dataset_id: int | None = None
    authorization_reference: str = Field(min_length=2, max_length=500)
    auth_token_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,127}$")
    schedule_minutes: int = Field(default=1440, ge=5, le=10080)
    enabled: bool = True
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AuthorizedSourceUpdateRequest(BaseModel):
    enabled: bool | None = None
    schedule_minutes: int | None = Field(default=None, ge=5, le=10080)
    model_config = ConfigDict(extra="forbid")


class AuthorizedSourceItem(BaseModel):
    source_id: int
    name: str
    source_kind: str
    endpoint_url: str
    platform: str
    market_country: str
    category_code: str
    dataset_id: int | None = None
    authorization_reference: str
    auth_token_env: str | None = None
    schedule_minutes: int
    enabled: bool
    last_fetched_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None


class CollectRunResult(BaseModel):
    run_id: int
    source_id: int
    status: str
    fetched_count: int
    inserted_count: int
    alert_count: int
    error_summary: str | None = None
    listing_asins: list[str] = Field(default_factory=list)


class CollectUrlRequest(BaseModel):
    endpoint_url: str = Field(min_length=8, max_length=1000)
    source_kind: Literal[
        "amazon_product_page", "amazon_review_page", "web_review_page", "authorized_market_json",
    ] | None = None
    dataset_id: int | None = None
    market_country: str = Field(default="US", pattern=r"^[A-Za-z]{2}$")
    category_code: str = Field(default="sofa", max_length=80)
    platform: str = Field(default="amazon", max_length=40)
    schedule_minutes: int = Field(default=1440, ge=5, le=10080)
    enabled: bool = False
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SentimentEventInput(BaseModel):
    external_id: str | None = Field(default=None, max_length=200)
    platform: str | None = Field(default=None, max_length=40)
    market_country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    asin: str | None = Field(default=None, max_length=40)
    reviewer_location: str | None = Field(default=None, max_length=80)
    language_code: str | None = Field(default=None, max_length=16)
    rating: float | None = Field(default=None, ge=0, le=5)
    content_original: str = Field(min_length=1, max_length=10000)
    reviewed_at: datetime | None = None
    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class SentimentIngestRequest(BaseModel):
    source_id: int | None = None
    authorization_reference: str | None = Field(default=None, max_length=500)
    events: list[SentimentEventInput] = Field(min_length=1, max_length=500)
    model_config = ConfigDict(extra="forbid")


class SentimentEventItem(BaseModel):
    event_id: int
    source_id: int | None = None
    external_id: str | None = None
    platform: str
    market_country: str
    asin: str | None = None
    reviewer_location: str | None = None
    language_code: str | None = None
    rating: float | None = None
    content_original: str
    sentiment: str
    sentiment_score: float | None = None
    reviewed_at: datetime | None = None
    received_at: datetime


class SentimentIngestResult(BaseModel):
    inserted_count: int
    items: list[dict[str, Any]]


class SentimentFeedItem(BaseModel):
    item_id: str
    origin: Literal["live", "dataset"]
    dataset_id: int | None = None
    dataset_name: str | None = None
    asin: str | None = None
    platform: str | None = None
    market_country: str | None = None
    rating: float | None = None
    content_original: str
    sentiment: str
    sentiment_score: float | None = None
    reviewer_location: str | None = None
    is_valid: bool = True
    occurred_at: datetime | None = None


class SentimentFeedPage(BaseModel):
    items: list[SentimentFeedItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    has_next: bool
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    live_count: int = 0
    dataset_count: int = 0


class PolicySourceCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    source_type: Literal["official_rss", "official_json"]
    source_url: str = Field(min_length=8, max_length=1000)
    market_country: str | None = Field(default=None, pattern=r"^[A-Za-z]{2}$")
    category_code: str | None = Field(default=None, max_length=80)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    authorization_reference: str = Field(min_length=2, max_length=500)
    schedule_minutes: int = Field(default=1440, ge=5, le=10080)
    enabled: bool = True
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PolicySourceUpdateRequest(BaseModel):
    enabled: bool | None = None
    schedule_minutes: int | None = Field(default=None, ge=5, le=10080)
    model_config = ConfigDict(extra="forbid")


class PolicySourceItem(BaseModel):
    source_id: int
    name: str
    source_type: str
    source_url: str
    market_country: str | None = None
    category_code: str | None = None
    keywords: list[str] = Field(default_factory=list)
    authorization_reference: str
    schedule_minutes: int
    enabled: bool
    last_fetched_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None


class PolicyFetchResult(BaseModel):
    source_id: int
    status: str
    fetched_count: int
    inserted_count: int
    error_summary: str | None = None


class PolicyAlertItem(BaseModel):
    alert_id: int
    source_id: int | None = None
    source_name: str | None = None
    external_id: str | None = None
    title: str
    summary: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    severity: str
    matched_keywords: list[str] = Field(default_factory=list)
    is_read: bool
    created_at: datetime


class MarketSignalOverview(BaseModel):
    source_count: int
    sentiment_count: int
    sentiment_24h: int
    policy_source_count: int
    unread_policy_alerts: int
