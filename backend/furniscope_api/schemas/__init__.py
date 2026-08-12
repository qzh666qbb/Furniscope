"""Shared HTTP envelope and pagination schemas."""

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, computed_field

T = TypeVar("T")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[Any] = Field(default_factory=list)


class SuccessEnvelope(BaseModel, Generic[T]):
    success: bool = True
    data: T
    request_id: str
    timestamp: datetime = Field(default_factory=utc_now)


class ErrorEnvelope(BaseModel):
    success: bool = False
    error: ErrorBody
    request_id: str
    timestamp: datetime = Field(default_factory=utc_now)


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    model_config = ConfigDict(extra="forbid")

    @computed_field
    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PageData(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    has_next: bool

    @classmethod
    def build(cls, *, items: list[T], total: int, params: PaginationParams) -> "PageData[T]":
        return cls(
            items=items,
            total=total,
            page=params.page,
            page_size=params.page_size,
            has_next=params.page * params.page_size < total,
        )
