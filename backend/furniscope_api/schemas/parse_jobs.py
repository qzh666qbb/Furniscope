"""Product parse job API projections."""

from typing import Any
from pydantic import BaseModel


class ParseAccepted(BaseModel):
    parse_job_id: str
    product_id: int
    status: str
    progress_percent: float
    current_stage: str


class ParseSummary(BaseModel):
    file_count: int
    succeeded_file_count: int
    failed_file_count: int


class ParseFileResult(BaseModel):
    file_name: str
    security_status: str
    parse_status: str
    error_code: str | None = None
    error_message: str | None = None


class ParseJobDetail(BaseModel):
    parse_job_id: str
    product_id: int
    status: str
    progress_percent: float
    current_stage: str
    summary: ParseSummary
    file_results: list[ParseFileResult]
    retryable: bool
    failure_code: str | None
    failure_message: str | None
