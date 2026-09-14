"""AI workbench containers and persisted conversation turns."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

WorkspaceStatus = Literal["active", "archived"]
MessageRole = Literal["user", "assistant", "system"]
MessageKind = Literal["greeting", "text", "run_event", "task_chat", "error", "context"]


class WorkspaceCreateRequest(BaseModel):
    workspace_uuid: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    source: str = Field(default="node_workflow_canvas", max_length=64)
    product_id: int | None = Field(default=None, gt=0)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkspaceMessageCreateRequest(BaseModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=20000)
    message_kind: MessageKind = "text"
    client_message_id: str | None = Field(default=None, max_length=128)
    analysis_task_uuid: UUID | None = None
    evidence_refs: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class WorkspaceListItem(BaseModel):
    workspace_uuid: UUID
    job_name: str
    source: str
    workspace_status: WorkspaceStatus
    status: str
    analysis_count: int = Field(ge=0)
    product_id: int | None = None
    product_sku: str | None = None
    product_name: str | None = None
    target_country: str | None = None
    target_platform: str | None = None
    task_uuid: UUID | None = None
    report_uuid: UUID | None = None
    created_at: datetime
    updated_at: datetime


class WorkspaceMessageItem(BaseModel):
    message_uuid: UUID
    role: MessageRole
    message_kind: MessageKind
    content: str
    seq_no: int
    client_message_id: str | None = None
    analysis_task_uuid: UUID | None = None
    evidence_refs: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class WorkspaceArchiveResponse(BaseModel):
    workspace_uuid: UUID
    archived: bool
    archived_tasks: int = Field(ge=0)
