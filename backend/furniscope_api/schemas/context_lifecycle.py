"""Contracts for conversation, memory, context, citation, and knowledge resources."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemoryScope = Literal["workspace", "user", "tenant"]
MemoryStatus = Literal["candidate", "confirmed", "superseded", "archived", "invalidated"]
MemorySensitivity = Literal["internal", "confidential", "restricted"]
TurnMemoryMode = Literal["policy", "temporary", "workspace", "user"]


class CustomerMemoryItem(BaseModel):
    memory_uuid: UUID
    memory_type: str
    value: dict[str, Any] = Field(default_factory=dict)
    scope: MemoryScope
    status: MemoryStatus
    confidence: float = Field(ge=0, le=1)
    workspace_uuid: UUID | None = None
    source_message_uuid: UUID | None = None
    supersedes_memory_uuid: UUID | None = None
    effective_at: datetime
    expires_at: datetime | None = None
    confirmed_by: int | None = None
    invalidated_at: datetime | None = None
    invalidation_reason: str | None = None
    sensitivity: MemorySensitivity = "confidential"
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None = None
    archived_at: datetime | None = None


class CustomerMemoryPatchRequest(BaseModel):
    value: dict[str, Any] | None = None
    scope: MemoryScope | None = None
    workspace_uuid: UUID | None = None
    expires_at: datetime | None = None
    sensitivity: MemorySensitivity | None = None
    model_config = ConfigDict(extra="forbid")


class CustomerMemoryBatchConfirmRequest(BaseModel):
    memory_uuids: list[UUID] = Field(min_length=1, max_length=100)
    model_config = ConfigDict(extra="forbid")


class CustomerMemoryPolicy(BaseModel):
    auto_extract: bool = False
    confirmation_required: bool = True
    default_scope: MemoryScope = "workspace"
    retention_days: int = Field(default=365, ge=1, le=3650)
    allowed_types: list[str] = Field(
        default_factory=lambda: [
            "target_market",
            "budget",
            "unit_cost_limit",
            "preferred_channel",
            "customer_preference",
        ],
        min_length=1,
        max_length=100,
    )

    model_config = ConfigDict(extra="forbid")


class CitationItem(BaseModel):
    citation_uuid: UUID
    source_type: str
    source_id: str
    source_version: str | None = None
    label: str
    locator: dict[str, Any] = Field(default_factory=dict)
    excerpt: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)


class ContextSourceItem(BaseModel):
    type: str
    source_id: str
    label: str
    version: str | int | None = None
    locator: dict[str, Any] = Field(default_factory=dict)
    estimated_tokens: int = Field(default=0, ge=0)
    priority: int = Field(default=0, ge=0, le=100)
    trust_level: Literal[
        "authoritative",
        "confirmed",
        "historical",
        "untrusted",
        "restricted",
    ] = "historical"


class ContextConflictItem(BaseModel):
    field: str
    values: list[dict[str, Any]] = Field(default_factory=list)
    selected_source: str
    resolution: str


class ConversationStateItem(BaseModel):
    state_uuid: UUID | None = None
    revision: int = Field(default=0, ge=0)
    current_product_id: int | None = None
    current_product_label: str | None = None
    current_market: str | None = None
    compared_markets: list[str] = Field(default_factory=list)
    current_dataset_id: int | None = None
    current_task_uuid: UUID | None = None
    current_analysis_stage: str | None = None
    pending_confirmation: dict[str, Any] | None = None
    last_user_intent: str | None = None
    resolved_references: list[dict[str, Any]] = Field(default_factory=list)


class RetrievalPolicy(BaseModel):
    history_turn_limit: int = Field(default=12, ge=0, le=50)
    knowledge_top_k: int = Field(default=8, ge=0, le=50)
    max_context_tokens: int = Field(default=12000, ge=1000, le=100000)
    include_enterprise_profile: bool = True
    include_product_profile: bool = True

    model_config = ConfigDict(extra="forbid")


class WorkspaceContextConfig(BaseModel):
    product_id: int | None = Field(default=None, gt=0)
    dataset_ids: list[int] = Field(default_factory=list, max_length=20)
    knowledge_base_uuids: list[UUID] = Field(default_factory=list, max_length=20)
    memory_scope: list[MemoryScope] = Field(default_factory=lambda: ["workspace", "user"])
    task_uuids: list[UUID] = Field(default_factory=list, max_length=20)
    retrieval_policy: RetrievalPolicy = Field(default_factory=RetrievalPolicy)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def unique_values(self) -> "WorkspaceContextConfig":
        for name in ("dataset_ids", "knowledge_base_uuids", "memory_scope", "task_uuids"):
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self


class WorkspaceContextResponse(WorkspaceContextConfig):
    context_uuid: UUID | None = None
    revision: int = Field(default=0, ge=0)


class ContextPreviewResponse(BaseModel):
    sources: list[ContextSourceItem] = Field(default_factory=list)
    estimated_tokens: int = Field(ge=0)
    truncated_sources: list[ContextSourceItem] = Field(default_factory=list)
    resolved_state: ConversationStateItem = Field(default_factory=ConversationStateItem)
    conflicts: list[ContextConflictItem] = Field(default_factory=list)
    resolution_log: list[dict[str, Any]] = Field(default_factory=list)


class ContextPreviewRequest(BaseModel):
    query: str | None = Field(default=None, max_length=2000)
    configuration: WorkspaceContextConfig | None = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TurnCreateRequest(BaseModel):
    client_turn_id: UUID
    question: str = Field(min_length=1, max_length=2000)
    product_id: int | None = Field(default=None, gt=0)
    dataset_id: int | None = Field(default=None, gt=0)
    task_uuid: UUID | None = None
    memory_mode: TurnMemoryMode = "policy"
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolExecutionItem(BaseModel):
    tool: Literal["data_query", "rag", "workflow", "forecast"]
    status: Literal["succeeded", "rejected", "needs_input"]
    data: dict[str, Any] = Field(default_factory=dict)


class TurnResponse(BaseModel):
    turn_uuid: UUID
    user_message_uuid: UUID
    assistant_message_uuid: UUID
    answer: str
    citations: list[CitationItem] = Field(default_factory=list)
    memory_candidates: list[CustomerMemoryItem] = Field(default_factory=list)
    context_sources: list[ContextSourceItem] = Field(default_factory=list)
    suggested_actions: list[dict[str, Any]] = Field(default_factory=list)
    tool_results: list[ToolExecutionItem] = Field(default_factory=list)
    context_snapshot_uuid: UUID
    resolved_state: ConversationStateItem = Field(default_factory=ConversationStateItem)
    context_conflicts: list[ContextConflictItem] = Field(default_factory=list)
    resolution_log: list[dict[str, Any]] = Field(default_factory=list)


class TurnListItem(BaseModel):
    turn_uuid: UUID
    client_turn_id: UUID
    sequence_no: int = Field(ge=1)
    status: Literal["pending", "completed", "failed", "cancelled"]
    task_uuid: UUID | None = None
    regenerated_from_turn_uuid: UUID | None = None
    user_message_uuid: UUID | None = None
    assistant_message_uuid: UUID | None = None
    question: str
    answer: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class KnowledgeBaseCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["tenant", "user"] = "tenant"
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class KnowledgeBasePatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["tenant", "user"] | None = None
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class KnowledgeBaseItem(BaseModel):
    knowledge_base_uuid: UUID
    name: str
    description: str | None = None
    status: Literal["active", "archived"]
    visibility: Literal["tenant", "user"] = "tenant"
    document_count: int = Field(default=0, ge=0)
    ready_document_count: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentItem(BaseModel):
    document_uuid: UUID
    knowledge_base_uuid: UUID
    filename: str
    mime_type: str
    document_type: str | None = None
    status: Literal["uploaded", "parsing", "chunking", "embedding", "ready", "failed", "deleted"]
    version: int = Field(ge=1)
    sha256: str
    byte_size: int = Field(gt=0)
    index_job_uuid: UUID | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentVersionItem(BaseModel):
    document_version_uuid: UUID
    version: int = Field(ge=1)
    sha256: str
    byte_size: int = Field(gt=0)
    extraction_method: str | None = None
    page_or_sheet_count: int | None = Field(default=None, gt=0)
    index_job_uuid: UUID | None = None
    index_status: Literal["queued", "running", "succeeded", "failed", "cancelled"] | None = None
    error_code: str | None = None
    error_message: str | None = None
    is_current: bool = False
    created_at: datetime


class KnowledgeDocumentPreview(BaseModel):
    document_uuid: UUID
    filename: str
    mime_type: str
    version: int = Field(ge=1)
    extraction_method: str | None = None
    page_or_sheet_count: int | None = Field(default=None, gt=0)
    text: str | None = None
    available: bool = False
    truncated: bool = False


class KnowledgeSearchFilters(BaseModel):
    document_type: list[str] = Field(default_factory=list, max_length=20)
    model_config = ConfigDict(extra="forbid")


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    knowledge_base_uuids: list[UUID] = Field(min_length=1, max_length=20)
    workspace_uuid: UUID | None = None
    top_k: int = Field(default=8, ge=1, le=50)
    filters: KnowledgeSearchFilters = Field(default_factory=KnowledgeSearchFilters)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class KnowledgeSearchMatch(BaseModel):
    citation_uuid: UUID
    document_uuid: UUID
    document_name: str
    page: int | None = None
    chunk_text: str
    score: float = Field(ge=0, le=1)
    document_version: int = Field(ge=1)


class KnowledgeSearchResponse(BaseModel):
    matches: list[KnowledgeSearchMatch] = Field(default_factory=list)
    relevance_threshold: float = Field(default=0.35, ge=0, le=1)
    refused: bool = False
