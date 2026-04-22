"""Pydantic models for API jobs (Airtable api_jobs table)."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class WorkflowKind(str, Enum):
    SEARCH = "search"
    MATCH_CHECK_A = "match_check_a"
    MATCH_CHECK_B = "match_check_b"


class Job(BaseModel):
    """Full job row as stored in Airtable."""

    model_config = ConfigDict(use_enum_values=True)

    job_id: UUID
    api_key_record_id: str | None = None  # Airtable rec... of linked api_keys row
    workflow_kind: WorkflowKind
    status: JobStatus = JobStatus.QUEUED
    request_payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    callback_url: str | None = None
    idempotency_key: str | None = None
    n8n_execution_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class JobCreate(BaseModel):
    """What partners POST when creating a job. Shape is workflow-specific but
    we keep a loose `payload` dict here; each endpoint validates its own shape.
    """

    payload: dict[str, Any]
    callback_url: str | None = Field(
        default=None,
        description="Optional HTTPS URL to POST the final result to when done.",
    )


class JobAccepted(BaseModel):
    """202 response body."""

    job_id: UUID
    status: JobStatus
    created_at: datetime


class JobView(BaseModel):
    """Public-facing job representation. Hides internal Airtable record IDs."""

    model_config = ConfigDict(use_enum_values=True)

    job_id: UUID
    status: JobStatus
    workflow_kind: WorkflowKind
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None
