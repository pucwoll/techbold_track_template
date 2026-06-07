from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .schemas import (
    BackupRecord,
    CommandExecution,
    CommandExecutionStatus,
    CommandOutputChunk,
    IntegrationRequest,
    IntegrationRequestStatus,
    InspectedSource,
    OutboxEvent,
    OutboxStatus,
    ProposedStep,
    RedactionEvent,
    Run,
    RunEvent,
    RunStatus,
    StepStatus,
    ValidationExpectation,
    ValidationResult,
)


Row = Mapping[str, Any]


def row_to_run(row: Row) -> Run:
    return Run(
        id=row["id"],
        ticket_id=row["ticket_id"],
        status=RunStatus(row["status"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        ticket_snapshot=row["ticket_snapshot"],
        customer_system_snapshot=row["customer_system_snapshot"],
        current_hypotheses=row["current_hypotheses"] or [],
        pending_step=row["pending_step"],
        validation_result=row["validation_result"],
        activity_draft=row["activity_draft"],
    )


def row_to_event(row: Row) -> RunEvent:
    return RunEvent(
        id=row["id"],
        run_id=row["run_id"],
        created_at=row["created_at"],
        actor=row["actor"],
        event_type=row["event_type"],
        summary=row["summary"],
        command=row["command"],
        sanitized_stdout=row["sanitized_stdout"],
        sanitized_stderr=row["sanitized_stderr"],
        exit_code=row["exit_code"],
        duration_ms=row["duration_ms"],
        risk_class=row["risk_class"],
        approval_status=row["approval_status"],
        error=row["error"],
        payload=row["payload"] or {},
    )


def row_to_step(row: Row) -> ProposedStep:
    return ProposedStep(
        id=row["id"],
        run_id=row["run_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        source=row["source"],
        phase=row["phase"],
        command=row["command"],
        purpose=row["purpose"],
        expected_signal=row["expected_signal"],
        risk_class=row["risk_class"],
        safety_verdict=row["safety_verdict"],
        safety_summary=row["safety_summary"],
        safety_notes=row["safety_notes"] or [],
        status=StepStatus(row["status"]),
        timeout_s=row["timeout_s"],
        approved_command=row["approved_command"],
        approved_by=row["approved_by"],
        approved_at=row["approved_at"],
        rejection_reason=row["rejection_reason"],
    )


def row_to_outbox_event(row: Row) -> OutboxEvent:
    return OutboxEvent(
        id=row["id"],
        run_id=row["run_id"],
        event_type=row["event_type"],
        payload=row["payload"] or {},
        status=OutboxStatus(row["status"]),
        attempts=row["attempts"],
        available_at=row["available_at"],
        claimed_at=row["claimed_at"],
        completed_at=row["completed_at"],
        error=row["error"],
        created_at=row["created_at"],
    )


def row_to_integration_request(row: Row) -> IntegrationRequest:
    return IntegrationRequest(
        id=row["id"],
        run_id=row["run_id"],
        ticket_id=row["ticket_id"],
        activity_draft_id=row["activity_draft_id"],
        request_type=row["request_type"],
        status=IntegrationRequestStatus(row["status"]),
        activity_payload=row["activity_payload"],
        phoenix_activity_id=row["phoenix_activity_id"],
        ticket_status=row["ticket_status"],
        attempts=row["attempts"],
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def row_to_command_execution(row: Row) -> CommandExecution:
    return CommandExecution(
        id=row["id"],
        run_id=row["run_id"],
        proposed_step_id=row["proposed_step_id"],
        approved_command=row["approved_command"],
        status=CommandExecutionStatus(row["status"]),
        target_host=row["target_host"],
        target_port=row["target_port"],
        target_username=row["target_username"],
        timeout_s=row["timeout_s"],
        output_limit_bytes=row["output_limit_bytes"],
        output_truncated=row["output_truncated"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        exit_code=row["exit_code"],
        duration_ms=row["duration_ms"],
        sanitized_stdout=row["sanitized_stdout"] or "",
        sanitized_stderr=row["sanitized_stderr"] or "",
        error=row["error"],
    )


def row_to_command_output_chunk(row: Row) -> CommandOutputChunk:
    return CommandOutputChunk(
        id=row["id"],
        command_execution_id=row["command_execution_id"],
        run_id=row["run_id"],
        sequence=row["sequence"],
        stream=row["stream"],
        content=row["content"],
        redacted=row["redacted"],
        created_at=row["created_at"],
    )


def row_to_redaction_event(row: Row) -> RedactionEvent:
    return RedactionEvent(
        id=row["id"],
        run_id=row["run_id"],
        command_execution_id=row["command_execution_id"],
        inspected_source_id=row["inspected_source_id"],
        activity_draft_id=row["activity_draft_id"],
        surface=row["surface"],
        field_name=row["field_name"],
        created_at=row["created_at"],
    )


def row_to_inspected_source(row: Row) -> InspectedSource:
    return InspectedSource(
        id=row["id"],
        run_id=row["run_id"],
        command_execution_id=row["command_execution_id"],
        source_type=row["source_type"],
        source_name=row["source_name"],
        path=row["path"],
        command=row["command"],
        actor=row["actor"],
        purpose=row["purpose"],
        finding=row["finding"],
        supports=row["supports"],
        sanitized_excerpt=row["sanitized_excerpt"],
        redacted=row["redacted"],
        line_range=row["line_range"],
        created_at=row["created_at"],
    )


def row_to_validation_result(row: Row) -> ValidationResult:
    return ValidationResult(
        id=row["id"],
        run_id=row["run_id"],
        command_execution_id=row["command_execution_id"],
        check_type=row["check_type"],
        target=row["target"],
        passed=row["passed"],
        summary=row["summary"],
        evidence=row["evidence"],
        created_at=row["created_at"],
    )


def row_to_validation_expectation(row: Row) -> ValidationExpectation:
    return ValidationExpectation(
        id=row["id"],
        run_id=row["run_id"],
        fix_command_execution_id=row["fix_command_execution_id"],
        check_type=row["check_type"],
        target=row["target"],
        expected_result=row["expected_result"],
        relation_to_customer_symptom=row["relation_to_customer_symptom"],
        required=row["required"],
        status=row["status"],
        validation_result_id=row["validation_result_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_backup_record(row: Row) -> BackupRecord:
    return BackupRecord(
        id=row["id"],
        run_id=row["run_id"],
        ticket_id=row["ticket_id"],
        command_execution_id=row["command_execution_id"],
        source_path=row["source_path"],
        backup_path=row["backup_path"],
        backup_type=row["backup_type"],
        reason=row["reason"],
        pre_change_hash=row["pre_change_hash"],
        post_change_hash=row["post_change_hash"],
        owner_before=row["owner_before"],
        group_before=row["group_before"],
        mode_before=row["mode_before"],
        size_before=row["size_before"],
        mtime_before=row["mtime_before"],
        checksum_before=row["checksum_before"],
        sanitized_diff=row["sanitized_diff"],
        restore_command=row["restore_command"],
        stored_content=row["stored_content"],
        redacted=row["redacted"],
        backup_required=row["backup_required"],
        backup_created=row["backup_created"],
        persistent_across_reboot=row["persistent_across_reboot"],
        created_at=row["created_at"],
    )
