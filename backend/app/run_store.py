from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any, Protocol

from .activity_generator import build_activity_draft
from .backup_service import (
    backup_record_satisfies,
    backup_plan_for_requirement,
    backup_requirement_for_command,
    detect_backup_record,
    is_targeted_backup_command,
)
from .evidence_detector import detect_inspected_sources
from .safety_layer import classify_command, redact_output
from .schemas import (
    ActivityDraft,
    BackupRecord,
    CommandExecution,
    CommandExecutionStatus,
    CommandOutputChunk,
    IntegrationRequest,
    IntegrationRequestStatus,
    InspectedSource,
    JsonObject,
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


class RunStoreError(Exception):
    """Base error for durable run storage."""


class RunNotFoundError(RunStoreError):
    pass


class RunTransitionError(RunStoreError):
    pass


class RunStore(Protocol):
    def create_run(self, *, ticket_id: int, ticket_snapshot: JsonObject, customer_system_snapshot: JsonObject) -> Run:
        ...

    def get_run(self, run_id: int) -> Run:
        ...

    def approve_connection(self, run_id: int, *, approved_by: str) -> Run:
        ...

    def create_manual_step(
        self,
        run_id: int,
        *,
        command: str,
        entered_by: str,
        purpose: str,
        expected_signal: str | None = None,
        phase: str = "diagnostic",
        timeout_s: int | None = None,
    ) -> ProposedStep:
        ...

    def propose_agent_step(
        self,
        run_id: int,
        *,
        command: str,
        purpose: str,
        expected_signal: str,
        phase: str = "diagnostic",
        timeout_s: int | None = None,
    ) -> ProposedStep:
        ...

    def get_step(self, run_id: int, step_id: int) -> ProposedStep:
        ...

    def approve_step(self, run_id: int, step_id: int, *, approved_by: str) -> Run:
        ...

    def edit_and_approve_step(
        self,
        run_id: int,
        step_id: int,
        *,
        command: str,
        approved_by: str,
        purpose: str | None = None,
        expected_signal: str | None = None,
        timeout_s: int | None = None,
    ) -> Run:
        ...

    def reject_step(self, run_id: int, step_id: int, *, rejected_by: str, reason: str) -> Run:
        ...

    def retry_run(self, run_id: int, *, requested_by: str, reason: str = "Retry requested by technician.") -> Run:
        ...

    def abort_run(self, run_id: int, *, aborted_by: str, reason: str = "Aborted by technician.") -> Run:
        ...

    def list_events(self, run_id: int, *, after_id: int = 0) -> list[RunEvent]:
        ...

    def list_outbox_events(
        self,
        run_id: int,
        *,
        statuses: set[OutboxStatus] | None = None,
    ) -> list[OutboxEvent]:
        ...

    def list_integration_requests(self, run_id: int) -> list[IntegrationRequest]:
        ...

    def get_integration_request(self, run_id: int, integration_request_id: int) -> IntegrationRequest:
        ...

    def append_event(
        self,
        run_id: int,
        *,
        actor: str,
        event_type: str,
        summary: str,
        payload: JsonObject,
        command: str | None = None,
        error: str | None = None,
    ) -> None:
        ...

    def claim_next_outbox_event(self) -> OutboxEvent | None:
        ...

    def recover_stale_outbox_events(self, *, stale_after_s: int = 120) -> int:
        ...

    def complete_outbox_event(self, outbox_event_id: int) -> None:
        ...

    def fail_outbox_event(self, outbox_event_id: int, *, error: str) -> None:
        ...

    def start_command_execution(self, run_id: int, step_id: int) -> CommandExecution:
        ...

    def append_command_output_chunk(
        self,
        run_id: int,
        command_execution_id: int,
        *,
        stream: str,
        content: str,
        redacted: bool,
    ) -> CommandOutputChunk | None:
        ...

    def complete_command_execution(
        self,
        run_id: int,
        command_execution_id: int,
        *,
        exit_code: int | None,
        duration_ms: int,
        error: str | None,
        timed_out: bool,
    ) -> CommandExecution:
        ...

    def list_command_executions(self, run_id: int) -> list[CommandExecution]:
        ...

    def list_command_output_chunks(self, run_id: int) -> list[CommandOutputChunk]:
        ...

    def list_redaction_events(self, run_id: int) -> list[RedactionEvent]:
        ...

    def list_inspected_sources(self, run_id: int) -> list[InspectedSource]:
        ...

    def list_validation_results(self, run_id: int) -> list[ValidationResult]:
        ...

    def list_validation_expectations(self, run_id: int) -> list[ValidationExpectation]:
        ...

    def list_backup_records(self, run_id: int) -> list[BackupRecord]:
        ...

    def record_backup_not_applicable(
        self,
        run_id: int,
        *,
        source_path: str | None,
        reason: str,
        recorded_by: str,
    ) -> BackupRecord:
        ...

    def propose_restore_command(
        self,
        run_id: int,
        backup_record_id: int,
        *,
        proposed_by: str,
        reason: str,
    ) -> ProposedStep:
        ...

    def create_activity_draft(self, run_id: int) -> ActivityDraft:
        ...

    def save_activity_draft(self, run_id: int, draft: ActivityDraft, *, edited_by: str | None = None) -> ActivityDraft:
        ...

    def queue_activity_submission(self, run_id: int) -> IntegrationRequest:
        ...

    def mark_integration_request_processing(self, run_id: int, integration_request_id: int) -> IntegrationRequest:
        ...

    def mark_integration_activity_created(
        self,
        run_id: int,
        integration_request_id: int,
        *,
        phoenix_activity_id: int,
    ) -> IntegrationRequest:
        ...

    def mark_integration_request_completed(
        self,
        run_id: int,
        integration_request_id: int,
        *,
        ticket_status: str,
    ) -> Run:
        ...

    def fail_integration_request(
        self,
        run_id: int,
        integration_request_id: int,
        *,
        error: str,
        terminal: bool = False,
    ) -> IntegrationRequest:
        ...


class InMemoryRunStore:
    def __init__(
        self,
        *,
        command_timeout_s: int = 30,
        command_output_limit_bytes: int = 200_000,
    ) -> None:
        self.command_timeout_s = command_timeout_s
        self.command_output_limit_bytes = command_output_limit_bytes
        self._runs: dict[int, Run] = {}
        self._events: dict[int, list[RunEvent]] = {}
        self._steps: dict[int, ProposedStep] = {}
        self._steps_by_run: dict[int, list[int]] = {}
        self._outbox: dict[int, OutboxEvent] = {}
        self._executions: dict[int, CommandExecution] = {}
        self._executions_by_run: dict[int, list[int]] = {}
        self._chunks: dict[int, CommandOutputChunk] = {}
        self._chunks_by_run: dict[int, list[int]] = {}
        self._redaction_events: dict[int, RedactionEvent] = {}
        self._redaction_events_by_run: dict[int, list[int]] = {}
        self._inspected_sources: dict[int, InspectedSource] = {}
        self._inspected_sources_by_run: dict[int, list[int]] = {}
        self._validation_results: dict[int, ValidationResult] = {}
        self._validation_results_by_run: dict[int, list[int]] = {}
        self._validation_expectations: dict[int, ValidationExpectation] = {}
        self._validation_expectations_by_run: dict[int, list[int]] = {}
        self._backup_records: dict[int, BackupRecord] = {}
        self._backup_records_by_run: dict[int, list[int]] = {}
        self._activity_drafts: dict[int, ActivityDraft] = {}
        self._activity_draft_ids_by_run: dict[int, int] = {}
        self._integration_requests: dict[int, IntegrationRequest] = {}
        self._integration_requests_by_run: dict[int, list[int]] = {}
        self._stored_output_bytes: dict[int, int] = {}
        self._next_run_id = 1
        self._next_event_id = 1
        self._next_step_id = 1
        self._next_outbox_id = 1
        self._next_execution_id = 1
        self._next_chunk_id = 1
        self._next_redaction_event_id = 1
        self._next_inspected_source_id = 1
        self._next_validation_result_id = 1
        self._next_validation_expectation_id = 1
        self._next_backup_record_id = 1
        self._next_activity_draft_id = 1
        self._next_integration_request_id = 1

    def create_run(self, *, ticket_id: int, ticket_snapshot: JsonObject, customer_system_snapshot: JsonObject) -> Run:
        run = Run(
            id=self._next_run_id,
            ticket_id=ticket_id,
            status=RunStatus.AWAITING_CONNECTION_APPROVAL,
            started_at=_utc_now(),
            ticket_snapshot=deepcopy(ticket_snapshot),
            customer_system_snapshot=deepcopy(customer_system_snapshot),
        )
        self._next_run_id += 1
        self._runs[run.id] = run
        self._events[run.id] = []
        self._steps_by_run[run.id] = []
        self._executions_by_run[run.id] = []
        self._chunks_by_run[run.id] = []
        self._redaction_events_by_run[run.id] = []
        self._inspected_sources_by_run[run.id] = []
        self._validation_results_by_run[run.id] = []
        self._validation_expectations_by_run[run.id] = []
        self._backup_records_by_run[run.id] = []
        self._integration_requests_by_run[run.id] = []

        self._append_event(
            run.id,
            actor="technician",
            event_type="run.created",
            summary=f"Troubleshooting run created for ticket #{ticket_id}.",
            payload={"ticket_id": ticket_id},
        )
        self._append_event(
            run.id,
            actor="system",
            event_type="connection.approval_requested",
            summary=_connection_summary(customer_system_snapshot),
            payload={"target": _connection_target(customer_system_snapshot)},
        )
        return run

    def get_run(self, run_id: int) -> Run:
        run = self._runs.get(run_id)
        if not run:
            raise RunNotFoundError(f"Run {run_id} was not found")
        return run

    def approve_connection(self, run_id: int, *, approved_by: str) -> Run:
        run = self.get_run(run_id)
        if run.status != RunStatus.AWAITING_CONNECTION_APPROVAL:
            raise RunTransitionError("Connection approval can only be recorded while approval is pending")

        updated = run.model_copy(update={"status": RunStatus.INVESTIGATING})
        self._runs[run_id] = updated
        self._append_event(
            run_id,
            actor="technician",
            event_type="connection.approved",
            summary=f"SSH connection approved by {approved_by}.",
            approval_status="approved",
            payload={"approved_by": approved_by},
        )
        self._append_event(
            run_id,
            actor="agent",
            event_type="agent.plan_requested",
            summary="Initial diagnostic planning requested after connection approval.",
            payload={},
        )
        self._enqueue_outbox(run_id, "agent.plan_requested", {"reason": "connection_approved"})
        return updated

    def create_manual_step(
        self,
        run_id: int,
        *,
        command: str,
        entered_by: str,
        purpose: str,
        expected_signal: str | None = None,
        phase: str = "diagnostic",
        timeout_s: int | None = None,
    ) -> ProposedStep:
        self._cancel_pending_agent_plan(run_id)
        return self._create_step(
            run_id,
            source="manual",
            command=command,
            actor="technician",
            actor_name=entered_by,
            purpose=purpose,
            expected_signal=expected_signal,
            phase=phase,
            timeout_s=timeout_s,
        )

    def propose_agent_step(
        self,
        run_id: int,
        *,
        command: str,
        purpose: str,
        expected_signal: str,
        phase: str = "diagnostic",
        timeout_s: int | None = None,
    ) -> ProposedStep:
        return self._create_step(
            run_id,
            source="agent",
            command=command,
            actor="agent",
            actor_name="agent",
            purpose=purpose,
            expected_signal=expected_signal,
            phase=phase,
            timeout_s=timeout_s,
        )

    def get_step(self, run_id: int, step_id: int) -> ProposedStep:
        self.get_run(run_id)
        step = self._steps.get(step_id)
        if not step or step.run_id != run_id:
            raise RunTransitionError(f"Step {step_id} does not belong to run {run_id}")
        return step

    def approve_step(self, run_id: int, step_id: int, *, approved_by: str) -> Run:
        run = self.get_run(run_id)
        step = self.get_step(run_id, step_id)
        self._assert_step_approvable(run, step)
        self._assert_fix_policy(run, step)
        self._assert_backup_requirement_satisfied(run, step)

        approved_at = _utc_now()
        updated_step = step.model_copy(
            update={
                "status": StepStatus.APPROVED,
                "approved_command": step.command,
                "approved_by": approved_by,
                "approved_at": approved_at,
                "updated_at": approved_at,
            }
        )
        self._steps[step_id] = updated_step
        updated_run = run.model_copy(update={"status": _status_for_approved_step(updated_step), "pending_step": None})
        self._runs[run_id] = updated_run
        self._append_event(
            run_id,
            actor="technician",
            event_type="step.approved",
            summary=f"Command approved by {approved_by}.",
            command=updated_step.approved_command,
            risk_class=updated_step.risk_class,
            approval_status="approved",
            payload={"step_id": step_id, "approved_by": approved_by},
        )
        self._queue_command_execution(run_id, step_id)
        return updated_run

    def edit_and_approve_step(
        self,
        run_id: int,
        step_id: int,
        *,
        command: str,
        approved_by: str,
        purpose: str | None = None,
        expected_signal: str | None = None,
        timeout_s: int | None = None,
    ) -> Run:
        run = self.get_run(run_id)
        step = self.get_step(run_id, step_id)
        if run.status != RunStatus.AWAITING_STEP_APPROVAL:
            raise RunTransitionError("A step can only be edited while approval is pending")
        if step.status != StepStatus.PROPOSED:
            raise RunTransitionError("Only proposed steps can be edited and approved")

        safety = classify_command(command)
        now = _utc_now()
        blocked_update = step.model_copy(
            update={
                "command": command,
                "purpose": purpose or step.purpose,
                "expected_signal": expected_signal if expected_signal is not None else step.expected_signal,
                "risk_class": safety.risk_class,
                "safety_verdict": safety.verdict,
                "safety_summary": safety.summary,
                "safety_notes": safety.notes,
                "timeout_s": timeout_s or step.timeout_s,
                "updated_at": now,
            }
        )
        self._steps[step_id] = blocked_update
        self._append_event(
            run_id,
            actor="safety_layer",
            event_type="step.safety_classified",
            summary=safety.summary,
            command=command,
            risk_class=safety.risk_class,
            approval_status=safety.verdict,
            payload=_safety_event_payload(step_id, safety, edited=True),
        )
        if safety.verdict == "blocked":
            raise RunTransitionError("Edited command was blocked by the safety layer")
        self._assert_fix_policy(run, blocked_update)
        self._assert_backup_requirement_satisfied(run, blocked_update)

        approved_step = blocked_update.model_copy(
            update={
                "status": StepStatus.APPROVED,
                "approved_command": command,
                "approved_by": approved_by,
                "approved_at": now,
                "updated_at": now,
            }
        )
        self._steps[step_id] = approved_step
        updated_run = run.model_copy(update={"status": _status_for_approved_step(approved_step), "pending_step": None})
        self._runs[run_id] = updated_run
        self._append_event(
            run_id,
            actor="technician",
            event_type="step.edited_and_approved",
            summary=f"Command edited and approved by {approved_by}.",
            command=command,
            risk_class=approved_step.risk_class,
            approval_status="approved",
            payload={"step_id": step_id, "approved_by": approved_by},
        )
        self._queue_command_execution(run_id, step_id)
        return updated_run

    def reject_step(self, run_id: int, step_id: int, *, rejected_by: str, reason: str) -> Run:
        run = self.get_run(run_id)
        step = self.get_step(run_id, step_id)
        if run.status != RunStatus.AWAITING_STEP_APPROVAL or step.status != StepStatus.PROPOSED:
            raise RunTransitionError("Only pending proposed steps can be rejected")

        now = _utc_now()
        self._steps[step_id] = step.model_copy(
            update={"status": StepStatus.REJECTED, "rejection_reason": reason, "updated_at": now}
        )
        updated_run = run.model_copy(update={"status": _idle_status_for_run(run), "pending_step": None})
        self._runs[run_id] = updated_run
        self._append_event(
            run_id,
            actor="technician",
            event_type="step.rejected",
            summary=f"Command rejected by {rejected_by}: {reason}",
            command=step.command,
            risk_class=step.risk_class,
            approval_status="rejected",
            payload={"step_id": step_id, "rejected_by": rejected_by, "reason": reason},
        )
        return updated_run

    def retry_run(self, run_id: int, *, requested_by: str, reason: str = "Retry requested by technician.") -> Run:
        run = self.get_run(run_id)
        if run.status in {RunStatus.ABORTED, RunStatus.SUBMITTED}:
            raise RunTransitionError("Terminal runs cannot be retried")

        now = _utc_now()
        for outbox_id, event in list(self._outbox.items()):
            if (
                event.run_id == run_id
                and event.event_type == "agent.plan_requested"
                and event.status in {OutboxStatus.FAILED, OutboxStatus.DEAD_LETTER}
            ):
                self._outbox[outbox_id] = event.model_copy(
                    update={
                        "status": OutboxStatus.COMPLETED,
                        "completed_at": now,
                        "payload": event.payload | {"superseded_by_retry": True},
                    }
                )
        updated = run.model_copy(update={"status": RunStatus.INVESTIGATING, "pending_step": None})
        self._runs[run_id] = updated
        self._append_event(
            run_id,
            actor="technician",
            event_type="run.retry_requested",
            summary=f"Retry requested by {requested_by}: {reason}",
            payload={"requested_by": requested_by, "reason": reason},
        )
        self._enqueue_outbox(run_id, "agent.plan_requested", {"reason": "retry_requested", "requested_by": requested_by})
        return updated

    def abort_run(self, run_id: int, *, aborted_by: str, reason: str = "Aborted by technician.") -> Run:
        run = self.get_run(run_id)
        if run.status in {RunStatus.ABORTED, RunStatus.SUBMITTED}:
            return run
        updated = run.model_copy(update={"status": RunStatus.ABORTED, "pending_step": None, "ended_at": _utc_now()})
        self._runs[run_id] = updated
        self._append_event(
            run_id,
            actor="technician",
            event_type="run.aborted",
            summary=f"Run aborted by {aborted_by}: {reason}",
            approval_status="aborted",
            payload={"aborted_by": aborted_by, "reason": reason},
        )
        return updated

    def list_events(self, run_id: int, *, after_id: int = 0) -> list[RunEvent]:
        self.get_run(run_id)
        return [event for event in self._events[run_id] if event.id > after_id]

    def list_outbox_events(
        self,
        run_id: int,
        *,
        statuses: set[OutboxStatus] | None = None,
    ) -> list[OutboxEvent]:
        self.get_run(run_id)
        return sorted(
            (
                event
                for event in self._outbox.values()
                if event.run_id == run_id and (statuses is None or event.status in statuses)
            ),
            key=lambda event: event.id,
        )

    def list_integration_requests(self, run_id: int) -> list[IntegrationRequest]:
        self.get_run(run_id)
        return [
            self._integration_requests[request_id]
            for request_id in self._integration_requests_by_run.get(run_id, [])
        ]

    def get_integration_request(self, run_id: int, integration_request_id: int) -> IntegrationRequest:
        self.get_run(run_id)
        request = self._integration_requests.get(integration_request_id)
        if not request or request.run_id != run_id:
            raise RunTransitionError(f"Integration request {integration_request_id} does not belong to run {run_id}")
        return request

    def append_event(
        self,
        run_id: int,
        *,
        actor: str,
        event_type: str,
        summary: str,
        payload: JsonObject,
        command: str | None = None,
        error: str | None = None,
    ) -> None:
        self.get_run(run_id)
        self._append_event(
            run_id,
            actor=actor,
            event_type=event_type,
            summary=summary,
            command=command,
            error=error,
            payload=payload,
        )

    def claim_next_outbox_event(self) -> OutboxEvent | None:
        self.recover_stale_outbox_events()
        now = _utc_now()
        pending = sorted(
            (
                event
                for event in self._outbox.values()
                if event.status in {OutboxStatus.PENDING, OutboxStatus.FAILED}
                and (event.available_at is None or event.available_at <= now)
            ),
            key=lambda event: event.id,
        )
        if not pending:
            return None
        event = pending[0]
        updated = event.model_copy(
            update={"status": OutboxStatus.PROCESSING, "attempts": event.attempts + 1, "claimed_at": _utc_now()}
        )
        self._outbox[event.id] = updated
        return updated

    def recover_stale_outbox_events(self, *, stale_after_s: int = 120) -> int:
        cutoff = _utc_now() - timedelta(seconds=stale_after_s)
        recovered_count = 0
        for outbox_id, event in list(self._outbox.items()):
            if event.status != OutboxStatus.PROCESSING or not event.claimed_at or event.claimed_at >= cutoff:
                continue
            self._outbox[outbox_id] = event.model_copy(
                update={"status": OutboxStatus.PENDING, "claimed_at": None, "available_at": _utc_now()}
            )
            recovered_count += 1
            if event.run_id is not None:
                self._append_event(
                    event.run_id,
                    actor="worker",
                    event_type="outbox.recovered",
                    summary="Stale processing outbox event recovered for retry.",
                    payload={"outbox_event_id": event.id, "event_type": event.event_type},
                )
        return recovered_count

    def complete_outbox_event(self, outbox_event_id: int) -> None:
        event = self._outbox.get(outbox_event_id)
        if event:
            self._outbox[outbox_event_id] = event.model_copy(
                update={"status": OutboxStatus.COMPLETED, "completed_at": _utc_now()}
            )

    def fail_outbox_event(self, outbox_event_id: int, *, error: str) -> None:
        event = self._outbox.get(outbox_event_id)
        if event:
            status = OutboxStatus.DEAD_LETTER if event.attempts >= 3 else OutboxStatus.FAILED
            available_at = None if status == OutboxStatus.DEAD_LETTER else _utc_now() + timedelta(seconds=_outbox_backoff_seconds(event.attempts))
            self._outbox[outbox_event_id] = event.model_copy(
                update={
                    "status": status,
                    "available_at": available_at,
                    "claimed_at": None,
                    "error": error,
                    "payload": event.payload | {"error": error},
                }
            )
            integration_request_id = event.payload.get("integration_request_id")
            if event.event_type == "integration.activity_submission_requested" and isinstance(integration_request_id, int):
                self.fail_integration_request(
                    event.run_id or 0,
                    integration_request_id,
                    error=error,
                    terminal=status == OutboxStatus.DEAD_LETTER,
                )

    def start_command_execution(self, run_id: int, step_id: int) -> CommandExecution:
        run = self.get_run(run_id)
        step = self.get_step(run_id, step_id)
        if run.status == RunStatus.ABORTED:
            raise RunTransitionError("Aborted runs cannot execute commands")
        if step.status != StepStatus.APPROVED or step.safety_verdict == "blocked" or not step.approved_command:
            raise RunTransitionError("Only approved non-blocked steps can execute")
        if run.status != _status_for_approved_step(step):
            raise RunTransitionError("Stale approved step cannot execute after the run moved to another state")
        if any(execution.proposed_step_id == step_id for execution in self._executions.values()):
            raise RunTransitionError("Approved step already has a command execution")

        target = _connection_target(run.customer_system_snapshot)
        execution = CommandExecution(
            id=self._next_execution_id,
            run_id=run_id,
            proposed_step_id=step_id,
            approved_command=step.approved_command,
            status=CommandExecutionStatus.RUNNING,
            target_host=str(target.get("ip") or ""),
            target_port=int(target.get("port") or 22),
            target_username=str(target.get("username") or ""),
            timeout_s=step.timeout_s,
            output_limit_bytes=self.command_output_limit_bytes,
            started_at=_utc_now(),
        )
        self._next_execution_id += 1
        self._executions[execution.id] = execution
        self._executions_by_run[run_id].append(execution.id)
        self._stored_output_bytes[execution.id] = 0
        self._append_event(
            run_id,
            actor="ssh_runner",
            event_type="command.started",
            summary="Approved SSH command started.",
            command=execution.approved_command,
            risk_class=step.risk_class,
            payload={"step_id": step_id, "command_execution_id": execution.id, "target": target},
        )
        return execution

    def append_command_output_chunk(
        self,
        run_id: int,
        command_execution_id: int,
        *,
        stream: str,
        content: str,
        redacted: bool,
    ) -> CommandOutputChunk | None:
        self.get_run(run_id)
        execution = self._get_execution(run_id, command_execution_id)
        if stream not in {"stdout", "stderr"}:
            raise RunTransitionError("Command output stream must be stdout or stderr")
        content, store_redacted = redact_output(content)
        redacted = redacted or store_redacted

        stored_bytes = self._stored_output_bytes.get(command_execution_id, 0)
        if stored_bytes >= execution.output_limit_bytes:
            self._mark_execution_truncated(run_id, execution)
            return None

        available = execution.output_limit_bytes - stored_bytes
        content_to_store, truncated = _truncate_to_bytes(content, available)
        if not content_to_store:
            self._mark_execution_truncated(run_id, execution)
            return None

        sequence = len([chunk_id for chunk_id in self._chunks_by_run[run_id] if self._chunks[chunk_id].command_execution_id == command_execution_id]) + 1
        chunk = CommandOutputChunk(
            id=self._next_chunk_id,
            command_execution_id=command_execution_id,
            run_id=run_id,
            sequence=sequence,
            stream=stream,
            content=content_to_store,
            redacted=redacted,
            created_at=_utc_now(),
        )
        self._next_chunk_id += 1
        self._chunks[chunk.id] = chunk
        self._chunks_by_run[run_id].append(chunk.id)
        self._stored_output_bytes[command_execution_id] = stored_bytes + len(content_to_store.encode("utf-8"))
        aggregate_field = "sanitized_stdout" if stream == "stdout" else "sanitized_stderr"
        self._executions[command_execution_id] = execution.model_copy(
            update={aggregate_field: getattr(execution, aggregate_field) + content_to_store}
        )
        self._append_event(
            run_id,
            actor="ssh_runner",
            event_type="terminal.output_chunk",
            summary=f"{stream} output chunk received.",
            payload={
                "command_execution_id": command_execution_id,
                "sequence": sequence,
                "stream": stream,
                "content": content_to_store,
                "redacted": redacted,
            },
        )
        if redacted:
            self._record_redaction_event(
                run_id,
                command_execution_id=command_execution_id,
                surface=stream,
                field_name=stream,
            )
        if truncated:
            self._mark_execution_truncated(run_id, self._executions[command_execution_id])
        return chunk

    def complete_command_execution(
        self,
        run_id: int,
        command_execution_id: int,
        *,
        exit_code: int | None,
        duration_ms: int,
        error: str | None,
        timed_out: bool,
    ) -> CommandExecution:
        run = self.get_run(run_id)
        execution = self._get_execution(run_id, command_execution_id)
        status = _final_execution_status(exit_code=exit_code, timed_out=timed_out, error=error)
        completed = execution.model_copy(
            update={
                "status": status,
                "completed_at": _utc_now(),
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "error": error,
            }
        )
        self._executions[command_execution_id] = completed
        step = self.get_step(run_id, execution.proposed_step_id)
        self._steps[step.id] = step.model_copy(
            update={
                "status": StepStatus.EXECUTED if status == CommandExecutionStatus.COMPLETED else StepStatus.FAILED,
                "updated_at": _utc_now(),
            }
        )
        validation_result: ValidationResult | None = None
        if run.status != RunStatus.ABORTED:
            if step.phase == "validation":
                validation_result = self._record_validation_result(run_id, completed)
                suite_status = self._apply_validation_result_to_suite(run_id, validation_result)
                next_status = RunStatus.READY_FOR_ACTIVITY if suite_status == "passed" else RunStatus.VALIDATING
                self._runs[run_id] = run.model_copy(
                    update={
                        "status": next_status,
                        "pending_step": None,
                        "validation_result": validation_result.summary,
                    }
                )
                if suite_status == "passed":
                    self._append_event(
                        run_id,
                        actor="validation",
                        event_type="validation.suite_passed",
                        summary="All required validation checks passed for the latest fix.",
                        payload={"validation_result_id": validation_result.id},
                    )
                    self._append_event(
                        run_id,
                        actor="agent",
                        event_type="agent.activity_draft_requested",
                        summary="Activity draft generation requested after all required validation checks passed.",
                        payload={"reason": "validation_suite_passed", "validation_result_id": validation_result.id},
                    )
                    self._enqueue_outbox(
                        run_id,
                        "agent.activity_draft_requested",
                        {"reason": "validation_suite_passed", "validation_result_id": validation_result.id},
                    )
                elif suite_status == "failed" or not validation_result.passed:
                    self._append_event(
                        run_id,
                        actor="agent",
                        event_type="agent.plan_requested",
                        summary="Follow-up planning requested after validation failed.",
                        payload={"reason": "validation_failed", "validation_result_id": validation_result.id},
                    )
                    self._enqueue_outbox(run_id, "agent.plan_requested", {"reason": "validation_failed"})
                else:
                    self._append_event(
                        run_id,
                        actor="agent",
                        event_type="agent.plan_requested",
                        summary="Additional validation planning requested because the required suite is incomplete.",
                        payload={"reason": "validation_suite_incomplete", "validation_result_id": validation_result.id},
                    )
                    self._enqueue_outbox(run_id, "agent.plan_requested", {"reason": "validation_suite_incomplete"})
            elif status == CommandExecutionStatus.COMPLETED and step.phase == "fix":
                expectations = self._create_validation_expectations_for_fix(run_id, completed, step)
                self._runs[run_id] = run.model_copy(update={"status": RunStatus.VALIDATING, "pending_step": None})
                self._append_event(
                    run_id,
                    actor="validation",
                    event_type="validation.suite_required",
                    summary="Required validation suite persisted for the approved fix.",
                    command=completed.approved_command,
                    payload={
                        "step_id": step.id,
                        "command_execution_id": command_execution_id,
                        "validation_expectation_ids": [expectation.id for expectation in expectations],
                        "required_checks": [expectation.check_type for expectation in expectations],
                    },
                )
                self._append_event(
                    run_id,
                    actor="validation",
                    event_type="validation.required",
                    summary="Approved fix completed; validation planning is required before activity drafting.",
                    command=completed.approved_command,
                    payload={"step_id": step.id, "command_execution_id": command_execution_id},
                )
                self._append_event(
                    run_id,
                    actor="agent",
                    event_type="agent.plan_requested",
                    summary="Validation planning requested after the approved fix completed.",
                    payload={"reason": "fix_completed_validation_required"},
                )
                self._enqueue_outbox(run_id, "agent.plan_requested", {"reason": "fix_completed_validation_required"})
            elif status == CommandExecutionStatus.COMPLETED:
                self._runs[run_id] = run.model_copy(update={"status": RunStatus.INVESTIGATING, "pending_step": None})
                self._append_event(
                    run_id,
                    actor="agent",
                    event_type="agent.plan_requested",
                    summary="Observation interpretation requested after command completion.",
                    payload={"reason": "observation_recorded", "command_execution_id": command_execution_id},
                )
                self._enqueue_outbox(run_id, "agent.plan_requested", {"reason": "observation_recorded"})
            elif _runner_failed_before_remote_observation(completed):
                self._runs[run_id] = run.model_copy(update={"status": RunStatus.FAILED, "pending_step": None})
            else:
                self._runs[run_id] = run.model_copy(update={"status": RunStatus.INVESTIGATING, "pending_step": None})
                self._append_event(
                    run_id,
                    actor="agent",
                    event_type="agent.plan_requested",
                    summary="Follow-up planning requested after command failure.",
                    payload={"reason": "command_failed", "command_execution_id": command_execution_id},
                )
                self._enqueue_outbox(run_id, "agent.plan_requested", {"reason": "command_failed"})
        event_type = {
            CommandExecutionStatus.COMPLETED: "command.completed",
            CommandExecutionStatus.TIMED_OUT: "command.timed_out",
            CommandExecutionStatus.FAILED: "command.failed",
        }.get(status, "command.failed")
        summary = "Command completed." if status == CommandExecutionStatus.COMPLETED else error or "Command failed."
        self._append_event(
            run_id,
            actor="ssh_runner",
            event_type=event_type,
            summary=summary,
            command=completed.approved_command,
            sanitized_stdout=completed.sanitized_stdout,
            sanitized_stderr=completed.sanitized_stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            risk_class=step.risk_class,
            error=error,
            payload={
                "step_id": step.id,
                "command_execution_id": command_execution_id,
                "output_truncated": completed.output_truncated,
            },
        )
        if _remote_command_started(completed):
            self._record_ledgers_for_completed_command(run_id, completed, step)
            self._record_restore_completed_if_applicable(run_id, completed, step)
        return completed

    def list_command_executions(self, run_id: int) -> list[CommandExecution]:
        self.get_run(run_id)
        return [self._executions[execution_id] for execution_id in self._executions_by_run[run_id]]

    def list_command_output_chunks(self, run_id: int) -> list[CommandOutputChunk]:
        self.get_run(run_id)
        return [self._chunks[chunk_id] for chunk_id in self._chunks_by_run[run_id]]

    def list_redaction_events(self, run_id: int) -> list[RedactionEvent]:
        self.get_run(run_id)
        return [self._redaction_events[event_id] for event_id in self._redaction_events_by_run[run_id]]

    def list_inspected_sources(self, run_id: int) -> list[InspectedSource]:
        self.get_run(run_id)
        return [self._inspected_sources[source_id] for source_id in self._inspected_sources_by_run[run_id]]

    def list_validation_results(self, run_id: int) -> list[ValidationResult]:
        self.get_run(run_id)
        return [self._validation_results[result_id] for result_id in self._validation_results_by_run[run_id]]

    def list_validation_expectations(self, run_id: int) -> list[ValidationExpectation]:
        self.get_run(run_id)
        return [self._validation_expectations[expectation_id] for expectation_id in self._validation_expectations_by_run[run_id]]

    def list_backup_records(self, run_id: int) -> list[BackupRecord]:
        self.get_run(run_id)
        return [self._backup_records[record_id] for record_id in self._backup_records_by_run[run_id]]

    def record_backup_not_applicable(
        self,
        run_id: int,
        *,
        source_path: str | None,
        reason: str,
        recorded_by: str,
    ) -> BackupRecord:
        run = self.get_run(run_id)
        record = BackupRecord(
            id=self._next_backup_record_id,
            run_id=run_id,
            ticket_id=run.ticket_id,
            command_execution_id=None,
            source_path=source_path,
            backup_path=None,
            backup_type="not_applicable",
            reason=reason,
            restore_command=None,
            stored_content=False,
            redacted=False,
            backup_required=False,
            backup_created=False,
            persistent_across_reboot=False,
            created_at=_utc_now(),
        )
        self._next_backup_record_id += 1
        self._backup_records[record.id] = record
        self._backup_records_by_run[run_id].append(record.id)
        self._append_event(
            run_id,
            actor="technician",
            event_type="backup.not_applicable",
            summary=f"Backup marked not applicable by {recorded_by}: {reason}",
            payload={"source_path": source_path, "backup_record_id": record.id, "recorded_by": recorded_by},
        )
        return record

    def propose_restore_command(
        self,
        run_id: int,
        backup_record_id: int,
        *,
        proposed_by: str,
        reason: str,
    ) -> ProposedStep:
        record = self._backup_records.get(backup_record_id)
        if not record or record.run_id != run_id:
            raise RunTransitionError(f"Backup record {backup_record_id} does not belong to run {run_id}")
        if not record.restore_command:
            raise RunTransitionError("Backup record has no restore command")
        step = self._create_step(
            run_id,
            source="restore",
            command=record.restore_command,
            actor="technician",
            actor_name=proposed_by,
            purpose=reason,
            expected_signal=f"Restore command exits 0 for backup record #{record.id}.",
            phase="restore",
            timeout_s=None,
        )
        self._append_event(
            run_id,
            actor="backup_service",
            event_type="backup.restore_proposed",
            summary=f"Restore command proposed from backup record #{record.id}.",
            command=record.restore_command,
            risk_class=step.risk_class,
            payload={
                "backup_record_id": record.id,
                "step_id": step.id,
                "restore_command": record.restore_command,
                "proposed_by": proposed_by,
                "reason": reason,
            },
        )
        return step

    def create_activity_draft(self, run_id: int) -> ActivityDraft:
        run = self.get_run(run_id)
        self._assert_activity_ready(run)
        self._assert_activity_evidence_sufficient(run)
        draft = build_activity_draft(
            run=run,
            events=self.list_events(run_id),
            commands=self.list_command_executions(run_id),
            inspected_sources=self.list_inspected_sources(run_id),
            backup_records=self.list_backup_records(run_id),
            validation_results=self.list_validation_results(run_id),
        )
        return self.save_activity_draft(run_id, draft)

    def save_activity_draft(self, run_id: int, draft: ActivityDraft, *, edited_by: str | None = None) -> ActivityDraft:
        run = self.get_run(run_id)
        self._assert_activity_ready(run)
        draft, redacted_fields = _redact_activity_draft(draft)
        self._assert_activity_draft_complete(run, draft)
        activity_draft_id = self._next_activity_draft_id
        self._next_activity_draft_id += 1
        self._activity_draft_ids_by_run[run_id] = activity_draft_id
        self._activity_drafts[run_id] = draft
        self._runs[run_id] = run.model_copy(update={"activity_draft": draft.model_dump(mode="json")})
        for field_name in redacted_fields:
            self._record_redaction_event(
                run_id,
                surface="activity",
                field_name=field_name,
                activity_draft_id=activity_draft_id,
            )
        event_type = "activity.draft_edited" if edited_by else "agent.activity_draft_generated"
        actor = "technician" if edited_by else "activity_writer"
        summary = (
            f"Phoenix activity draft edited and saved by {edited_by}."
            if edited_by
            else "Phoenix activity draft generated from run audit, commands, evidence, and backups."
        )
        self._append_event(
            run_id,
            actor=actor,
            event_type=event_type,
            summary=summary,
            payload={"ticket_id": draft.ticket_id, "activity_draft_id": activity_draft_id, "edited_by": edited_by},
        )
        return draft

    def queue_activity_submission(self, run_id: int) -> IntegrationRequest:
        run = self.get_run(run_id)
        self._assert_activity_ready(run)
        draft = self._activity_drafts.get(run_id)
        if not draft and run.activity_draft:
            draft = ActivityDraft.model_validate(run.activity_draft)
        if not draft:
            raise RunTransitionError("Activity submission requires a saved draft")
        self._assert_activity_draft_complete(run, draft)
        for request in reversed(self.list_integration_requests(run_id)):
            if request.status not in {IntegrationRequestStatus.COMPLETED, IntegrationRequestStatus.DEAD_LETTER}:
                return request
        request = IntegrationRequest(
            id=self._next_integration_request_id,
            run_id=run_id,
            ticket_id=run.ticket_id,
            activity_draft_id=self._activity_draft_ids_by_run.get(run_id),
            request_type="phoenix_activity_submission",
            status=IntegrationRequestStatus.PENDING,
            activity_payload=draft.model_dump(mode="json", exclude_none=True),
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        self._next_integration_request_id += 1
        self._integration_requests[request.id] = request
        self._integration_requests_by_run[run_id].append(request.id)
        self._append_event(
            run_id,
            actor="technician",
            event_type="activity.submission_requested",
            summary="Phoenix activity submission queued for durable worker processing.",
            payload={
                "integration_request_id": request.id,
                "activity_draft_id": request.activity_draft_id,
                "ticket_id": draft.ticket_id,
            },
        )
        self._cancel_pending_outbox_event(run_id, "agent.activity_draft_requested")
        self._enqueue_outbox(
            run_id,
            "integration.activity_submission_requested",
            {"integration_request_id": request.id},
        )
        return request

    def mark_integration_request_processing(self, run_id: int, integration_request_id: int) -> IntegrationRequest:
        request = self.get_integration_request(run_id, integration_request_id)
        if request.status == IntegrationRequestStatus.COMPLETED:
            return request
        updated = request.model_copy(
            update={
                "status": IntegrationRequestStatus.PROCESSING,
                "attempts": request.attempts + 1,
                "updated_at": _utc_now(),
                "error": None,
            }
        )
        self._integration_requests[integration_request_id] = updated
        return updated

    def mark_integration_activity_created(
        self,
        run_id: int,
        integration_request_id: int,
        *,
        phoenix_activity_id: int,
    ) -> IntegrationRequest:
        request = self.get_integration_request(run_id, integration_request_id)
        updated = request.model_copy(
            update={
                "status": IntegrationRequestStatus.ACTIVITY_CREATED,
                "phoenix_activity_id": phoenix_activity_id,
                "updated_at": _utc_now(),
                "error": None,
            }
        )
        self._integration_requests[integration_request_id] = updated
        self._append_event(
            run_id,
            actor="phoenix",
            event_type="activity.created",
            summary="Phoenix activity was created; ticket status update remains in progress.",
            payload={"integration_request_id": integration_request_id, "submitted_activity_id": phoenix_activity_id},
        )
        return updated

    def mark_integration_request_completed(
        self,
        run_id: int,
        integration_request_id: int,
        *,
        ticket_status: str,
    ) -> Run:
        request = self.get_integration_request(run_id, integration_request_id)
        updated_request = request.model_copy(
            update={
                "status": IntegrationRequestStatus.COMPLETED,
                "ticket_status": ticket_status,
                "updated_at": _utc_now(),
                "completed_at": _utc_now(),
                "error": None,
            }
        )
        self._integration_requests[integration_request_id] = updated_request
        run = self.get_run(run_id)
        updated_run = run.model_copy(update={"status": RunStatus.SUBMITTED, "ended_at": _utc_now()})
        self._runs[run_id] = updated_run
        self._append_event(
            run_id,
            actor="phoenix",
            event_type="activity.submitted",
            summary="Phoenix activity submitted for troubleshooting run.",
            payload={
                "integration_request_id": integration_request_id,
                "submitted_activity_id": request.phoenix_activity_id,
                "ticket_id": request.ticket_id,
            },
        )
        self._append_event(
            run_id,
            actor="phoenix",
            event_type="ticket.status_updated",
            summary="Phoenix ticket status updated to DONE after activity submission.",
            payload={"integration_request_id": integration_request_id, "ticket_id": request.ticket_id, "status": ticket_status},
        )
        return updated_run

    def fail_integration_request(
        self,
        run_id: int,
        integration_request_id: int,
        *,
        error: str,
        terminal: bool = False,
    ) -> IntegrationRequest:
        request = self.get_integration_request(run_id, integration_request_id)
        if request.status == IntegrationRequestStatus.ACTIVITY_CREATED and not terminal:
            status = IntegrationRequestStatus.ACTIVITY_CREATED
        else:
            status = IntegrationRequestStatus.DEAD_LETTER if terminal else IntegrationRequestStatus.FAILED
        updated = request.model_copy(update={"status": status, "error": error, "updated_at": _utc_now()})
        self._integration_requests[integration_request_id] = updated
        self._append_event(
            run_id,
            actor="phoenix",
            event_type="integration.failed",
            summary=error,
            payload={
                "integration_request_id": integration_request_id,
                "status": status.value,
                "phoenix_activity_id": request.phoenix_activity_id,
            },
            error=error,
        )
        return updated

    def _create_step(
        self,
        run_id: int,
        *,
        source: str,
        command: str,
        actor: str,
        actor_name: str,
        purpose: str,
        expected_signal: str | None,
        phase: str,
        timeout_s: int | None,
    ) -> ProposedStep:
        run = self.get_run(run_id)
        if run.status in {RunStatus.ABORTED, RunStatus.SUBMITTED, RunStatus.FAILED}:
            raise RunTransitionError("Terminal runs cannot accept new steps")
        if run.status == RunStatus.AWAITING_STEP_APPROVAL and run.pending_step:
            raise RunTransitionError("A run can only have one pending step")

        safety = classify_command(command)
        now = _utc_now()
        step = ProposedStep(
            id=self._next_step_id,
            run_id=run_id,
            created_at=now,
            updated_at=now,
            source=source,
            phase=phase,
            command=command,
            purpose=purpose,
            expected_signal=expected_signal,
            risk_class=safety.risk_class,
            safety_verdict=safety.verdict,
            safety_summary=safety.summary,
            safety_notes=safety.notes,
            status=StepStatus.BLOCKED if safety.verdict == "blocked" else StepStatus.PROPOSED,
            timeout_s=timeout_s or self.command_timeout_s,
        )
        self._next_step_id += 1
        self._steps[step.id] = step
        self._steps_by_run[run_id].append(step.id)

        if source == "manual":
            self._append_event(
                run_id,
                actor=actor,
                event_type="manual_step.entered",
                summary=f"Manual command entered by {actor_name}.",
                command=command,
                payload={"step_id": step.id, "entered_by": actor_name, "purpose": purpose},
            )
        self._append_event(
            run_id,
            actor=actor,
            event_type="step.proposed",
            summary=purpose,
            command=command,
            risk_class=step.risk_class,
            payload={"step_id": step.id, "source": source, "phase": phase, "expected_signal": expected_signal},
        )
        self._append_event(
            run_id,
            actor="safety_layer",
            event_type="step.safety_classified",
            summary=safety.summary,
            command=command,
            risk_class=safety.risk_class,
            approval_status=safety.verdict if safety.verdict == "allowed" else "blocked",
            payload=_safety_event_payload(step.id, safety),
        )
        requirement = backup_requirement_for_command(command, step.risk_class)
        if requirement.required:
            self._record_backup_plan(run_id, run.ticket_id, step.id, command, requirement)

        if safety.verdict == "blocked":
            self._runs[run_id] = run.model_copy(update={"status": _idle_status_for_run(run), "pending_step": None})
        else:
            self._runs[run_id] = run.model_copy(
                update={"status": RunStatus.AWAITING_STEP_APPROVAL, "pending_step": step.model_dump(mode="json")}
            )
        return step

    def _record_backup_plan(
        self,
        run_id: int,
        ticket_id: int,
        step_id: int,
        command: str,
        requirement: Any,
    ) -> BackupRecord:
        for record in self.list_backup_records(run_id):
            if (
                record.backup_required
                and not record.backup_created
                and record.backup_type == requirement.backup_type
                and record.source_path == requirement.source_path
            ):
                return record
        plan = backup_plan_for_requirement(run_id=run_id, ticket_id=ticket_id, requirement=requirement)
        record = BackupRecord(
            id=self._next_backup_record_id,
            run_id=run_id,
            ticket_id=ticket_id,
            command_execution_id=None,
            source_path=requirement.source_path,
            backup_path=plan.backup_path,
            backup_type=requirement.backup_type,
            reason=requirement.reason,
            restore_command=plan.restore_command,
            stored_content=False,
            redacted=False,
            backup_required=True,
            backup_created=False,
            persistent_across_reboot=plan.persistent_across_reboot,
            created_at=_utc_now(),
        )
        self._next_backup_record_id += 1
        self._backup_records[record.id] = record
        self._backup_records_by_run[run_id].append(record.id)
        self._append_event(
            run_id,
            actor="backup_service",
            event_type="backup.planned",
            summary=requirement.reason,
            command=command,
            payload={
                "step_id": step_id,
                "source_path": requirement.source_path,
                "backup_type": requirement.backup_type,
                "backup_record_id": record.id,
                "backup_path": plan.backup_path,
                "restore_command": plan.restore_command,
            },
        )
        return record

    def _assert_step_approvable(self, run: Run, step: ProposedStep) -> None:
        if run.status != RunStatus.AWAITING_STEP_APPROVAL:
            raise RunTransitionError("A step can only be approved while approval is pending")
        if step.status != StepStatus.PROPOSED:
            raise RunTransitionError("Only proposed steps can be approved")
        if step.safety_verdict == "blocked":
            raise RunTransitionError("Blocked steps cannot be approved")

    def _assert_fix_policy(self, run: Run, step: ProposedStep) -> None:
        if step.phase != "fix":
            return
        if step.risk_class == "READ_ONLY" or is_targeted_backup_command(step.command):
            return
        evidence_sources = [
            source
            for source in self.list_inspected_sources(run.id)
            if source.supports in {"root_cause", "fix_choice"}
        ]
        if not evidence_sources:
            self._append_event(
                run.id,
                actor="safety_layer",
                event_type="fix.approval_blocked",
                summary="Fix approval blocked because no root-cause or fix-choice evidence has been recorded.",
                command=step.command,
                risk_class=step.risk_class,
                approval_status="blocked",
                payload={"step_id": step.id, "reason": "missing_fix_evidence"},
            )
            raise RunTransitionError("Fix approval requires root-cause or fix-choice evidence before execution")

        service = _service_action_target(step.command)
        if service:
            related_services = _related_services_for_run(run, evidence_sources)
            if related_services and service not in related_services:
                self._append_event(
                    run.id,
                    actor="safety_layer",
                    event_type="fix.approval_blocked",
                    summary=f"Fix approval blocked because {service} is an unrelated service for the recorded evidence.",
                    command=step.command,
                    risk_class=step.risk_class,
                    approval_status="blocked",
                    payload={
                        "step_id": step.id,
                        "reason": "unrelated_service",
                        "service": service,
                        "related_services": sorted(related_services),
                    },
                )
                raise RunTransitionError("Fix command targets an unrelated service for the recorded evidence")

        self._append_event(
            run.id,
            actor="safety_layer",
            event_type="fix.evidence_verified",
            summary="Fix proposal references recorded root-cause or fix-choice evidence.",
            command=step.command,
            risk_class=step.risk_class,
            payload={"step_id": step.id, "inspected_source_ids": [source.id for source in evidence_sources]},
        )

    def _assert_backup_requirement_satisfied(self, run: Run, step: ProposedStep) -> None:
        requirement = backup_requirement_for_command(step.command, step.risk_class)
        if not requirement.required:
            return
        records = self.list_backup_records(run.id)
        if any(
            backup_record_satisfies(
                source_path=requirement.source_path,
                record_source_path=record.source_path,
                record_type=record.backup_type,
                backup_created=record.backup_created,
            )
            for record in records
        ):
            return
        self._append_event(
            run.id,
            actor="backup_service",
            event_type="backup.approval_requested",
            summary=requirement.reason,
            command=step.command,
            payload={"step_id": step.id, "source_path": requirement.source_path, "backup_type": requirement.backup_type},
        )
        raise RunTransitionError("Medium-risk persistent changes require a matching backup record or backup.not_applicable event before approval")

    def _queue_command_execution(self, run_id: int, step_id: int) -> None:
        self._append_event(
            run_id,
            actor="system",
            event_type="command.execution_requested",
            summary="Approved command queued for worker execution.",
            payload={"step_id": step_id},
        )
        self._enqueue_outbox(run_id, "command.execution_requested", {"step_id": step_id})

    def _enqueue_outbox(self, run_id: int | None, event_type: str, payload: JsonObject) -> OutboxEvent:
        event = OutboxEvent(
            id=self._next_outbox_id,
            run_id=run_id,
            event_type=event_type,
            payload=deepcopy(payload),
            status=OutboxStatus.PENDING,
            attempts=0,
            available_at=_utc_now(),
            created_at=_utc_now(),
        )
        self._next_outbox_id += 1
        self._outbox[event.id] = event
        return event

    def _cancel_pending_agent_plan(self, run_id: int) -> None:
        self._cancel_pending_outbox_event(run_id, "agent.plan_requested")

    def _cancel_pending_outbox_event(self, run_id: int, event_type: str) -> None:
        for outbox_id, event in list(self._outbox.items()):
            if (
                event.run_id == run_id
                and event.event_type == event_type
                and event.status == OutboxStatus.PENDING
            ):
                self._outbox[outbox_id] = event.model_copy(
                    update={"status": OutboxStatus.COMPLETED, "completed_at": _utc_now()}
                )

    def _append_event(
        self,
        run_id: int,
        *,
        actor: str,
        event_type: str,
        summary: str,
        payload: JsonObject,
        command: str | None = None,
        sanitized_stdout: str | None = None,
        sanitized_stderr: str | None = None,
        exit_code: int | None = None,
        duration_ms: int | None = None,
        risk_class: str | None = None,
        approval_status: str | None = None,
        error: str | None = None,
    ) -> RunEvent:
        event = RunEvent(
            id=self._next_event_id,
            run_id=run_id,
            created_at=_utc_now(),
            actor=actor,
            event_type=event_type,
            summary=summary,
            command=command,
            sanitized_stdout=sanitized_stdout,
            sanitized_stderr=sanitized_stderr,
            exit_code=exit_code,
            duration_ms=duration_ms,
            risk_class=risk_class,
            approval_status=approval_status,
            error=error,
            payload=deepcopy(payload),
        )
        self._next_event_id += 1
        self._events[run_id].append(event)
        return event

    def _record_redaction_event(
        self,
        run_id: int,
        *,
        surface: str,
        field_name: str,
        command_execution_id: int | None = None,
        inspected_source_id: int | None = None,
        activity_draft_id: int | None = None,
    ) -> RedactionEvent:
        event = RedactionEvent(
            id=self._next_redaction_event_id,
            run_id=run_id,
            command_execution_id=command_execution_id,
            inspected_source_id=inspected_source_id,
            activity_draft_id=activity_draft_id,
            surface=surface,
            field_name=field_name,
            created_at=_utc_now(),
        )
        self._next_redaction_event_id += 1
        self._redaction_events[event.id] = event
        self._redaction_events_by_run[run_id].append(event.id)
        return event

    def _get_execution(self, run_id: int, command_execution_id: int) -> CommandExecution:
        execution = self._executions.get(command_execution_id)
        if not execution or execution.run_id != run_id:
            raise RunTransitionError(f"Command execution {command_execution_id} does not belong to run {run_id}")
        return execution

    def _mark_execution_truncated(self, run_id: int, execution: CommandExecution) -> None:
        current = self._executions[execution.id]
        if current.output_truncated:
            return
        self._executions[execution.id] = current.model_copy(update={"output_truncated": True})
        self._append_event(
            run_id,
            actor="ssh_runner",
            event_type="terminal.output_truncated",
            summary="Command output exceeded the configured storage cap.",
            payload={"command_execution_id": execution.id, "output_limit_bytes": execution.output_limit_bytes},
        )

    def _record_ledgers_for_completed_command(
        self,
        run_id: int,
        execution: CommandExecution,
        step: ProposedStep,
    ) -> None:
        redacted = any(chunk.redacted for chunk in self.list_command_output_chunks(run_id) if chunk.command_execution_id == execution.id)
        for source in detect_inspected_sources(
            command=execution.approved_command,
            sanitized_stdout=execution.sanitized_stdout,
            sanitized_stderr=execution.sanitized_stderr,
            purpose=step.purpose,
            phase=step.phase,
            redacted=redacted,
        ):
            self._record_inspected_source(run_id, execution, step, source)

        detected_backup = detect_backup_record(
            run_id=run_id,
            ticket_id=self.get_run(run_id).ticket_id,
            command_execution_id=execution.id,
            command=execution.approved_command,
            output=f"{execution.sanitized_stdout}\n{execution.sanitized_stderr}",
        )
        if detected_backup:
            self._record_backup_created(run_id, execution, detected_backup)

    def _record_inspected_source(
        self,
        run_id: int,
        execution: CommandExecution,
        step: ProposedStep,
        source: Any,
    ) -> InspectedSource:
        inspected_source = InspectedSource(
            id=self._next_inspected_source_id,
            run_id=run_id,
            command_execution_id=execution.id,
            source_type=source.source_type,
            source_name=source.source_name,
            path=source.path,
            command=execution.approved_command,
            actor=step.source,
            purpose=source.purpose,
            finding=source.finding,
            supports=source.supports,
            sanitized_excerpt=source.sanitized_excerpt,
            redacted=source.redacted,
            line_range=source.line_range,
            created_at=_utc_now(),
        )
        self._next_inspected_source_id += 1
        self._inspected_sources[inspected_source.id] = inspected_source
        self._inspected_sources_by_run[run_id].append(inspected_source.id)
        self._append_event(
            run_id,
            actor="evidence_detector",
            event_type="evidence.source_detected",
            summary=f"{inspected_source.source_type} source recorded: {inspected_source.path or inspected_source.source_name}",
            command=execution.approved_command,
            payload={
                "inspected_source_id": inspected_source.id,
                "command_execution_id": execution.id,
                "source_type": inspected_source.source_type,
                "source_name": inspected_source.source_name,
                "path": inspected_source.path,
                "supports": inspected_source.supports,
                "finding": inspected_source.finding,
                "redacted": inspected_source.redacted,
            },
        )
        if inspected_source.redacted:
            self._append_event(
                run_id,
                actor="evidence_detector",
                event_type="evidence.source_redacted",
                summary="Evidence source excerpt contained redacted secret-like material.",
                command=execution.approved_command,
                payload={"inspected_source_id": inspected_source.id},
            )
            self._record_redaction_event(
                run_id,
                command_execution_id=execution.id,
                inspected_source_id=inspected_source.id,
                surface="evidence",
                field_name="sanitized_excerpt",
            )
        return inspected_source

    def _record_validation_result(self, run_id: int, execution: CommandExecution) -> ValidationResult:
        evaluation = _evaluate_validation_result(execution)
        result = ValidationResult(
            id=self._next_validation_result_id,
            run_id=run_id,
            command_execution_id=execution.id,
            check_type=evaluation["check_type"],
            target=evaluation["target"],
            passed=bool(evaluation["passed"]),
            summary=evaluation["summary"],
            evidence=evaluation["evidence"],
            created_at=_utc_now(),
        )
        self._next_validation_result_id += 1
        self._validation_results[result.id] = result
        self._validation_results_by_run[run_id].append(result.id)
        self._append_event(
            run_id,
            actor="validation",
            event_type="validation.passed" if result.passed else "validation.failed",
            summary=result.summary,
            command=execution.approved_command,
            payload={
                "validation_result_id": result.id,
                "command_execution_id": execution.id,
                "check_type": result.check_type,
                "target": result.target,
                "passed": result.passed,
                "evidence": result.evidence,
            },
        )
        return result

    def _create_validation_expectations_for_fix(
        self,
        run_id: int,
        execution: CommandExecution,
        step: ProposedStep,
    ) -> list[ValidationExpectation]:
        run = self.get_run(run_id)
        expectations: list[ValidationExpectation] = []
        for spec in _validation_expectation_specs(run, execution, step):
            expectation = ValidationExpectation(
                id=self._next_validation_expectation_id,
                run_id=run_id,
                fix_command_execution_id=execution.id,
                check_type=spec["check_type"],
                target=spec["target"],
                expected_result=spec["expected_result"],
                relation_to_customer_symptom=spec["relation_to_customer_symptom"],
                required=True,
                status="pending",
                validation_result_id=None,
                created_at=_utc_now(),
                updated_at=None,
            )
            self._next_validation_expectation_id += 1
            self._validation_expectations[expectation.id] = expectation
            self._validation_expectations_by_run[run_id].append(expectation.id)
            expectations.append(expectation)
        return expectations

    def _apply_validation_result_to_suite(self, run_id: int, result: ValidationResult) -> str:
        expectations = self._latest_validation_expectations(run_id)
        if not expectations:
            fix_completed = self._has_completed_fix_execution(run_id)
            self._append_event(
                run_id,
                actor="validation",
                event_type="validation.suite_missing",
                summary=(
                    "Validation result recorded, but no required validation suite exists for the latest fix."
                    if fix_completed
                    else "Validation result recorded for a run with no completed fix command."
                ),
                command=self._get_execution(run_id, result.command_execution_id).approved_command,
                payload={"validation_result_id": result.id},
            )
            if fix_completed:
                return "incomplete"
            return "passed" if result.passed else "failed"

        match = _matching_validation_expectation(expectations, result)
        if match and match.status == "pending":
            updated = match.model_copy(
                update={
                    "status": "passed" if result.passed else "failed",
                    "validation_result_id": result.id,
                    "updated_at": _utc_now(),
                }
            )
            self._validation_expectations[match.id] = updated
            self._append_event(
                run_id,
                actor="validation",
                event_type="validation.expectation_updated",
                summary=f"{match.check_type} validation expectation marked {updated.status}.",
                payload={
                    "validation_expectation_id": match.id,
                    "validation_result_id": result.id,
                    "check_type": match.check_type,
                    "status": updated.status,
                },
            )
            expectations = self._latest_validation_expectations(run_id)

        return _validation_suite_status(expectations)

    def _latest_validation_expectations(self, run_id: int) -> list[ValidationExpectation]:
        expectations = self.list_validation_expectations(run_id)
        if not expectations:
            return []
        latest_fix_id = next(
            (
                expectation.fix_command_execution_id
                for expectation in reversed(expectations)
                if expectation.fix_command_execution_id is not None
            ),
            None,
        )
        if latest_fix_id is None:
            return expectations
        return [expectation for expectation in expectations if expectation.fix_command_execution_id == latest_fix_id]

    def _record_backup_created(self, run_id: int, execution: CommandExecution, detected_backup: Any) -> BackupRecord:
        run = self.get_run(run_id)
        record = BackupRecord(
            id=self._next_backup_record_id,
            run_id=run_id,
            ticket_id=run.ticket_id,
            command_execution_id=execution.id,
            source_path=detected_backup.source_path,
            backup_path=detected_backup.backup_path,
            backup_type=detected_backup.backup_type,
            reason=detected_backup.reason,
            restore_command=detected_backup.restore_command,
            stored_content=detected_backup.stored_content,
            redacted=detected_backup.redacted,
            pre_change_hash=detected_backup.pre_change_hash,
            owner_before=detected_backup.owner_before,
            group_before=detected_backup.group_before,
            mode_before=detected_backup.mode_before,
            size_before=detected_backup.size_before,
            mtime_before=detected_backup.mtime_before,
            checksum_before=detected_backup.checksum_before,
            sanitized_diff=detected_backup.sanitized_diff,
            backup_required=True,
            backup_created=True,
            persistent_across_reboot=detected_backup.persistent_across_reboot,
            created_at=_utc_now(),
        )
        self._next_backup_record_id += 1
        self._backup_records[record.id] = record
        self._backup_records_by_run[run_id].append(record.id)
        self._append_event(
            run_id,
            actor="backup_service",
            event_type="backup.created",
            summary=f"Targeted rollback record created for {record.source_path}.",
            command=execution.approved_command,
            payload={
                "backup_record_id": record.id,
                "command_execution_id": execution.id,
                "source_path": record.source_path,
                "backup_path": record.backup_path,
                "restore_command": record.restore_command,
                "persistent_across_reboot": record.persistent_across_reboot,
            },
        )
        return record

    def _record_restore_completed_if_applicable(
        self,
        run_id: int,
        execution: CommandExecution,
        step: ProposedStep,
    ) -> None:
        if step.phase != "restore" or execution.status != CommandExecutionStatus.COMPLETED:
            return
        record = next(
            (
                backup_record
                for backup_record in self.list_backup_records(run_id)
                if backup_record.restore_command == execution.approved_command
            ),
            None,
        )
        if not record:
            return
        self._append_event(
            run_id,
            actor="backup_service",
            event_type="backup.restored",
            summary=f"Restore command completed for backup record #{record.id}.",
            command=execution.approved_command,
            payload={
                "backup_record_id": record.id,
                "step_id": step.id,
                "command_execution_id": execution.id,
                "source_path": record.source_path,
                "backup_path": record.backup_path,
            },
        )

    def _assert_activity_ready(self, run: Run) -> None:
        expectations = self._latest_validation_expectations(run.id)
        suite_status = _validation_suite_status(expectations)
        if suite_status == "failed":
            raise RunTransitionError("A failed validation check requires a new fix and validation suite before activity drafting")
        validation_only_ready = (
            not expectations
            and not self._has_completed_fix_execution(run.id)
            and any(result.passed for result in self.list_validation_results(run.id))
        )
        if suite_status != "passed" and not validation_only_ready:
            raise RunTransitionError("Activity draft and submission require a completed validation suite")
        if run.status != RunStatus.READY_FOR_ACTIVITY:
            raise RunTransitionError("Activity draft and submission require the run to be ready for activity")

    def _assert_activity_evidence_sufficient(self, run: Run) -> None:
        if not any(source.supports == "root_cause" for source in self.list_inspected_sources(run.id)):
            raise RunTransitionError("Activity generation requires concrete root-cause evidence before drafting")

    def _has_completed_fix_execution(self, run_id: int) -> bool:
        for execution_id in self._executions_by_run[run_id]:
            execution = self._executions[execution_id]
            step = self._steps.get(execution.proposed_step_id)
            if step and step.phase == "fix" and execution.status == CommandExecutionStatus.COMPLETED:
                return True
        return False

    def _assert_activity_draft_complete(self, run: Run, draft: ActivityDraft) -> None:
        if draft.ticket_id != run.ticket_id:
            raise RunTransitionError("Activity draft ticket_id must match the troubleshooting run")
        required_fields = [
            "summary",
            "root_cause",
            "actions_taken",
            "commands_summary",
            "validation_result",
        ]
        missing = [field for field in required_fields if not (getattr(draft, field) or "").strip()]
        if missing:
            raise RunTransitionError(f"Activity draft is missing required field(s): {', '.join(missing)}")



@lru_cache
def get_postgres_run_store(
    database_url: str,
    command_timeout_s: int = 30,
    command_output_limit_bytes: int = 200_000,
) -> PostgresRunStore:
    return PostgresRunStore(
        database_url,
        command_timeout_s=command_timeout_s,
        command_output_limit_bytes=command_output_limit_bytes,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _outbox_backoff_seconds(attempts: int) -> int:
    return min(60, 2 ** max(attempts, 1))


_ACTIVITY_REDACTABLE_FIELDS = (
    "description",
    "summary",
    "root_cause",
    "actions_taken",
    "commands_summary",
    "validation_result",
)


def _redact_activity_draft(draft: ActivityDraft) -> tuple[ActivityDraft, list[str]]:
    payload = draft.model_dump()
    redacted_fields: list[str] = []
    for field_name in _ACTIVITY_REDACTABLE_FIELDS:
        value = payload.get(field_name)
        if not isinstance(value, str):
            continue
        sanitized_value, changed = _sanitize_activity_text(value)
        if changed:
            payload[field_name] = sanitized_value
            redacted_fields.append(field_name)
    return ActivityDraft.model_validate(payload), redacted_fields


def _sanitize_activity_text(value: str) -> tuple[str, bool]:
    redacted, changed = redact_output(value)
    kept_lines: list[str] = []
    omitted_noise = False
    for line in redacted.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if (
            "terminal.output_chunk" in lowered
            or lowered.startswith("stdout>")
            or lowered.startswith("stderr>")
            or lowered.startswith("stdout:")
            or lowered.startswith("stderr:")
        ):
            omitted_noise = True
            continue
        kept_lines.append(stripped)
    if len(kept_lines) > 12:
        kept_lines = kept_lines[:12]
        omitted_noise = True
    if omitted_noise:
        kept_lines.append("[omitted noisy raw output]")
    sanitized = "\n".join(kept_lines).strip()
    if not sanitized:
        sanitized = "[omitted noisy raw output]"
    return sanitized, changed or sanitized != value.strip()


def _safety_event_payload(step_id: int, safety: Any, *, edited: bool = False) -> JsonObject:
    payload: JsonObject = {
        "step_id": step_id,
        "verdict": safety.verdict,
        "risk_class": safety.risk_class,
        "summary": safety.summary,
        "notes": safety.notes,
    }
    if edited:
        payload["edited"] = True
    if safety.verdict == "blocked":
        payload["blocked_reason"] = safety.summary
    return payload


def _connection_target(customer_system_snapshot: JsonObject) -> JsonObject:
    system = customer_system_snapshot.get("system")
    if not isinstance(system, dict):
        return {}
    return {
        "ip": system.get("ip"),
        "port": system.get("port"),
        "username": system.get("username"),
        "os": system.get("os"),
    }


def _connection_summary(customer_system_snapshot: JsonObject) -> str:
    target = _connection_target(customer_system_snapshot)
    username = target.get("username") or "unknown-user"
    ip = target.get("ip") or "unknown-host"
    port = target.get("port") or 22
    return f"SSH connection approval requested for {username}@{ip}:{port}."


def _truncate_to_bytes(content: str, limit: int) -> tuple[str, bool]:
    encoded = content.encode("utf-8")
    if len(encoded) <= limit:
        return content, False
    truncated = encoded[: max(0, limit)]
    while truncated:
        try:
            return truncated.decode("utf-8"), True
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return "", True


def _final_execution_status(
    *,
    exit_code: int | None,
    timed_out: bool,
    error: str | None,
) -> CommandExecutionStatus:
    if timed_out:
        return CommandExecutionStatus.TIMED_OUT
    if error or exit_code not in {0, None}:
        return CommandExecutionStatus.FAILED
    return CommandExecutionStatus.COMPLETED


def _remote_command_started(execution: CommandExecution) -> bool:
    return (
        execution.exit_code is not None
        or execution.status == CommandExecutionStatus.TIMED_OUT
        or bool(execution.sanitized_stdout)
        or bool(execution.sanitized_stderr)
    )


def _runner_failed_before_remote_observation(execution: CommandExecution) -> bool:
    return execution.status == CommandExecutionStatus.FAILED and bool(execution.error) and not _remote_command_started(execution)


def _status_for_approved_step(step: ProposedStep) -> RunStatus:
    if step.phase == "fix":
        return RunStatus.FIXING
    if step.phase == "validation":
        return RunStatus.VALIDATING
    return RunStatus.EXECUTING


def _idle_status_for_run(run: Run) -> RunStatus:
    if run.status == RunStatus.VALIDATING:
        return RunStatus.VALIDATING
    return RunStatus.INVESTIGATING


_KNOWN_SERVICES = (
    "customer-status",
    "nginx",
    "apache2",
    "httpd",
    "postgresql",
    "mysql",
    "mariadb",
    "redis",
    "php-fpm",
    "docker",
)


def _normalize_service(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().removesuffix(".service").lower()


def _service_action_target(command: str) -> str | None:
    match = re.search(
        r"\b(?:sudo\s+-n\s+)?systemctl\s+(?:restart|reload|try-restart|enable|disable)(?:\s+--now)?\s+([A-Za-z0-9_.@:-]+)",
        command,
        re.IGNORECASE,
    )
    if match:
        return _normalize_service(match.group(1))
    return None


def _service_from_command_text(command: str) -> str | None:
    systemctl_match = re.search(
        r"\b(?:sudo\s+-n\s+)?systemctl\s+(?:status|is-active|restart|reload|try-restart|enable|disable)(?:\s+--now)?\s+([A-Za-z0-9_.@:-]+)",
        command,
        re.IGNORECASE,
    )
    if systemctl_match:
        return _normalize_service(systemctl_match.group(1))
    journal_match = re.search(r"\bjournalctl\b.*(?:-u|--unit(?:=|\s+))\s*([A-Za-z0-9_.@:-]+)", command, re.IGNORECASE)
    if journal_match:
        return _normalize_service(journal_match.group(1))
    lowered = command.lower()
    for service in _KNOWN_SERVICES:
        if re.search(rf"\b{re.escape(service)}\b", lowered):
            return service
    return None


def _related_services_for_run(run: Run, evidence_sources: list[InspectedSource]) -> set[str]:
    services = {
        service
        for service in (_normalize_service(source.source_name) for source in evidence_sources)
        if service
    }
    text = " ".join(
        str(value)
        for value in [
            run.ticket_snapshot.get("title"),
            run.ticket_snapshot.get("description"),
            run.customer_system_snapshot.get("system", {}).get("notes", ""),
            *(source.path or "" for source in evidence_sources),
            *(source.finding for source in evidence_sources),
            *(source.command for source in evidence_sources),
        ]
    ).lower()
    for service in _KNOWN_SERVICES:
        if re.search(rf"\b{re.escape(service)}\b", text):
            services.add(service)
    return services


def _validation_expectation_specs(
    run: Run,
    execution: CommandExecution,
    step: ProposedStep,
) -> list[JsonObject]:
    service = (
        _service_action_target(execution.approved_command)
        or _service_from_command_text(execution.approved_command)
        or _service_from_command_text(step.purpose)
        or _service_from_command_text(str(run.customer_system_snapshot.get("system", {}).get("notes", "")))
    )
    symptom = str(run.ticket_snapshot.get("description") or run.ticket_snapshot.get("title") or "the customer symptom")
    endpoint = _customer_validation_target(run)
    persistence_required = _requires_persistence_validation(execution.approved_command)
    restart_before_customer_check = persistence_required and not re.search(
        r"\bsystemctl\s+(?:restart|reload|try-restart|enable|disable)\b",
        execution.approved_command.lower(),
    )
    specs: list[JsonObject] = []
    if service:
        specs.append(
            {
                "check_type": "service_health",
                "target": service,
                "expected_result": f"{service} service reports active after the approved fix.",
                "relation_to_customer_symptom": f"The affected {service} service must be healthy before resolving: {symptom}",
            }
        )
    if restart_before_customer_check:
        specs.append(
            {
                "check_type": "persistence",
                "target": service,
                "expected_result": "The fix remains valid after the affected service is restarted or reloaded.",
                "relation_to_customer_symptom": "The repair should survive the relevant service lifecycle action.",
            }
        )
    if _has_customer_facing_symptom(run):
        specs.append(
            {
                "check_type": "customer_benefit",
                "target": endpoint,
                "expected_result": "The local customer-facing check returns a successful response.",
                "relation_to_customer_symptom": f"This directly verifies the reported customer symptom: {symptom}",
            }
        )
    if service:
        specs.append(
            {
                "check_type": "logs_clean",
                "target": service,
                "expected_result": "Recent logs no longer show the original error, or show it materially reduced.",
                "relation_to_customer_symptom": "The original service error must not continue after the fix.",
            }
        )
    if persistence_required and not restart_before_customer_check:
        specs.append(
            {
                "check_type": "persistence",
                "target": service,
                "expected_result": "The fix remains valid after the affected service is restarted or reloaded.",
                "relation_to_customer_symptom": "The repair should survive the relevant service lifecycle action.",
            }
        )
    public_validation_command = _ticket_public_validation_command(run)
    if public_validation_command:
        specs.append(
            {
                "check_type": "public_validation",
                "target": public_validation_command,
                "expected_result": "The ticket-required public validation command exits successfully.",
                "relation_to_customer_symptom": "The customer ticket explicitly requires this validation before resolution.",
            }
        )

    deduped: list[JsonObject] = []
    seen: set[tuple[str, str | None]] = set()
    for spec in specs:
        key = (str(spec["check_type"]), spec["target"] if isinstance(spec["target"], str) else None)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return deduped or [
        {
            "check_type": "other",
            "target": None,
            "expected_result": "A concrete validation command proves the customer symptom is resolved.",
            "relation_to_customer_symptom": f"Validation must address: {symptom}",
        }
    ]


def _has_customer_facing_symptom(run: Run) -> bool:
    text = " ".join(
        str(value)
        for value in [
            run.ticket_snapshot.get("title"),
            run.ticket_snapshot.get("description"),
            run.customer_system_snapshot.get("system", {}).get("notes", ""),
        ]
    ).lower()
    return any(
        marker in text
        for marker in ("customer", "http", "api", "endpoint", "web", "port", "connect", "reach", "down", "unavailable")
    )


def _customer_validation_target(run: Run) -> str:
    text = " ".join(
        str(value)
        for value in [
            run.ticket_snapshot.get("title"),
            run.ticket_snapshot.get("description"),
            run.customer_system_snapshot.get("system", {}).get("notes", ""),
        ]
    )
    urls = re.findall(r"https?://[^\s<>()\[\]{}\"']+", text)
    if urls:
        return urls[0].rstrip(".,;:!?")
    system = run.customer_system_snapshot.get("system", {})
    port = system.get("port") if isinstance(system, dict) else None
    if isinstance(port, int) and port not in {22, 2222}:
        return f"http://localhost:{port}"
    return "http://localhost"


def _ticket_public_validation_command(run: Run) -> str | None:
    text = "\n".join(
        str(value)
        for value in [
            run.ticket_snapshot.get("description"),
            run.customer_system_snapshot.get("system", {}).get("notes", ""),
        ]
    )
    match = re.search(
        r"(?im)\b(?:run|execute)[ \t]*:[ \t]*((?:sudo[ \t]+)?/[^\r\n`]+\.sh(?:[ \t]+[^\r\n`]*)?)",
        text,
    )
    if not match:
        return None
    return match.group(1).strip().rstrip(".,;")


def _requires_persistence_validation(command: str) -> bool:
    lowered = command.lower()
    if re.search(r"\bsystemctl\s+(?:restart|reload|try-restart|enable|disable)\b", lowered):
        return True
    parts = lowered.split()
    if len(parts) >= 3 and parts[:2] == ["sudo", "-n"]:
        parts = parts[2:]
    return bool(parts and parts[0].split("/")[-1] in {"sed", "tee", "install", "cp", "mv", "chmod", "chown"})


def _matching_validation_expectation(
    expectations: list[ValidationExpectation],
    result: ValidationResult,
) -> ValidationExpectation | None:
    pending = [expectation for expectation in expectations if expectation.status == "pending"]
    for expectation in pending:
        if expectation.check_type != result.check_type:
            continue
        if _targets_match(expectation.target, result.target):
            return expectation
    for expectation in pending:
        if expectation.check_type == result.check_type:
            return expectation
    return None


def _targets_match(expected: str | None, actual: str | None) -> bool:
    if not expected or not actual:
        return True
    return expected.strip().removesuffix(".service").lower() == actual.strip().removesuffix(".service").lower()


def _validation_suite_status(expectations: list[ValidationExpectation]) -> str:
    required = [expectation for expectation in expectations if expectation.required]
    if not required:
        return "missing"
    if any(expectation.status == "failed" for expectation in required):
        return "failed"
    if all(expectation.status == "passed" for expectation in required):
        return "passed"
    return "incomplete"


def _validation_summary(execution: CommandExecution) -> str:
    output = (execution.sanitized_stdout or execution.sanitized_stderr).strip()
    if output:
        return f"`{execution.approved_command}` returned {output[:240]}."
    return f"`{execution.approved_command}` completed successfully with exit code 0."


def _evaluate_validation_result(execution: CommandExecution) -> JsonObject:
    command = execution.approved_command
    output = (execution.sanitized_stdout or execution.sanitized_stderr or "").strip()
    evidence = (output or execution.error or "No output captured.").strip()[:1_000]
    check_type = _validation_check_type(command)
    target = _validation_target(command, check_type)
    passed = _validation_passed(execution, check_type, output)
    target_label = f" for {target}" if target else ""
    verdict = "passed" if passed else "failed"
    summary = f"{check_type.replace('_', ' ').title()} validation {verdict}{target_label}: {_validation_summary(execution)}"
    return {
        "check_type": check_type,
        "target": target,
        "passed": passed,
        "summary": summary,
        "evidence": evidence,
    }


def _validation_check_type(command: str) -> str:
    lowered = command.lower()
    if re.search(r"(?:^|\s)(?:sudo\s+)?/[^\s]+\.sh(?:\s|$)", lowered):
        return "public_validation"
    if re.search(r"\b(?:reboot|systemctl\s+reboot|shutdown\s+-r)\b", lowered):
        return "reboot"
    if "systemctl is-active" in lowered or "systemctl status" in lowered:
        return "service_health"
    if "curl " in f" {lowered}" or "ss " in f" {lowered}" or "nc " in f" {lowered}":
        return "customer_benefit"
    if "journalctl" in lowered or "/var/log/" in lowered:
        return "logs_clean"
    if "restart" in lowered or "reload" in lowered:
        return "persistence"
    return "other"


def _validation_target(command: str, check_type: str) -> str | None:
    if check_type == "service_health":
        match = re.search(r"\bsystemctl\s+(?:is-active|status)\s+([A-Za-z0-9_.@:-]+)", command)
        if match:
            return match.group(1).removesuffix(".service")
    if check_type == "customer_benefit":
        match = re.search(r"https?://[^\s'\"<>]+", command)
        if match:
            return match.group(0)
    if check_type == "logs_clean":
        match = re.search(r"\bjournalctl\b.*(?:-u|--unit(?:=|\s+))\s*([A-Za-z0-9_.@:-]+)", command)
        if match:
            return match.group(1).removesuffix(".service")
    if check_type == "persistence":
        match = re.search(r"\bsystemctl\s+(?:restart|reload|try-restart)\s+([A-Za-z0-9_.@:-]+)", command)
        if match:
            return match.group(1).removesuffix(".service")
    if check_type == "public_validation":
        return command
    return None


def _validation_passed(execution: CommandExecution, check_type: str, output: str) -> bool:
    if execution.status != CommandExecutionStatus.COMPLETED or execution.exit_code != 0:
        return False
    lowered_output = output.lower()
    if check_type == "service_health":
        return "active" in lowered_output and not any(marker in lowered_output for marker in ("inactive", "failed", "dead"))
    if check_type == "customer_benefit":
        status_match = re.search(r"\b(?:http/\d(?:\.\d)?\s+)?([1-5]\d\d)\b", lowered_output)
        if status_match:
            status_code = int(status_match.group(1))
            return 200 <= status_code < 400
        return not any(marker in lowered_output for marker in ("connection refused", "timed out", "failed", "error"))
    if check_type == "logs_clean":
        if re.search(r"\bno\b.{0,80}\b(error|errors|failed|failure|refused|timeout)\b", lowered_output):
            return True
        return not any(marker in lowered_output for marker in ("error", "failed", "failure", "refused", "timeout"))
    return True


from .postgres_run_store import PostgresRunStore  # noqa: E402
