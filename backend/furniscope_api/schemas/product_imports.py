"""Product catalog import API schemas."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProductImportDetectedField(BaseModel):
    field_code: str
    title: str
    source_header: str
    confidence: int = Field(ge=0, le=100)


class ProductImportMappingIssue(BaseModel):
    field_code: str
    title: str
    kind: Literal["missing_required", "ambiguous", "type_conflict"]
    required: bool
    current_header: str | None = None
    candidates: list[str] = Field(default_factory=list)
    message: str


class ProductImportValueIssue(BaseModel):
    field_code: str
    title: str
    source_values: list[str]
    allowed_values: list[str]
    message: str


class ProductImportInspection(BaseModel):
    original_filename: str
    sheet_name: str
    available_sheets: list[str]
    header_row: int = Field(ge=1, le=100)
    headers: list[str]
    total_rows: int = Field(ge=0)
    field_mapping: dict[str, str]
    detected_fields: list[ProductImportDetectedField]
    mapping_issues: list[ProductImportMappingIssue]
    value_issues: list[ProductImportValueIssue]
    unit_mapping: dict[str, str]
    dictionary_mapping: dict[str, dict[str, str]]
    ignored_columns: list[str]
    warnings: list[str]


class ProductImportRow(BaseModel):
    source_row_number: int
    source_values: dict[str, Any]
    normalized_values: dict[str, Any]
    validation_errors: list[dict[str, str]]
    validation_warnings: list[dict[str, str]]
    planned_action: Literal["create", "update", "skip", "invalid"]
    is_user_edited: bool
    imported_product_id: int | None = None


class ProductImportJob(BaseModel):
    job_uuid: str
    original_filename: str
    source_sha256: str
    template_version: str | None
    sheet_name: str
    available_sheets: list[str]
    header_row: int
    field_mapping: dict[str, str]
    unit_mapping: dict[str, str]
    dictionary_mapping: dict[str, dict[str, str]]
    import_mode: Literal["create_only", "upsert"]
    status: Literal[
        "preflighting", "ready", "blocked", "importing",
        "completed", "failed", "cancelled",
    ]
    total_rows: int
    valid_rows: int
    error_rows: int
    warning_rows: int
    imported_rows: int
    preview_sha256: str | None
    schema_snapshot: dict[str, Any]
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    committed_at: datetime | None
    rows: list[ProductImportRow] = Field(default_factory=list)


class ProductImportRowPatch(BaseModel):
    normalized_values: dict[str, Any]
    model_config = ConfigDict(extra="forbid")


class ProductImportCommitRequest(BaseModel):
    preview_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_config = ConfigDict(extra="forbid")


class ProductImportCommitResponse(BaseModel):
    job_uuid: str
    status: Literal["completed"]
    imported_rows: int
    created_rows: int
    updated_rows: int
    skipped_rows: int
    alias_rows: int
    committed_at: datetime
