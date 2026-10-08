"""Explicit, versioned contracts for enterprise sales data."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImportRules(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mapping: dict[str, str] = Field(default_factory=dict)
    kind: Literal["sales", "inventory"] = "sales"
    grain: Literal["daily", "transactions"] = "daily"
    sales_basis: Literal["gross_units", "net_units"] = "gross_units"
    date_format: Literal["%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y"] = "%Y-%m-%d"
    default_site: str | None = Field(default=None, min_length=2, max_length=16)
    missing_dates: Literal["unknown", "zero"] = "unknown"
    complete_export_confirmed: bool = False
    warehouse_sites: dict[str, str] = Field(default_factory=dict, max_length=1000)
    order_statuses: dict[str, Literal["completed", "cancelled", "refunded"]] = Field(default_factory=dict, max_length=100)
    order_semantics_confirmed: bool = False
    duplicate_orders: Literal["error", "drop_identical"] = "error"
    default_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    price_semantics_confirmed: bool = False

    @model_validator(mode="after")
    def validate_rules(self):
        if self.missing_dates == "zero" and not self.complete_export_confirmed:
            raise ValueError("补零必须确认连续销售且导出完整")
        allowed = {"date", "sku", "site", self.kind, "status", "warehouse"}
        if self.kind == "sales":
            allowed |= {"order_id", "line_id", "order_status", "refunded_units",
                        "unit_price", "discount", "currency"}
        if set(self.mapping) - allowed:
            raise ValueError("映射包含当前数据类型不支持的字段")
        if len(set(self.mapping.values())) != len(self.mapping):
            raise ValueError("同一原始列不能映射到多个字段")
        if self.kind == "inventory" and self.grain != "daily":
            raise ValueError("库存必须是每日快照，不能累加库存明细")
        order_fields = {"order_id", "line_id", "order_status", "refunded_units"}
        if order_fields & set(self.mapping):
            if self.grain != "transactions" or not {"order_id", "line_id"} <= set(self.mapping):
                raise ValueError("订单处理须选择明细粒度并映射订单号、订单行号")
            if not self.order_semantics_confirmed:
                raise ValueError("须确认整日订单快照、取消排除及按原订单日期扣退货的口径")
        if "warehouse" in self.mapping and not self.warehouse_sites:
            raise ValueError("须明确每个仓库唯一对应的站点；共享库存保留独立站点")
        if {"unit_price", "discount"} & set(self.mapping) and not self.price_semantics_confirmed:
            raise ValueError("须确认单价为折后成交单价，折扣为0—1比例")
        return self


class ImportPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules: ImportRules
    auxiliary_versions: dict[Literal["control", "inventory"], UUID] = Field(default_factory=dict)


class ImportConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class TrainingCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_version_uuid: UUID
    mode: Literal["initial", "append", "rebuild"] = "initial"
    allow_history_overwrite: bool = False


class MappingSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mapping: dict[Literal["date", "sku", "site", "sales", "inventory", "status", "warehouse",
                          "order_id", "line_id", "order_status", "refunded_units",
                          "unit_price", "discount", "currency"], str]


class ImportTemplateSave(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    data_version_uuid: UUID
    expected_revision: int = Field(default=0, ge=0)


class ImportTemplateApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_uuid: UUID


class SkuMappingItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_sku: str = Field(min_length=1, max_length=128)
    product_sku: str = Field(min_length=1, max_length=128)


class SkuMappingSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    items: list[SkuMappingItem] = Field(max_length=10000)
