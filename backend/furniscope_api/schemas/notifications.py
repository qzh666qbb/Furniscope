from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NotificationChannelCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    channel_type: Literal["webhook", "dingtalk", "slack", "email_gateway", "sms_gateway"]
    target_url: str = Field(min_length=8, max_length=1000)
    secret_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{1,127}$")
    events: list[Literal["competitor_alert", "policy_alert"]] = Field(
        default_factory=lambda: ["competitor_alert", "policy_alert"], max_length=10)
    enabled: bool = True
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NotificationChannelUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    channel_type: Literal[
        "webhook", "dingtalk", "slack", "email_gateway", "sms_gateway"
    ] | None = None
    target_url: str | None = Field(default=None, min_length=8, max_length=1000)
    secret_env: str | None = Field(
        default=None, pattern=r"^[A-Z][A-Z0-9_]{1,127}$"
    )
    events: list[Literal["competitor_alert", "policy_alert"]] | None = Field(
        default=None, max_length=10
    )
    enabled: bool | None = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NotificationChannelItem(BaseModel):
    channel_id: int
    name: str
    channel_type: str
    target_url: str
    secret_env: str | None = None
    events: list[str] = Field(default_factory=list)
    enabled: bool
    created_at: datetime | None = None


class NotificationTestResult(BaseModel):
    event_id: int
    channel_id: int
    status: str


class NotificationEventItem(BaseModel):
    event_id: int
    channel_id: int
    channel_name: str
    channel_type: str
    event_type: str
    title: str
    content: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    attempts: int
    last_error: str | None = None
    created_at: datetime
    delivered_at: datetime | None = None
