from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


JsonObject = dict[str, Any]


class SchemaModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    PENDING = "PENDING"
    DONE = "DONE"


class Employee(SchemaModel):
    id: int
    firstname: str
    lastname: str
    username: str
    teamname: str


class Ticket(SchemaModel):
    id: int
    title: str
    description: str
    priority: str
    status: TicketStatus
    customer_id: int
    customer_name: str
    tags: list[str] = Field(default_factory=list)
    sla_due_at: str | None = None
    created_at: str | None = None


class SystemInfo(SchemaModel):
    ip: str
    port: int
    username: str
    os: str
    notes: str | None = None


class CustomerSystem(SchemaModel):
    ticket_id: int
    customer_id: int
    system: SystemInfo


class StatusUpdate(SchemaModel):
    status: TicketStatus


class ActivityCreate(SchemaModel):
    ticket_id: int
    start_datetime: str
    end_datetime: str
    description: str | None = None
    summary: str | None = None
    root_cause: str | None = None
    actions_taken: str | None = None
    commands_summary: str | None = None
    validation_result: str | None = None


class ActivityDraft(ActivityCreate):
    start_datetime: str = Field(min_length=1, max_length=120)
    end_datetime: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=2_000)
    root_cause: str = Field(min_length=1, max_length=2_000)
    actions_taken: str = Field(min_length=1, max_length=4_000)
    commands_summary: str = Field(min_length=1, max_length=4_000)
    validation_result: str = Field(min_length=1, max_length=2_000)

    @field_validator(
        "start_datetime",
        "end_datetime",
        "summary",
        "root_cause",
        "actions_taken",
        "commands_summary",
        "validation_result",
        mode="before",
    )
    @classmethod
    def _require_non_empty_string(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Phoenix activity draft fields must be non-empty strings")
        return value.strip()


class Activity(ActivityCreate):
    id: int
    team_id: int
    team_name: str
    employee_id: int
    created_at: str | None = None


class HealthIntegration(SchemaModel):
    configured: bool
    reachable: bool | None = None
    error: str | None = None


class HealthResponse(SchemaModel):
    status: str
    database: HealthIntegration
    phoenix: HealthIntegration


class RunStatus(StrEnum):
    AWAITING_CONNECTION_APPROVAL = "awaiting_connection_approval"
    INVESTIGATING = "investigating"
    PLANNING = "planning"
    AWAITING_STEP_APPROVAL = "awaiting_step_approval"
    EXECUTING = "executing"
    FIXING = "fixing"
    VALIDATING = "validating"
    READY_FOR_ACTIVITY = "ready_for_activity"
    SUBMITTED = "submitted"
    ABORTED = "aborted"
    FAILED = "failed"


class StepStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    EXECUTED = "executed"
    FAILED = "failed"


class CommandExecutionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ABORTED = "aborted"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class IntegrationRequestStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    ACTIVITY_CREATED = "activity_created"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class RunCreate(SchemaModel):
    ticket_id: int = Field(gt=0)


class ConnectionApproval(SchemaModel):
    approved_by: str = Field(default="technician", min_length=1, max_length=120)


class ManualStepCreate(SchemaModel):
    command: str = Field(min_length=1, max_length=4_000)
    entered_by: str = Field(default="technician", min_length=1, max_length=120)
    purpose: str = Field(default="Manual technician command.", min_length=1, max_length=1_000)
    expected_signal: str | None = Field(default=None, max_length=1_000)
    phase: str = Field(default="diagnostic", min_length=1, max_length=80)
    timeout_s: int | None = Field(default=None, ge=1, le=600)


class CommandApproval(SchemaModel):
    approved_by: str = Field(default="technician", min_length=1, max_length=120)


class CommandEditApproval(CommandApproval):
    command: str = Field(min_length=1, max_length=4_000)
    purpose: str | None = Field(default=None, max_length=1_000)
    expected_signal: str | None = Field(default=None, max_length=1_000)
    timeout_s: int | None = Field(default=None, ge=1, le=600)


class StepRejection(SchemaModel):
    rejected_by: str = Field(default="technician", min_length=1, max_length=120)
    reason: str = Field(default="Rejected by technician.", min_length=1, max_length=1_000)


class RunRetry(SchemaModel):
    requested_by: str = Field(default="technician", min_length=1, max_length=120)
    reason: str = Field(default="Retry requested by technician.", min_length=1, max_length=1_000)


class RunAbort(SchemaModel):
    aborted_by: str = Field(default="technician", min_length=1, max_length=120)
    reason: str = Field(default="Aborted by technician.", min_length=1, max_length=1_000)


class BackupNotApplicableCreate(SchemaModel):
    source_path: str | None = Field(default=None, max_length=1_000)
    reason: str = Field(min_length=1, max_length=1_000)
    recorded_by: str = Field(default="technician", min_length=1, max_length=120)


class BackupRestoreProposalCreate(SchemaModel):
    reason: str = Field(default="Restore from recorded rollback command.", min_length=1, max_length=1_000)
    proposed_by: str = Field(default="technician", min_length=1, max_length=120)


class ProposedStep(SchemaModel):
    id: int
    run_id: int
    created_at: datetime
    updated_at: datetime | None = None
    source: str
    phase: str
    command: str
    purpose: str
    expected_signal: str | None = None
    risk_class: str
    safety_verdict: str
    safety_summary: str
    safety_notes: list[str] = Field(default_factory=list)
    status: StepStatus
    timeout_s: int
    approved_command: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejection_reason: str | None = None


class OutboxEvent(SchemaModel):
    id: int
    run_id: int | None = None
    event_type: str
    payload: JsonObject = Field(default_factory=dict)
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = 0
    available_at: datetime | None = None
    claimed_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    created_at: datetime


class IntegrationRequest(SchemaModel):
    id: int
    run_id: int
    ticket_id: int
    activity_draft_id: int | None = None
    request_type: str
    status: IntegrationRequestStatus
    activity_payload: JsonObject = Field(default_factory=dict)
    phoenix_activity_id: int | None = None
    ticket_status: TicketStatus | None = None
    attempts: int = 0
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class CommandExecution(SchemaModel):
    id: int
    run_id: int
    proposed_step_id: int
    approved_command: str
    status: CommandExecutionStatus
    target_host: str
    target_port: int
    target_username: str
    timeout_s: int
    output_limit_bytes: int
    output_truncated: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    sanitized_stdout: str = ""
    sanitized_stderr: str = ""
    error: str | None = None


class CommandOutputChunk(SchemaModel):
    id: int
    command_execution_id: int
    run_id: int
    sequence: int
    stream: str
    content: str
    redacted: bool = False
    created_at: datetime


class RedactionEvent(SchemaModel):
    id: int
    run_id: int
    command_execution_id: int | None = None
    inspected_source_id: int | None = None
    activity_draft_id: int | None = None
    surface: str
    field_name: str
    created_at: datetime


class InspectedSource(SchemaModel):
    id: int
    run_id: int
    command_execution_id: int
    source_type: str
    source_name: str | None = None
    path: str | None = None
    command: str
    actor: str = "technician"
    purpose: str
    finding: str
    supports: str = "none"
    sanitized_excerpt: str
    redacted: bool = False
    line_range: str | None = None
    created_at: datetime


class ValidationResult(SchemaModel):
    id: int
    run_id: int
    command_execution_id: int
    check_type: str
    target: str | None = None
    passed: bool
    summary: str
    evidence: str
    created_at: datetime


class ValidationExpectation(SchemaModel):
    id: int
    run_id: int
    fix_command_execution_id: int | None = None
    check_type: str
    target: str | None = None
    expected_result: str
    relation_to_customer_symptom: str
    required: bool = True
    status: str = "pending"
    validation_result_id: int | None = None
    created_at: datetime
    updated_at: datetime | None = None


class BackupRecord(SchemaModel):
    id: int
    run_id: int
    ticket_id: int
    command_execution_id: int | None = None
    source_path: str | None = None
    backup_path: str | None = None
    backup_type: str
    reason: str
    pre_change_hash: str | None = None
    post_change_hash: str | None = None
    owner_before: str | None = None
    group_before: str | None = None
    mode_before: str | None = None
    size_before: int | None = None
    mtime_before: str | None = None
    checksum_before: str | None = None
    sanitized_diff: str | None = None
    restore_command: str | None = None
    stored_content: bool = False
    redacted: bool = False
    backup_required: bool = False
    backup_created: bool = False
    persistent_across_reboot: bool = False
    created_at: datetime


class Run(SchemaModel):
    id: int
    ticket_id: int
    status: RunStatus
    started_at: datetime
    ended_at: datetime | None = None
    ticket_snapshot: JsonObject
    customer_system_snapshot: JsonObject
    current_hypotheses: list[JsonObject] = Field(default_factory=list)
    pending_step: JsonObject | None = None
    validation_result: str | None = None
    activity_draft: JsonObject | None = None


class RunEvent(SchemaModel):
    id: int
    run_id: int
    created_at: datetime
    actor: str
    event_type: str
    summary: str
    command: str | None = None
    sanitized_stdout: str | None = None
    sanitized_stderr: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    risk_class: str | None = None
    approval_status: str | None = None
    error: str | None = None
    payload: JsonObject = Field(default_factory=dict)
