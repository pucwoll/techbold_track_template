from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import ForeignKeyConstraint
from sqlalchemy import Identity
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column


class Base(DeclarativeBase):
    pass


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'awaiting_connection_approval', 'investigating', 'planning', 'awaiting_step_approval', "
            "'executing', 'fixing', 'validating', 'ready_for_activity', 'submitted', 'aborted', 'failed'"
            ")",
            name="runs_status_check",
        ),
        Index("runs_ticket_started_idx", "ticket_id", text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ticket_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    customer_system_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    current_hypotheses: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    pending_step: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_result: Mapped[str | None] = mapped_column(Text)
    activity_draft: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class RunEventRecord(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        CheckConstraint(
            "event_type NOT IN ('command.started', 'command.completed', 'command.failed', 'command.timed_out') "
            "OR command IS NOT NULL",
            name="run_events_command_event_fields_check",
        ),
        CheckConstraint(
            "event_type <> 'terminal.output_chunk' "
            "OR (payload ? 'command_execution_id' AND payload ? 'sequence' AND payload ? 'stream')",
            name="run_events_terminal_payload_check",
        ),
        Index("run_events_run_id_idx", "run_id", "id"),
        Index("run_events_run_created_idx", "run_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    command: Mapped[str | None] = mapped_column(Text)
    sanitized_stdout: Mapped[str | None] = mapped_column(Text)
    sanitized_stderr: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    risk_class: Mapped[str | None] = mapped_column(Text)
    approval_status: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class ProposedStepRecord(Base):
    __tablename__ = "proposed_steps"
    __table_args__ = (
        CheckConstraint("source IN ('agent', 'manual', 'restore')", name="proposed_steps_source_check"),
        CheckConstraint("safety_verdict IN ('allowed', 'blocked')", name="proposed_steps_safety_verdict_check"),
        CheckConstraint(
            "status IN ('proposed', 'approved', 'rejected', 'blocked', 'executed', 'failed')",
            name="proposed_steps_status_check",
        ),
        CheckConstraint(
            "((status IN ('proposed', 'rejected', 'blocked') "
            "AND approved_command IS NULL AND approved_by IS NULL AND approved_at IS NULL) "
            "OR (status IN ('approved', 'executed', 'failed') "
            "AND safety_verdict = 'allowed' AND approved_command IS NOT NULL "
            "AND approved_command = command AND approved_by IS NOT NULL AND approved_at IS NOT NULL))",
            name="proposed_steps_approval_state_check",
        ),
        UniqueConstraint("id", "run_id", name="proposed_steps_id_run_unique"),
        Index("proposed_steps_run_status_idx", "run_id", "status", text("created_at DESC")),
        Index(
            "proposed_steps_one_active_step_per_run_idx",
            "run_id",
            unique=True,
            postgresql_where=text("status IN ('proposed', 'approved')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(Text, nullable=False)
    phase: Mapped[str] = mapped_column(Text, nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    expected_signal: Mapped[str | None] = mapped_column(Text)
    risk_class: Mapped[str] = mapped_column(Text, nullable=False)
    safety_verdict: Mapped[str] = mapped_column(Text, nullable=False)
    safety_summary: Mapped[str] = mapped_column(Text, nullable=False)
    safety_notes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_command: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)


class CommandExecutionRecord(Base):
    __tablename__ = "command_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'timed_out', 'aborted')",
            name="command_executions_status_check",
        ),
        UniqueConstraint("id", "run_id", name="command_executions_id_run_unique"),
        ForeignKeyConstraint(
            ["proposed_step_id", "run_id"],
            ["proposed_steps.id", "proposed_steps.run_id"],
            name="command_executions_step_run_fk",
            ondelete="RESTRICT",
        ),
        Index("command_executions_run_idx", "run_id", "id"),
        Index("command_executions_step_idx", "proposed_step_id"),
        Index("command_executions_one_per_step_idx", "run_id", "proposed_step_id", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    proposed_step_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    approved_command: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    target_host: Mapped[str] = mapped_column(Text, nullable=False)
    target_port: Mapped[int] = mapped_column(Integer, nullable=False)
    target_username: Mapped[str] = mapped_column(Text, nullable=False)
    timeout_s: Mapped[int] = mapped_column(Integer, nullable=False)
    output_limit_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    output_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    sanitized_stdout: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    sanitized_stderr: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    error: Mapped[str | None] = mapped_column(Text)


class CommandOutputChunkRecord(Base):
    __tablename__ = "command_output_chunks"
    __table_args__ = (
        CheckConstraint("stream IN ('stdout', 'stderr')", name="command_output_chunks_stream_check"),
        UniqueConstraint("command_execution_id", "sequence", name="command_output_chunks_execution_sequence_unique"),
        ForeignKeyConstraint(
            ["command_execution_id", "run_id"],
            ["command_executions.id", "command_executions.run_id"],
            name="command_output_chunks_execution_run_fk",
            ondelete="RESTRICT",
        ),
        Index("command_output_chunks_run_idx", "run_id", "command_execution_id", "sequence"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    command_execution_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stream: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class InspectedSourceRecord(Base):
    __tablename__ = "inspected_sources"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('file', 'journal', 'service_status', 'config', 'metadata', 'endpoint', 'other')",
            name="inspected_sources_source_type_check",
        ),
        CheckConstraint(
            "supports IN ('hypothesis', 'root_cause', 'fix_choice', 'validation', 'context', 'none')",
            name="inspected_sources_supports_check",
        ),
        ForeignKeyConstraint(
            ["command_execution_id", "run_id"],
            ["command_executions.id", "command_executions.run_id"],
            name="inspected_sources_execution_run_fk",
            ondelete="RESTRICT",
        ),
        Index("inspected_sources_run_idx", "run_id", "id"),
        Index("inspected_sources_execution_idx", "command_execution_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    command_execution_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(Text)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'technician'"))
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    finding: Mapped[str] = mapped_column(Text, nullable=False)
    supports: Mapped[str] = mapped_column(Text, nullable=False)
    sanitized_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    line_range: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ValidationResultRecord(Base):
    __tablename__ = "validation_results"
    __table_args__ = (
        CheckConstraint(
            "check_type IN ('service_health', 'customer_benefit', 'logs_clean', 'persistence', 'reboot', 'other')",
            name="validation_results_check_type_check",
        ),
        ForeignKeyConstraint(
            ["command_execution_id", "run_id"],
            ["command_executions.id", "command_executions.run_id"],
            name="validation_results_execution_run_fk",
            ondelete="RESTRICT",
        ),
        Index("validation_results_run_idx", "run_id", "id"),
        Index("validation_results_execution_idx", "command_execution_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    command_execution_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    check_type: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ValidationExpectationRecord(Base):
    __tablename__ = "validation_expectations"
    __table_args__ = (
        CheckConstraint(
            "check_type IN ('service_health', 'customer_benefit', 'logs_clean', 'persistence', 'reboot', 'other')",
            name="validation_expectations_check_type_check",
        ),
        CheckConstraint("status IN ('pending', 'passed', 'failed')", name="validation_expectations_status_check"),
        Index("validation_expectations_run_idx", "run_id", "id"),
        Index("validation_expectations_fix_idx", "fix_command_execution_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    fix_command_execution_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("command_executions.id", ondelete="RESTRICT"),
    )
    check_type: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text)
    expected_result: Mapped[str] = mapped_column(Text, nullable=False)
    relation_to_customer_symptom: Mapped[str] = mapped_column(Text, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    validation_result_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("validation_results.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackupRecordRecord(Base):
    __tablename__ = "backup_records"
    __table_args__ = (
        CheckConstraint(
            "backup_type IN ('file_copy', 'metadata_snapshot', 'service_state', 'config_dump', 'not_applicable')",
            name="backup_records_backup_type_check",
        ),
        CheckConstraint(
            "backup_type <> 'not_applicable' OR ("
            "command_execution_id IS NULL AND backup_path IS NULL AND restore_command IS NULL "
            "AND stored_content = false AND backup_required = false AND backup_created = false"
            ")",
            name="backup_records_not_applicable_shape_check",
        ),
        ForeignKeyConstraint(
            ["command_execution_id", "run_id"],
            ["command_executions.id", "command_executions.run_id"],
            name="backup_records_execution_run_fk",
            ondelete="RESTRICT",
        ),
        Index("backup_records_run_idx", "run_id", "id"),
        Index("backup_records_source_idx", "run_id", "source_path"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    command_execution_id: Mapped[int | None] = mapped_column(BigInteger)
    source_path: Mapped[str | None] = mapped_column(Text)
    backup_path: Mapped[str | None] = mapped_column(Text)
    backup_type: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    pre_change_hash: Mapped[str | None] = mapped_column(Text)
    post_change_hash: Mapped[str | None] = mapped_column(Text)
    owner_before: Mapped[str | None] = mapped_column(Text)
    group_before: Mapped[str | None] = mapped_column(Text)
    mode_before: Mapped[str | None] = mapped_column(Text)
    size_before: Mapped[int | None] = mapped_column(BigInteger)
    mtime_before: Mapped[str | None] = mapped_column(Text)
    checksum_before: Mapped[str | None] = mapped_column(Text)
    sanitized_diff: Mapped[str | None] = mapped_column(Text)
    restore_command: Mapped[str | None] = mapped_column(Text)
    stored_content: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    backup_required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    backup_created: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    persistent_across_reboot: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ActivityDraftRecord(Base):
    __tablename__ = "activity_drafts"
    __table_args__ = (Index("activity_drafts_run_idx", "run_id", text("id DESC")),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    submitted_activity_id: Mapped[int | None] = mapped_column(BigInteger)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class IntegrationRequestRecord(Base):
    __tablename__ = "integration_requests"
    __table_args__ = (
        CheckConstraint("request_type IN ('phoenix_activity_submission')", name="integration_requests_request_type_check"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'activity_created', 'completed', 'failed', 'dead_letter')",
            name="integration_requests_status_check",
        ),
        CheckConstraint("ticket_status IN ('OPEN', 'PENDING', 'DONE')", name="integration_requests_ticket_status_check"),
        Index("integration_requests_run_idx", "run_id", text("id DESC")),
        Index("integration_requests_status_idx", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    ticket_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    activity_draft_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("activity_drafts.id", ondelete="RESTRICT"),
    )
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    activity_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    phoenix_activity_id: Mapped[int | None] = mapped_column(BigInteger)
    ticket_status: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RedactionEventRecord(Base):
    __tablename__ = "redaction_events"
    __table_args__ = (
        CheckConstraint("surface IN ('stdout', 'stderr', 'evidence', 'activity')", name="redaction_events_surface_check"),
        Index("redaction_events_run_idx", "run_id", "id"),
        Index("redaction_events_command_idx", "command_execution_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False)
    command_execution_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("command_executions.id", ondelete="RESTRICT"),
    )
    inspected_source_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("inspected_sources.id", ondelete="RESTRICT"),
    )
    activity_draft_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("activity_drafts.id", ondelete="RESTRICT"),
    )
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class OutboxEventRecord(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'dead_letter')",
            name="outbox_events_status_check",
        ),
        Index("outbox_events_pending_idx", "status", "available_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("runs.id", ondelete="RESTRICT"))
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TechnicianCacheRecord(Base):
    __tablename__ = "technician_cache"
    __table_args__ = (CheckConstraint("cache_key = 'me'", name="technician_cache_key_check"),)

    cache_key: Mapped[str] = mapped_column(Text, primary_key=True)
    technician_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TicketCacheRecord(Base):
    __tablename__ = "tickets_cache"
    __table_args__ = (
        Index("tickets_cache_status_idx", "status", "ticket_id"),
        Index("tickets_cache_priority_idx", "priority", "ticket_id"),
        Index("tickets_cache_created_idx", text("created_at_text DESC"), text("ticket_id DESC")),
    )

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticket_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str | None] = mapped_column(Text)
    customer_id: Mapped[int | None] = mapped_column(BigInteger)
    customer_name: Mapped[str | None] = mapped_column(Text)
    created_at_text: Mapped[str | None] = mapped_column(Text)
    sla_due_at_text: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CustomerSystemCacheRecord(Base):
    __tablename__ = "customer_system_cache"
    __table_args__ = (Index("customer_system_cache_customer_idx", "customer_id", "ticket_id"),)

    ticket_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    customer_id: Mapped[int | None] = mapped_column(BigInteger)
    customer_system_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def claimable_outbox_events_query():
    return (
        select(OutboxEventRecord)
        .where(
            OutboxEventRecord.status.in_(("pending", "failed")),
            OutboxEventRecord.available_at <= func.now(),
        )
        .order_by(OutboxEventRecord.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
