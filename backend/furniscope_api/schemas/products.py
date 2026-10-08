"""Product catalog and profile API schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ProductCategory = Literal["sofa", "chair", "table", "bed", "storage", "other"]
ProductLifecycle = Literal["concept", "sample", "active", "discontinued"]


class ProductCreateRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    category_code: ProductCategory
    description: str | None = None
    lifecycle_status: ProductLifecycle = "active"
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class ProductCreateResponse(BaseModel):
    product_id: int
    sku: str
    name: str
    category_code: str
    lifecycle_status: str
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
    sku: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category_code: ProductCategory | None = None
    lifecycle_status: ProductLifecycle | None = None
    description: str | None = None
    analysis_status: str | None = None
    attributes: list[ProductAttribute] | None = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProductDetail(BaseModel):
    product_id: int
    sku: str
    name: str
    category_code: str
    lifecycle_status: str
    description: str | None
    analysis_status: str
    current_profile_version_id: int | None
    profile_version: int | None
    profile_version_id: int | None = None
    profile_status: str | None = None
    completeness_score: float
    source_summary: dict[str, Any]
    attributes: list[ProductAttribute]
    fact_suggestions: list[dict[str, Any]] = Field(default_factory=list)


class ProductUpdateResponse(BaseModel):
    product_id: int
    sku: str
    name: str
    category_code: str
    lifecycle_status: str
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


class ProductArchiveResponse(BaseModel):
    product_id: int
    sku: str
    lifecycle_status: Literal["discontinued"]
    analysis_status: Literal["archived"]
    archived_at: datetime


class ProductRelationInput(BaseModel):
    group_type: Literal["spu", "variant", "bundle", "bom"]
    group_code: str = Field(min_length=1, max_length=100)
    group_name: str = Field(min_length=1, max_length=200)
    member_role: Literal["parent", "variant", "component", "item"]
    quantity: Decimal = Field(default=Decimal("1"), gt=0, max_digits=18, decimal_places=6)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProductRelationsUpdateRequest(BaseModel):
    items: list[ProductRelationInput] = Field(max_length=200)
    model_config = ConfigDict(extra="forbid")


class ProductRelationItem(ProductRelationInput):
    group_id: int


class ProductRelationsResponse(BaseModel):
    product_id: int
    items: list[ProductRelationItem]


class InventorySiteSummary(BaseModel):
    site: str
    inventory_units: Decimal
    as_of_date: date
    source_version_uuid: str


class InventoryImportStatus(BaseModel):
    status: Literal["none", "uploaded", "previewed", "confirmed"]
    filename: str | None = None
    version_uuid: str | None = None
    updated_at: datetime | None = None


class ProductInventorySummary(BaseModel):
    product_id: int
    sku: str
    is_realtime: Literal[False] = False
    source: Literal["inventory_facts_daily"] = "inventory_facts_daily"
    as_of_date: date | None
    total_inventory_units: Decimal
    sites: list[InventorySiteSummary]
    import_status: InventoryImportStatus
