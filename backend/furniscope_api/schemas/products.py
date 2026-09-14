"""Product API schemas defined by API V3."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProductCreateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    category_code: str = Field(min_length=1, max_length=100)
    description: str | None = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class ProductCreateResponse(BaseModel):
    product_id: int
    sku: str
    name: str
    category_code: str
    analysis_status: str
    current_profile_version_id: int | None


class ProductListItem(ProductCreateResponse):
    created_at: datetime
    updated_at: datetime
    has_conflicts: bool = False
    moq: str | None = None
    factory_price: str | None = None


class ProductAttribute(BaseModel):
    attribute_code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    attribute_name: str | None = Field(default=None, max_length=200)
    value_type: str | None = None
    attribute_value: Any
    unit: str | None = Field(default=None, max_length=32)
    source_type: str = "user_input"
    source_locator: dict[str, Any] | None = None
    confidence: float = Field(default=1, ge=0, le=1)
    confirmation_status: str = "unconfirmed"
    model_config = ConfigDict(extra="forbid")


class ProductUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    analysis_status: str | None = None
    attributes: list[ProductAttribute] | None = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProductDetail(BaseModel):
    product_id: int
    sku: str
    name: str
    category_code: str
    description: str | None
    analysis_status: str
    current_profile_version_id: int | None
    profile_version: int | None
    completeness_score: float
    source_summary: dict[str, Any]
    attributes: list[ProductAttribute]


class ProductUpdateResponse(BaseModel):
    product_id: int
    sku: str
    name: str
    category_code: str
    description: str | None
    analysis_status: str
    profile_version_id: int | None
    resource_version: str


class ProductProfileConfirmRequest(BaseModel):
    profile_version_id: int
    confirmed_attribute_codes: list[str] = Field(min_length=1)
    model_config = ConfigDict(extra="forbid")


class ProductProfileConfirmResponse(BaseModel):
    product_id: int
    profile_version_id: int
    status: str
    completeness_score: float
    confirmed_at: datetime
