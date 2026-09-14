"""Enterprise capability profile schemas (data dictionary V3 section 4)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CapabilityType = Literal[
    "category", "material", "process", "customization",
    "packaging", "certification", "delivery", "equipment",
]
CapabilityAvailability = Literal["yes", "no", "unknown"]


class EnterpriseConstraint(BaseModel):
    """Cost/delivery constraint item stored in enterprise_profiles.constraints."""

    constraint_type: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=32)
    value: Any
    unit: str | None = Field(default=None, max_length=32)
    hardness: str = Field(default="soft", max_length=16)
    effective_at: str | None = None
    expires_at: str | None = None
    sensitivity_level: str = Field(default="normal", max_length=16)
    model_config = ConfigDict(extra="forbid")


class EnterpriseProfilePayload(BaseModel):
    business_model: list[str] = Field(default_factory=list, max_length=10)
    primary_categories: list[str] = Field(default_factory=list, max_length=50)
    export_markets: list[str] = Field(default_factory=list, max_length=100)
    sales_channels: list[str] = Field(default_factory=list, max_length=20)
    annual_capacity_note: str | None = Field(default=None, max_length=5000)
    constraints: list[EnterpriseConstraint] = Field(default_factory=list, max_length=50)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CapabilityItem(BaseModel):
    capability_type: CapabilityType
    capability_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    capability_name: str = Field(min_length=1, max_length=200)
    availability: CapabilityAvailability = "yes"
    min_value: float | None = None
    max_value: float | None = None
    unit: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=2000)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def check_range(self) -> "CapabilityItem":
        if self.min_value is not None and self.max_value is not None and self.max_value < self.min_value:
            raise ValueError("max_value must be greater than or equal to min_value")
        if (self.min_value is not None or self.max_value is not None) and not self.unit:
            raise ValueError("unit is required when min_value or max_value is provided")
        return self


class EnterpriseProfileSaveRequest(BaseModel):
    profile: EnterpriseProfilePayload
    capabilities: list[CapabilityItem] = Field(default_factory=list, max_length=300)
    model_config = ConfigDict(extra="forbid")


class CapabilityRecord(CapabilityItem):
    source_type: str
    confidence: float
    updated_at: datetime


class EnterpriseProfileRecord(EnterpriseProfilePayload):
    profile_version: int = Field(ge=1)
    profile_completeness: float
    confirmed_by: int | None
    confirmed_at: datetime | None
    updated_at: datetime


class EnterpriseCapabilityProfileResponse(BaseModel):
    profile: EnterpriseProfileRecord | None
    capabilities: list[CapabilityRecord]
