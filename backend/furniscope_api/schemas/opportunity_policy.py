"""Explicit, versioned business preferences; not a learned ranking model."""

import math
from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from furniscope_agent.enterprise_decision import DEFAULT_WEIGHTS
from .enterprise import CapabilityType


class RequiredCapability(BaseModel):
    capability_type: CapabilityType
    capability_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    taxonomy_code: Literal["comfort", "durability", "assembly", "dimension", "material",
                           "packaging", "odor", "appearance"] | None = None
    model_config = ConfigDict(extra="forbid")


class OpportunityPolicySave(BaseModel):
    expected_version: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=80)
    objective: Literal["balanced", "growth", "profit", "custom"]
    weights: dict[str, float]
    fit_strength: float = Field(ge=0, le=1, allow_inf_nan=False)
    required_capabilities: list[RequiredCapability] = Field(default_factory=list, max_length=50)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_weights(self):
        if (set(self.weights) != set(DEFAULT_WEIGHTS)
                or any(not math.isfinite(w) or w < 0 or w > 1 for w in self.weights.values())
                or not math.isclose(sum(self.weights.values()), 1.0, abs_tol=1e-6)):
            raise ValueError("五项权重须为非负有限数值，合计为1")
        return self


class OpportunityFeedbackSave(BaseModel):
    expected_revision: int = Field(ge=0)
    status: Literal["accepted", "rejected", "pending_validation"]
    reason: str = Field(min_length=1, max_length=2000)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReportedFinancials(BaseModel):
    revenue: float = Field(ge=0, allow_inf_nan=False)
    cost: float = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    basis: str = Field(min_length=1, max_length=2000)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReportedOperationalMetrics(BaseModel):
    sample_units: int = Field(ge=0)
    production_units: int = Field(ge=0)
    sold_units: int = Field(ge=0)
    returned_units: int = Field(ge=0)
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_funnel(self):
        if self.sold_units > self.production_units:
            raise ValueError("观察销量不能超过投产数量")
        if self.returned_units > self.sold_units:
            raise ValueError("退货数量不能超过观察销量")
        return self


class OpportunityOutcomeSave(BaseModel):
    expected_revision: int = Field(ge=0)
    accepted_feedback_id: int = Field(gt=0)
    status: Literal["planned", "in_progress", "completed", "abandoned"]
    implementation_start: date | None = None
    implementation_end: date | None = None
    observation_start: date | None = None
    observation_end: date | None = None
    result_label: Literal["achieved", "not_achieved", "inconclusive"] | None = None
    evidence: str = Field(min_length=1, max_length=4000)
    operational_metrics: ReportedOperationalMetrics | None = None
    financials: ReportedFinancials | None = None
    data_version_uuid: UUID | None = None
    source_sku: str | None = Field(default=None, min_length=1, max_length=128)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @model_validator(mode="after")
    def validate_observation(self):
        today = datetime.now(timezone.utc).date()
        if any(day and day > today for day in (
                self.implementation_start, self.implementation_end,
                self.observation_start, self.observation_end)):
            raise ValueError("实际实施及观察日期不能在未来")
        if self.implementation_end and (
                not self.implementation_start or self.implementation_end < self.implementation_start):
            raise ValueError("实施结束日期不能早于开始日期")
        if self.status in {"in_progress", "completed"} and not self.implementation_start:
            raise ValueError("实施中或已完成须填写实际开始日期")
        if self.status == "planned" and any((self.implementation_start, self.implementation_end,
                                            self.observation_start, self.observation_end)):
            raise ValueError("计划中尚无实际实施和观察日期")
        if self.status == "in_progress" and self.implementation_end:
            raise ValueError("实施中不能填写结束日期")
        if self.observation_start or self.observation_end:
            if not (self.observation_start and self.observation_end and self.implementation_start
                    and self.implementation_start <= self.observation_start <= self.observation_end):
                raise ValueError("观察区间须完整，且不能早于实施开始")
        if self.status == "completed" and not (self.implementation_end and self.observation_end):
            raise ValueError("完成时须填写实施结束日期及观察区间")
        if self.result_label and self.status != "completed":
            raise ValueError("结果标签仅用于已完成且有观察依据的实施")
        if self.operational_metrics and self.status == "planned":
            raise ValueError("计划中不能填写实际经营指标")
        if (self.operational_metrics or self.financials or self.data_version_uuid) and not self.observation_end:
            raise ValueError("经营数据须有完整观察区间")
        if bool(self.data_version_uuid) != bool(self.source_sku):
            raise ValueError("销量版本和来源SKU须同时填写")
        return self
