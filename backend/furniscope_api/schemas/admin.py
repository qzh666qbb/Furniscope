from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class EnterpriseUserCreate(BaseModel):
    tenant_code: str = Field(pattern=r"^[A-Z0-9_]{2,32}$")
    enterprise_name: str = Field(min_length=1, max_length=200)
    contact_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    initial_password: str = Field(min_length=12, max_length=1024)
    entitlements: list[Literal["sales_forecast", "market_analysis"]] = Field(
        default_factory=lambda: ["sales_forecast"], max_length=8
    )


class EnterpriseUserUpdate(BaseModel):
    enterprise_name: str | None = Field(default=None, min_length=1, max_length=200)
    tenant_status: Literal["trial", "active", "suspended", "closed"] | None = None
    user_status: Literal["invited", "active", "disabled", "locked"] | None = None
    entitlements: list[Literal["sales_forecast", "market_analysis"]] | None = Field(
        default=None, max_length=8
    )


class EnterprisePasswordReset(BaseModel):
    new_password: str = Field(min_length=12, max_length=1024)


class RegistrationApproval(BaseModel):
    tenant_code: str = Field(pattern=r"^[A-Z0-9_]{2,32}$")
    entitlements: list[Literal["sales_forecast", "market_analysis"]] = Field(
        default_factory=lambda: ["sales_forecast"], max_length=8
    )


class RegistrationRejection(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class AdminUserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["invited", "active", "disabled", "locked"] | None = None
    role_code: Literal["user", "admin"] | None = None


class TenantMemberRolesUpdate(BaseModel):
    role_codes: list[Literal[
        "tenant_owner", "data_admin", "analyst", "operator", "auditor", "viewer"
    ]] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_roles(self):
        if len(set(self.role_codes)) != len(self.role_codes):
            raise ValueError("role_codes must be unique")
        return self


class TenantLegalHoldCreate(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    reference_code: str = Field(min_length=1, max_length=160)


class TenantLegalHoldRelease(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class ModelRouteUpdate(BaseModel):
    primary_model_id: str = Field(min_length=1, max_length=200)
    fallback_model_ids: list[str] = Field(default_factory=list, max_length=5)
    timeout_ms: int = Field(gt=0, le=300_000)
    max_retries: int = Field(ge=0, le=5)
    batch_size: int | None = Field(default=None, ge=1, le=1000)
    concurrency_limit: int = Field(ge=1, le=128)
    compute_config: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class PromptTemplateCreate(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,99}$")
    version: str = Field(min_length=1, max_length=64)
    task_type: Literal["vision_extract", "text_extract", "translate", "embed", "rerank", "reason", "report"]
    template_content: str = Field(min_length=1, max_length=50_000)
    output_schema: dict[str, Any] | None = None
    status: Literal["draft", "testing", "active", "retired"] = "draft"
    evaluation_set_version: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def active_requires_evaluation(self):
        if self.status == "active" and not self.evaluation_set_version:
            raise ValueError("active prompt requires evaluation_set_version")
        return self


class WorkflowRecoveryRequest(BaseModel):
    event_type: Literal["auto_retry", "safe_stop"]
    checkpoint_id: str | None = Field(default=None, max_length=128)
    stage_code: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_retry(self):
        if self.event_type == "auto_retry" and (not self.checkpoint_id or not self.stage_code):
            raise ValueError("auto_retry requires checkpoint_id and stage_code")
        forbidden = {"token", "secret", "api_key", "password"} & {key.lower() for key in self.payload}
        if forbidden:
            raise ValueError("recovery payload contains forbidden secret fields")
        return self


class TenantDataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    source_kind: Literal["sales_history", "inventory", "product_catalog", "market_data"]
    connection_type: Literal["upload", "api", "database", "object_storage"]
    secret_ref: str | None = Field(default=None, max_length=500)
    authorization_reference: str = Field(min_length=1, max_length=1000)
    safe_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_inline_secrets(self):
        forbidden = {"token", "secret", "password", "api_key", "access_key", "private_key"}
        if forbidden & {key.lower() for key in self.safe_config}:
            raise ValueError("safe_config cannot contain credentials; use secret_ref")
        return self


class TenantSkuUpsert(BaseModel):
    sku: str = Field(min_length=1, max_length=128)
    site: str = Field(min_length=2, max_length=16)
    category_code: str | None = Field(default=None, max_length=64)
    lifecycle_status: Literal["active", "out_of_stock", "discontinued", "unknown"] = "active"
    label_status: Literal["complete", "partial", "unknown"] = "unknown"
    history_weeks: int = Field(default=0, ge=0)
    model_eligible: bool = False
    source_uuid: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
