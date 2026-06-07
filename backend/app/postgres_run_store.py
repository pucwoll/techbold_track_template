from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import Connection
from sqlalchemy import Engine
from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import select
from sqlalchemy import update

from .activity_generator import build_activity_draft
from .backup_service import (
    backup_record_satisfies,
    backup_plan_for_requirement,
    backup_requirement_for_command,
    detect_backup_record,
    is_targeted_backup_command,
)
from .database import create_database_engine, run_database_migrations
from .evidence_detector import detect_inspected_sources
from .persistence_mappers import (
    row_to_backup_record,
    row_to_command_execution,
    row_to_command_output_chunk,
    row_to_event,
    row_to_inspected_source,
    row_to_integration_request,
    row_to_outbox_event,
    row_to_redaction_event,
    row_to_run,
    row_to_step,
    row_to_validation_expectation,
    row_to_validation_result,
)
from .persistence_models import (
    ActivityDraftRecord,
    BackupRecordRecord,
    CommandExecutionRecord,
    CommandOutputChunkRecord,
    InspectedSourceRecord,
    IntegrationRequestRecord,
    OutboxEventRecord,
    ProposedStepRecord,
    RedactionEventRecord,
    RunEventRecord,
    RunRecord,
    ValidationExpectationRecord,
    ValidationResultRecord,
    claimable_outbox_events_query,
)
from .run_store import (
    RunNotFoundError,
    RunTransitionError,
    _connection_summary,
    _connection_target,
    _evaluate_validation_result,
    _final_execution_status,
    _idle_status_for_run,
    _matching_validation_expectation,
    _outbox_backoff_seconds,
    _redact_activity_draft,
    _related_services_for_run,
    _remote_command_started,
    _runner_failed_before_remote_observation,
    _safety_event_payload,
    _service_action_target,
    _status_for_approved_step,
    _truncate_to_bytes,
    _utc_now,
    _validation_expectation_specs,
    _validation_suite_status,
)
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


RUNS = RunRecord.__table__
RUN_EVENTS = RunEventRecord.__table__
PROPOSED_STEPS = ProposedStepRecord.__table__
COMMAND_EXECUTIONS = CommandExecutionRecord.__table__
COMMAND_OUTPUT_CHUNKS = CommandOutputChunkRecord.__table__
INSPECTED_SOURCES = InspectedSourceRecord.__table__
VALIDATION_RESULTS = ValidationResultRecord.__table__
VALIDATION_EXPECTATIONS = ValidationExpectationRecord.__table__
BACKUP_RECORDS = BackupRecordRecord.__table__
ACTIVITY_DRAFTS = ActivityDraftRecord.__table__
INTEGRATION_REQUESTS = IntegrationRequestRecord.__table__
REDACTION_EVENTS = RedactionEventRecord.__table__
OUTBOX_EVENTS = OutboxEventRecord.__table__


class PostgresRunStore:
    _engine: Engine | None = None

    def __init__(
        self,
        database_url: str,
        *,
        command_timeout_s: int = 30,
        command_output_limit_bytes: int = 200_000,
    ) -> None:
        self.database_url = database_url
        self.command_timeout_s = command_timeout_s
        self.command_output_limit_bytes = command_output_limit_bytes
        self.ensure_schema()
        self._engine = create_database_engine(database_url)

    def create_run(self, *, ticket_id: int, ticket_snapshot: JsonObject, customer_system_snapshot: JsonObject) -> Run:
        with self._begin() as conn:
            run_row = self._one(
                conn,
                insert(RUNS)
                .values(
                    ticket_id=ticket_id,
                    status=RunStatus.AWAITING_CONNECTION_APPROVAL.value,
                    ticket_snapshot=ticket_snapshot,
                    customer_system_snapshot=customer_system_snapshot,
                )
                .returning(RUNS),
            )
            run = row_to_run(run_row)
            self._insert_event(
                conn,
                run.id,
                actor="technician",
                event_type="run.created",
                summary=f"Troubleshooting run created for ticket #{ticket_id}.",
                payload={"ticket_id": ticket_id},
            )
            self._insert_event(
                conn,
                run.id,
                actor="system",
                event_type="connection.approval_requested",
                summary=_connection_summary(customer_system_snapshot),
                payload={"target": _connection_target(customer_system_snapshot)},
            )
            return run

    def get_run(self, run_id: int) -> Run:
        with self._connect() as conn:
            row = self._run_row(conn, run_id)
        if not row:
            raise RunNotFoundError(f"Run {run_id} was not found")
        return row_to_run(row)

    def approve_connection(self, run_id: int, *, approved_by: str) -> Run:
        with self._begin() as conn:
            row = self._run_row(conn, run_id, lock=True)
            if not row:
                raise RunNotFoundError(f"Run {run_id} was not found")
            run = row_to_run(row)
            if run.status != RunStatus.AWAITING_CONNECTION_APPROVAL:
                raise RunTransitionError("Connection approval can only be recorded while approval is pending")

            updated_row = self._one(
                conn,
                update(RUNS)
                .where(RUNS.c.id == run_id)
                .values(status=RunStatus.INVESTIGATING.value)
                .returning(RUNS),
            )
            self._insert_event(
                conn,
                run_id,
                actor="technician",
                event_type="connection.approved",
                summary=f"SSH connection approved by {approved_by}.",
                approval_status="approved",
                payload={"approved_by": approved_by},
            )
            self._insert_event(
                conn,
                run_id,
                actor="agent",
                event_type="agent.plan_requested",
                summary="Initial diagnostic planning requested after connection approval.",
                payload={},
            )
            self._enqueue_outbox(conn, run_id, "agent.plan_requested", {"reason": "connection_approved"})
            return row_to_run(updated_row)

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
        with self._begin() as conn:
            self._cancel_pending_agent_plan(conn, run_id)
            return self._create_step(
                conn,
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
        with self._begin() as conn:
            return self._create_step(
                conn,
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
        with self._connect() as conn:
            row = self._step_row(conn, run_id, step_id)
        if not row:
            raise RunTransitionError(f"Step {step_id} does not belong to run {run_id}")
        return row_to_step(row)

    def approve_step(self, run_id: int, step_id: int, *, approved_by: str) -> Run:
        with self._begin() as conn:
            run, step = self._locked_run_and_step(conn, run_id, step_id)
            self._assert_step_approvable(run, step)
            self._assert_fix_policy(conn, run, step)
            self._assert_backup_requirement_satisfied(conn, run, step)
            updated_step_row = self._one(
                conn,
                update(PROPOSED_STEPS)
                .where(PROPOSED_STEPS.c.id == step_id)
                .values(
                    status=StepStatus.APPROVED.value,
                    approved_command=PROPOSED_STEPS.c.command,
                    approved_by=approved_by,
                    approved_at=_utc_now(),
                    updated_at=_utc_now(),
                )
                .returning(PROPOSED_STEPS),
            )
            updated_step = row_to_step(updated_step_row)
            updated_row = self._one(
                conn,
                update(RUNS)
                .where(RUNS.c.id == run_id)
                .values(status=_status_for_approved_step(updated_step).value, pending_step=None)
                .returning(RUNS),
            )
            self._insert_event(
                conn,
                run_id,
                actor="technician",
                event_type="step.approved",
                summary=f"Command approved by {approved_by}.",
                command=updated_step.approved_command,
                risk_class=updated_step.risk_class,
                approval_status="approved",
                payload={"step_id": step_id, "approved_by": approved_by},
            )
            self._queue_command_execution(conn, run_id, step_id)
            return row_to_run(updated_row)

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
        with self._begin() as conn:
            run, step = self._locked_run_and_step(conn, run_id, step_id)
            if run.status != RunStatus.AWAITING_STEP_APPROVAL or step.status != StepStatus.PROPOSED:
                raise RunTransitionError("Only pending proposed steps can be edited and approved")
            safety = classify_command(command)
            now = _utc_now()
            updated_step_row = self._one(
                conn,
                update(PROPOSED_STEPS)
                .where(PROPOSED_STEPS.c.id == step_id)
                .values(
                    command=command,
                    purpose=purpose or step.purpose,
                    expected_signal=expected_signal if expected_signal is not None else step.expected_signal,
                    risk_class=safety.risk_class,
                    safety_verdict=safety.verdict,
                    safety_summary=safety.summary,
                    safety_notes=safety.notes,
                    timeout_s=timeout_s or step.timeout_s,
                    updated_at=now,
                )
                .returning(PROPOSED_STEPS),
            )
            updated_step = row_to_step(updated_step_row)
            self._insert_event(
                conn,
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
            self._assert_fix_policy(conn, run, updated_step)
            self._assert_backup_requirement_satisfied(conn, run, updated_step)
            self._one(
                conn,
                update(PROPOSED_STEPS)
                .where(PROPOSED_STEPS.c.id == step_id)
                .values(
                    status=StepStatus.APPROVED.value,
                    approved_command=command,
                    approved_by=approved_by,
                    approved_at=now,
                    updated_at=now,
                )
                .returning(PROPOSED_STEPS),
            )
            updated_row = self._one(
                conn,
                update(RUNS)
                .where(RUNS.c.id == run_id)
                .values(status=_status_for_approved_step(updated_step).value, pending_step=None)
                .returning(RUNS),
            )
            self._insert_event(
                conn,
                run_id,
                actor="technician",
                event_type="step.edited_and_approved",
                summary=f"Command edited and approved by {approved_by}.",
                command=command,
                risk_class=updated_step.risk_class,
                approval_status="approved",
                payload={"step_id": step_id, "approved_by": approved_by},
            )
            self._queue_command_execution(conn, run_id, step_id)
            return row_to_run(updated_row)

    def reject_step(self, run_id: int, step_id: int, *, rejected_by: str, reason: str) -> Run:
        with self._begin() as conn:
            run, step = self._locked_run_and_step(conn, run_id, step_id)
            if run.status != RunStatus.AWAITING_STEP_APPROVAL or step.status != StepStatus.PROPOSED:
                raise RunTransitionError("Only pending proposed steps can be rejected")
            conn.execute(
                update(PROPOSED_STEPS)
                .where(PROPOSED_STEPS.c.id == step_id)
                .values(status=StepStatus.REJECTED.value, rejection_reason=reason, updated_at=_utc_now())
            )
            updated_row = self._one(
                conn,
                update(RUNS)
                .where(RUNS.c.id == run_id)
                .values(status=_idle_status_for_run(run).value, pending_step=None)
                .returning(RUNS),
            )
            self._insert_event(
                conn,
                run_id,
                actor="technician",
                event_type="step.rejected",
                summary=f"Command rejected by {rejected_by}: {reason}",
                command=step.command,
                risk_class=step.risk_class,
                approval_status="rejected",
                payload={"step_id": step_id, "rejected_by": rejected_by, "reason": reason},
            )
            return row_to_run(updated_row)

    def retry_run(self, run_id: int, *, requested_by: str, reason: str = "Retry requested by technician.") -> Run:
        with self._begin() as conn:
            row = self._run_row(conn, run_id, lock=True)
            if not row:
                raise RunNotFoundError(f"Run {run_id} was not found")
            run = row_to_run(row)
            if run.status in {RunStatus.ABORTED, RunStatus.SUBMITTED}:
                raise RunTransitionError("Terminal runs cannot be retried")
            failed_planner_events = self._all(
                conn,
                select(OUTBOX_EVENTS).where(
                    and_(
                        OUTBOX_EVENTS.c.run_id == run_id,
                        OUTBOX_EVENTS.c.event_type == "agent.plan_requested",
                        OUTBOX_EVENTS.c.status.in_(
                            (OutboxStatus.FAILED.value, OutboxStatus.DEAD_LETTER.value)
                        ),
                    )
                ),
            )
            for event in failed_planner_events:
                conn.execute(
                    update(OUTBOX_EVENTS)
                    .where(OUTBOX_EVENTS.c.id == event["id"])
                    .values(
                        status=OutboxStatus.COMPLETED.value,
                        completed_at=_utc_now(),
                        payload=(event["payload"] or {}) | {"superseded_by_retry": True},
                    )
                )
            updated_row = self._one(
                conn,
                update(RUNS)
                .where(RUNS.c.id == run_id)
                .values(status=RunStatus.INVESTIGATING.value, pending_step=None)
                .returning(RUNS),
            )
            self._insert_event(
                conn,
                run_id,
                actor="technician",
                event_type="run.retry_requested",
                summary=f"Retry requested by {requested_by}: {reason}",
                payload={"requested_by": requested_by, "reason": reason},
            )
            self._enqueue_outbox(conn, run_id, "agent.plan_requested", {"reason": "retry_requested", "requested_by": requested_by})
            return row_to_run(updated_row)

    def abort_run(self, run_id: int, *, aborted_by: str, reason: str = "Aborted by technician.") -> Run:
        with self._begin() as conn:
            row = self._run_row(conn, run_id, lock=True)
            if not row:
                raise RunNotFoundError(f"Run {run_id} was not found")
            run = row_to_run(row)
            if run.status in {RunStatus.ABORTED, RunStatus.SUBMITTED}:
                return run
            updated_row = self._one(
                conn,
                update(RUNS)
                .where(RUNS.c.id == run_id)
                .values(status=RunStatus.ABORTED.value, pending_step=None, ended_at=_utc_now())
                .returning(RUNS),
            )
            self._insert_event(
                conn,
                run_id,
                actor="technician",
                event_type="run.aborted",
                summary=f"Run aborted by {aborted_by}: {reason}",
                approval_status="aborted",
                payload={"aborted_by": aborted_by, "reason": reason},
            )
            return row_to_run(updated_row)

    def list_events(self, run_id: int, *, after_id: int = 0) -> list[RunEvent]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(
                conn,
                select(RUN_EVENTS)
                .where(and_(RUN_EVENTS.c.run_id == run_id, RUN_EVENTS.c.id > after_id))
                .order_by(RUN_EVENTS.c.id.asc()),
            )
        return [row_to_event(row) for row in rows]

    def list_outbox_events(
        self,
        run_id: int,
        *,
        statuses: set[OutboxStatus] | None = None,
    ) -> list[OutboxEvent]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(
                conn,
                select(OUTBOX_EVENTS).where(OUTBOX_EVENTS.c.run_id == run_id).order_by(OUTBOX_EVENTS.c.id.asc()),
            )
        events = [row_to_outbox_event(row) for row in rows]
        if statuses is None:
            return events
        return [event for event in events if event.status in statuses]

    def list_integration_requests(self, run_id: int) -> list[IntegrationRequest]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(
                conn,
                select(INTEGRATION_REQUESTS).where(INTEGRATION_REQUESTS.c.run_id == run_id).order_by(INTEGRATION_REQUESTS.c.id),
            )
        return [row_to_integration_request(row) for row in rows]

    def get_integration_request(self, run_id: int, integration_request_id: int) -> IntegrationRequest:
        with self._connect() as conn:
            row = self._integration_request_row(conn, run_id, integration_request_id)
        if not row:
            raise RunTransitionError(f"Integration request {integration_request_id} does not belong to run {run_id}")
        return row_to_integration_request(row)

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
        with self._begin() as conn:
            self._insert_event(
                conn,
                run_id,
                actor=actor,
                event_type=event_type,
                summary=summary,
                command=command,
                error=error,
                payload=payload,
            )

    def claim_next_outbox_event(self) -> OutboxEvent | None:
        with self._begin() as conn:
            self._recover_stale_outbox_events(conn, stale_after_s=120)
            row = conn.execute(claimable_outbox_events_query()).mappings().fetchone()
            if not row:
                return None
            updated = self._one(
                conn,
                update(OUTBOX_EVENTS)
                .where(OUTBOX_EVENTS.c.id == row["id"])
                .values(status=OutboxStatus.PROCESSING.value, attempts=OUTBOX_EVENTS.c.attempts + 1, claimed_at=_utc_now(), error=None)
                .returning(OUTBOX_EVENTS),
            )
        return row_to_outbox_event(updated)

    def recover_stale_outbox_events(self, *, stale_after_s: int = 120) -> int:
        with self._begin() as conn:
            return self._recover_stale_outbox_events(conn, stale_after_s=stale_after_s)

    def complete_outbox_event(self, outbox_event_id: int) -> None:
        with self._begin() as conn:
            conn.execute(
                update(OUTBOX_EVENTS)
                .where(OUTBOX_EVENTS.c.id == outbox_event_id)
                .values(status=OutboxStatus.COMPLETED.value, completed_at=_utc_now())
            )

    def fail_outbox_event(self, outbox_event_id: int, *, error: str) -> None:
        with self._begin() as conn:
            row = self._one_or_none(conn, select(OUTBOX_EVENTS).where(OUTBOX_EVENTS.c.id == outbox_event_id))
            status = OutboxStatus.DEAD_LETTER.value if row and row["attempts"] >= 3 else OutboxStatus.FAILED.value
            values: dict[str, Any] = {"status": status, "error": error, "claimed_at": None}
            if status != OutboxStatus.DEAD_LETTER.value:
                attempts = row["attempts"] if row else 1
                values["available_at"] = _utc_now() + timedelta(seconds=_outbox_backoff_seconds(attempts))
            conn.execute(update(OUTBOX_EVENTS).where(OUTBOX_EVENTS.c.id == outbox_event_id).values(**values))
            if row and row["event_type"] == "integration.activity_submission_requested":
                integration_request_id = (row["payload"] or {}).get("integration_request_id")
                if isinstance(integration_request_id, int) and row["run_id"] is not None:
                    self._fail_integration_request(
                        conn,
                        row["run_id"],
                        integration_request_id,
                        error=error,
                        terminal=status == OutboxStatus.DEAD_LETTER.value,
                    )

    def start_command_execution(self, run_id: int, step_id: int) -> CommandExecution:
        with self._begin() as conn:
            run, step = self._locked_run_and_step(conn, run_id, step_id)
            if run.status == RunStatus.ABORTED:
                raise RunTransitionError("Aborted runs cannot execute commands")
            if step.status != StepStatus.APPROVED or step.safety_verdict == "blocked" or not step.approved_command:
                raise RunTransitionError("Only approved non-blocked steps can execute")
            if run.status != _status_for_approved_step(step):
                raise RunTransitionError("Stale approved step cannot execute after the run moved to another state")
            existing_execution = self._one_or_none(
                conn,
                select(COMMAND_EXECUTIONS.c.id)
                .where(and_(COMMAND_EXECUTIONS.c.run_id == run_id, COMMAND_EXECUTIONS.c.proposed_step_id == step_id))
                .limit(1)
                .with_for_update(),
            )
            if existing_execution:
                raise RunTransitionError("Approved step already has a command execution")
            target = _connection_target(run.customer_system_snapshot)
            row = self._one(
                conn,
                insert(COMMAND_EXECUTIONS)
                .values(
                    run_id=run_id,
                    proposed_step_id=step_id,
                    approved_command=step.approved_command,
                    status=CommandExecutionStatus.RUNNING.value,
                    target_host=target.get("ip") or "",
                    target_port=target.get("port") or 22,
                    target_username=target.get("username") or "",
                    timeout_s=step.timeout_s,
                    output_limit_bytes=self.command_output_limit_bytes,
                    started_at=_utc_now(),
                )
                .returning(COMMAND_EXECUTIONS),
            )
            execution = row_to_command_execution(row)
            self._insert_event(
                conn,
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
        if stream not in {"stdout", "stderr"}:
            raise RunTransitionError("Command output stream must be stdout or stderr")
        content, store_redacted = redact_output(content)
        redacted = redacted or store_redacted
        with self._begin() as conn:
            execution_row = self._one_or_none(
                conn,
                select(COMMAND_EXECUTIONS)
                .where(and_(COMMAND_EXECUTIONS.c.id == command_execution_id, COMMAND_EXECUTIONS.c.run_id == run_id))
                .with_for_update(),
            )
            if not execution_row:
                raise RunTransitionError(f"Command execution {command_execution_id} does not belong to run {run_id}")
            execution = row_to_command_execution(execution_row)
            stored_bytes = len((execution.sanitized_stdout + execution.sanitized_stderr).encode("utf-8"))
            if stored_bytes >= execution.output_limit_bytes:
                self._mark_execution_truncated(conn, run_id, execution)
                return None
            content_to_store, truncated = _truncate_to_bytes(content, execution.output_limit_bytes - stored_bytes)
            if not content_to_store:
                self._mark_execution_truncated(conn, run_id, execution)
                return None
            sequence_row = self._one(
                conn,
                select((func.coalesce(func.max(COMMAND_OUTPUT_CHUNKS.c.sequence), 0) + 1).label("next_sequence")).where(
                    COMMAND_OUTPUT_CHUNKS.c.command_execution_id == command_execution_id
                ),
            )
            sequence = sequence_row["next_sequence"]
            row = self._one(
                conn,
                insert(COMMAND_OUTPUT_CHUNKS)
                .values(
                    command_execution_id=command_execution_id,
                    run_id=run_id,
                    sequence=sequence,
                    stream=stream,
                    content=content_to_store,
                    redacted=redacted,
                )
                .returning(COMMAND_OUTPUT_CHUNKS),
            )
            column = COMMAND_EXECUTIONS.c.sanitized_stdout if stream == "stdout" else COMMAND_EXECUTIONS.c.sanitized_stderr
            conn.execute(
                update(COMMAND_EXECUTIONS)
                .where(COMMAND_EXECUTIONS.c.id == command_execution_id)
                .values({column.key: column + content_to_store})
            )
            self._insert_event(
                conn,
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
                self._insert_redaction_event(conn, run_id, command_execution_id=command_execution_id, surface=stream, field_name=stream)
            if truncated:
                self._mark_execution_truncated(conn, run_id, execution)
            return row_to_command_output_chunk(row)

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
        with self._begin() as conn:
            execution_row = self._one_or_none(
                conn,
                select(COMMAND_EXECUTIONS)
                .where(and_(COMMAND_EXECUTIONS.c.id == command_execution_id, COMMAND_EXECUTIONS.c.run_id == run_id))
                .with_for_update(),
            )
            if not execution_row:
                raise RunTransitionError(f"Command execution {command_execution_id} does not belong to run {run_id}")
            execution = row_to_command_execution(execution_row)
            status = _final_execution_status(exit_code=exit_code, timed_out=timed_out, error=error)
            updated_row = self._one(
                conn,
                update(COMMAND_EXECUTIONS)
                .where(COMMAND_EXECUTIONS.c.id == command_execution_id)
                .values(status=status.value, completed_at=_utc_now(), exit_code=exit_code, duration_ms=duration_ms, error=error)
                .returning(COMMAND_EXECUTIONS),
            )
            completed = row_to_command_execution(updated_row)
            conn.execute(
                update(PROPOSED_STEPS)
                .where(PROPOSED_STEPS.c.id == execution.proposed_step_id)
                .values(
                    status=StepStatus.EXECUTED.value if status == CommandExecutionStatus.COMPLETED else StepStatus.FAILED.value,
                    updated_at=_utc_now(),
                )
            )
            run = row_to_run(self._existing_run_row(conn, run_id))
            step_row = self._step_row(conn, run_id, execution.proposed_step_id)
            if not step_row:
                raise RunTransitionError(f"Step {execution.proposed_step_id} does not belong to run {run_id}")
            step_for_status = row_to_step(step_row)
            if run.status != RunStatus.ABORTED:
                self._apply_completed_command_transition(conn, run_id, command_execution_id, completed, step_for_status, status)
            event_type = {
                CommandExecutionStatus.COMPLETED: "command.completed",
                CommandExecutionStatus.TIMED_OUT: "command.timed_out",
                CommandExecutionStatus.FAILED: "command.failed",
            }.get(status, "command.failed")
            self._insert_event(
                conn,
                run_id,
                actor="ssh_runner",
                event_type=event_type,
                summary="Command completed." if status == CommandExecutionStatus.COMPLETED else error or "Command failed.",
                command=completed.approved_command,
                sanitized_stdout=completed.sanitized_stdout,
                sanitized_stderr=completed.sanitized_stderr,
                exit_code=exit_code,
                duration_ms=duration_ms,
                risk_class=step_for_status.risk_class,
                error=error,
                payload={
                    "step_id": step_for_status.id,
                    "command_execution_id": command_execution_id,
                    "output_truncated": completed.output_truncated,
                },
            )
            if _remote_command_started(completed):
                self._record_ledgers_for_completed_command(conn, run_id, completed, step_for_status)
                self._insert_restore_completed_if_applicable(conn, run_id, completed, step_for_status)
            return completed

    def list_command_executions(self, run_id: int) -> list[CommandExecution]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(conn, select(COMMAND_EXECUTIONS).where(COMMAND_EXECUTIONS.c.run_id == run_id).order_by(COMMAND_EXECUTIONS.c.id))
        return [row_to_command_execution(row) for row in rows]

    def list_command_output_chunks(self, run_id: int) -> list[CommandOutputChunk]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(
                conn,
                select(COMMAND_OUTPUT_CHUNKS)
                .where(COMMAND_OUTPUT_CHUNKS.c.run_id == run_id)
                .order_by(COMMAND_OUTPUT_CHUNKS.c.command_execution_id, COMMAND_OUTPUT_CHUNKS.c.sequence),
            )
        return [row_to_command_output_chunk(row) for row in rows]

    def list_redaction_events(self, run_id: int) -> list[RedactionEvent]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(conn, select(REDACTION_EVENTS).where(REDACTION_EVENTS.c.run_id == run_id).order_by(REDACTION_EVENTS.c.id))
        return [row_to_redaction_event(row) for row in rows]

    def list_inspected_sources(self, run_id: int) -> list[InspectedSource]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(conn, select(INSPECTED_SOURCES).where(INSPECTED_SOURCES.c.run_id == run_id).order_by(INSPECTED_SOURCES.c.id))
        return [row_to_inspected_source(row) for row in rows]

    def list_validation_results(self, run_id: int) -> list[ValidationResult]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(conn, select(VALIDATION_RESULTS).where(VALIDATION_RESULTS.c.run_id == run_id).order_by(VALIDATION_RESULTS.c.id))
        return [row_to_validation_result(row) for row in rows]

    def list_validation_expectations(self, run_id: int) -> list[ValidationExpectation]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(
                conn,
                select(VALIDATION_EXPECTATIONS).where(VALIDATION_EXPECTATIONS.c.run_id == run_id).order_by(VALIDATION_EXPECTATIONS.c.id),
            )
        return [row_to_validation_expectation(row) for row in rows]

    def list_backup_records(self, run_id: int) -> list[BackupRecord]:
        self.get_run(run_id)
        with self._connect() as conn:
            rows = self._all(conn, select(BACKUP_RECORDS).where(BACKUP_RECORDS.c.run_id == run_id).order_by(BACKUP_RECORDS.c.id))
        return [row_to_backup_record(row) for row in rows]

    def record_backup_not_applicable(
        self,
        run_id: int,
        *,
        source_path: str | None,
        reason: str,
        recorded_by: str,
    ) -> BackupRecord:
        with self._begin() as conn:
            run = row_to_run(self._existing_run_row(conn, run_id))
            record_row = self._one(
                conn,
                insert(BACKUP_RECORDS)
                .values(
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
                )
                .returning(BACKUP_RECORDS),
            )
            record = row_to_backup_record(record_row)
            self._insert_event(
                conn,
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
        with self._begin() as conn:
            record_row = self._one_or_none(
                conn,
                select(BACKUP_RECORDS).where(and_(BACKUP_RECORDS.c.id == backup_record_id, BACKUP_RECORDS.c.run_id == run_id)),
            )
            if not record_row:
                raise RunTransitionError(f"Backup record {backup_record_id} does not belong to run {run_id}")
            record = row_to_backup_record(record_row)
            if not record.restore_command:
                raise RunTransitionError("Backup record has no restore command")
            step = self._create_step(
                conn,
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
            self._insert_event(
                conn,
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
        with self._begin() as conn:
            run = row_to_run(self._existing_run_row(conn, run_id))
            self._assert_activity_ready(run)
            draft, redacted_fields = _redact_activity_draft(draft)
            self._assert_activity_draft_complete(run, draft)
            conn.execute(update(RUNS).where(RUNS.c.id == run_id).values(activity_draft=draft.model_dump(mode="json")))
            activity_row = self._one(
                conn,
                insert(ACTIVITY_DRAFTS)
                .values(run_id=run_id, ticket_id=draft.ticket_id, draft=draft.model_dump(mode="json"))
                .returning(ACTIVITY_DRAFTS),
            )
            for field_name in redacted_fields:
                self._insert_redaction_event(conn, run_id, activity_draft_id=activity_row["id"], surface="activity", field_name=field_name)
            event_type = "activity.draft_edited" if edited_by else "agent.activity_draft_generated"
            actor = "technician" if edited_by else "activity_writer"
            summary = (
                f"Phoenix activity draft edited and saved by {edited_by}."
                if edited_by
                else "Phoenix activity draft generated from run audit, commands, evidence, and backups."
            )
            self._insert_event(
                conn,
                run_id,
                actor=actor,
                event_type=event_type,
                summary=summary,
                payload={"ticket_id": draft.ticket_id, "activity_draft_id": activity_row["id"], "edited_by": edited_by},
            )
            return draft

    def queue_activity_submission(self, run_id: int) -> IntegrationRequest:
        with self._begin() as conn:
            row = self._run_row(conn, run_id, lock=True)
            if not row:
                raise RunNotFoundError(f"Run {run_id} was not found")
            run = row_to_run(row)
            self._assert_activity_ready(run)
            activity_row = self._one_or_none(
                conn,
                select(ACTIVITY_DRAFTS).where(ACTIVITY_DRAFTS.c.run_id == run_id).order_by(ACTIVITY_DRAFTS.c.id.desc()).limit(1),
            )
            if activity_row:
                draft = ActivityDraft.model_validate(activity_row["draft"])
                activity_draft_id = activity_row["id"]
            elif run.activity_draft:
                draft = ActivityDraft.model_validate(run.activity_draft)
                activity_draft_id = None
            else:
                raise RunTransitionError("Activity submission requires a saved draft")
            self._assert_activity_draft_complete(run, draft)
            existing = self._one_or_none(
                conn,
                select(INTEGRATION_REQUESTS)
                .where(and_(INTEGRATION_REQUESTS.c.run_id == run_id, INTEGRATION_REQUESTS.c.status.not_in(("completed", "dead_letter"))))
                .order_by(INTEGRATION_REQUESTS.c.id.desc())
                .limit(1),
            )
            if existing:
                return row_to_integration_request(existing)
            request_row = self._one(
                conn,
                insert(INTEGRATION_REQUESTS)
                .values(
                    run_id=run_id,
                    ticket_id=run.ticket_id,
                    activity_draft_id=activity_draft_id,
                    request_type="phoenix_activity_submission",
                    status=IntegrationRequestStatus.PENDING.value,
                    activity_payload=draft.model_dump(mode="json", exclude_none=True),
                )
                .returning(INTEGRATION_REQUESTS),
            )
            request = row_to_integration_request(request_row)
            self._insert_event(
                conn,
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
            self._cancel_pending_outbox_event(conn, run_id, "agent.activity_draft_requested")
            self._enqueue_outbox(conn, run_id, "integration.activity_submission_requested", {"integration_request_id": request.id})
            return request

    def mark_integration_request_processing(self, run_id: int, integration_request_id: int) -> IntegrationRequest:
        with self._begin() as conn:
            row = self._one_or_none(
                conn,
                update(INTEGRATION_REQUESTS)
                .where(
                    and_(
                        INTEGRATION_REQUESTS.c.id == integration_request_id,
                        INTEGRATION_REQUESTS.c.run_id == run_id,
                        INTEGRATION_REQUESTS.c.status != IntegrationRequestStatus.COMPLETED.value,
                    )
                )
                .values(status=IntegrationRequestStatus.PROCESSING.value, attempts=INTEGRATION_REQUESTS.c.attempts + 1, updated_at=_utc_now(), error=None)
                .returning(INTEGRATION_REQUESTS),
            )
            if not row:
                return self.get_integration_request(run_id, integration_request_id)
            return row_to_integration_request(row)

    def mark_integration_activity_created(
        self,
        run_id: int,
        integration_request_id: int,
        *,
        phoenix_activity_id: int,
    ) -> IntegrationRequest:
        with self._begin() as conn:
            row = self._one_or_none(
                conn,
                update(INTEGRATION_REQUESTS)
                .where(and_(INTEGRATION_REQUESTS.c.id == integration_request_id, INTEGRATION_REQUESTS.c.run_id == run_id))
                .values(status=IntegrationRequestStatus.ACTIVITY_CREATED.value, phoenix_activity_id=phoenix_activity_id, updated_at=_utc_now(), error=None)
                .returning(INTEGRATION_REQUESTS),
            )
            if not row:
                raise RunTransitionError(f"Integration request {integration_request_id} does not belong to run {run_id}")
            self._insert_event(
                conn,
                run_id,
                actor="phoenix",
                event_type="activity.created",
                summary="Phoenix activity was created; ticket status update remains in progress.",
                payload={"integration_request_id": integration_request_id, "submitted_activity_id": phoenix_activity_id},
            )
            return row_to_integration_request(row)

    def mark_integration_request_completed(
        self,
        run_id: int,
        integration_request_id: int,
        *,
        ticket_status: str,
    ) -> Run:
        with self._begin() as conn:
            request_row = self._one_or_none(
                conn,
                update(INTEGRATION_REQUESTS)
                .where(and_(INTEGRATION_REQUESTS.c.id == integration_request_id, INTEGRATION_REQUESTS.c.run_id == run_id))
                .values(status=IntegrationRequestStatus.COMPLETED.value, ticket_status=ticket_status, updated_at=_utc_now(), completed_at=_utc_now(), error=None)
                .returning(INTEGRATION_REQUESTS),
            )
            if not request_row:
                raise RunTransitionError(f"Integration request {integration_request_id} does not belong to run {run_id}")
            request = row_to_integration_request(request_row)
            conn.execute(
                update(ACTIVITY_DRAFTS)
                .where(ACTIVITY_DRAFTS.c.id == request.activity_draft_id)
                .values(submitted_activity_id=request.phoenix_activity_id, submitted_at=_utc_now(), updated_at=_utc_now())
            )
            conn.execute(update(RUNS).where(RUNS.c.id == run_id).values(status=RunStatus.SUBMITTED.value, ended_at=_utc_now()))
            self._insert_event(
                conn,
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
            self._insert_event(
                conn,
                run_id,
                actor="phoenix",
                event_type="ticket.status_updated",
                summary="Phoenix ticket status updated to DONE after activity submission.",
                payload={"integration_request_id": integration_request_id, "ticket_id": request.ticket_id, "status": ticket_status},
            )
            return row_to_run(self._existing_run_row(conn, run_id))

    def fail_integration_request(
        self,
        run_id: int,
        integration_request_id: int,
        *,
        error: str,
        terminal: bool = False,
    ) -> IntegrationRequest:
        with self._begin() as conn:
            return self._fail_integration_request(conn, run_id, integration_request_id, error=error, terminal=terminal)

    def ensure_schema(self) -> None:
        run_database_migrations(self.database_url)

    def _begin(self):
        return self._require_engine().begin()

    def _connect(self):
        return self._require_engine().connect()

    def _require_engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_database_engine(self.database_url)
        return self._engine

    def _create_step(
        self,
        conn: Connection,
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
        row = self._run_row(conn, run_id, lock=True)
        if not row:
            raise RunNotFoundError(f"Run {run_id} was not found")
        run = row_to_run(row)
        if run.status in {RunStatus.ABORTED, RunStatus.SUBMITTED, RunStatus.FAILED}:
            raise RunTransitionError("Terminal runs cannot accept new steps")
        if run.status == RunStatus.AWAITING_STEP_APPROVAL and run.pending_step:
            raise RunTransitionError("A run can only have one pending step")

        safety = classify_command(command)
        step_row = self._one(
            conn,
            insert(PROPOSED_STEPS)
            .values(
                run_id=run_id,
                source=source,
                phase=phase,
                command=command,
                purpose=purpose,
                expected_signal=expected_signal,
                risk_class=safety.risk_class,
                safety_verdict=safety.verdict,
                safety_summary=safety.summary,
                safety_notes=safety.notes,
                status=StepStatus.BLOCKED.value if safety.verdict == "blocked" else StepStatus.PROPOSED.value,
                timeout_s=timeout_s or self.command_timeout_s,
                updated_at=_utc_now(),
            )
            .returning(PROPOSED_STEPS),
        )
        step = row_to_step(step_row)
        if source == "manual":
            self._insert_event(
                conn,
                run_id,
                actor=actor,
                event_type="manual_step.entered",
                summary=f"Manual command entered by {actor_name}.",
                command=command,
                payload={"step_id": step.id, "entered_by": actor_name, "purpose": purpose},
            )
        self._insert_event(
            conn,
            run_id,
            actor=actor,
            event_type="step.proposed",
            summary=purpose,
            command=command,
            risk_class=step.risk_class,
            payload={"step_id": step.id, "source": source, "phase": phase, "expected_signal": expected_signal},
        )
        self._insert_event(
            conn,
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
            self._record_backup_plan(conn, run_id, run.ticket_id, step.id, command, requirement)
        if safety.verdict == "blocked":
            conn.execute(update(RUNS).where(RUNS.c.id == run_id).values(status=_idle_status_for_run(run).value, pending_step=None))
        else:
            conn.execute(
                update(RUNS)
                .where(RUNS.c.id == run_id)
                .values(status=RunStatus.AWAITING_STEP_APPROVAL.value, pending_step=step.model_dump(mode="json"))
            )
        return step

    def _record_backup_plan(
        self,
        conn: Connection,
        run_id: int,
        ticket_id: int,
        step_id: int,
        command: str,
        requirement: Any,
    ) -> BackupRecord:
        existing = self._one_or_none(
            conn,
            select(BACKUP_RECORDS)
            .where(
                and_(
                    BACKUP_RECORDS.c.run_id == run_id,
                    BACKUP_RECORDS.c.backup_required.is_(True),
                    BACKUP_RECORDS.c.backup_created.is_(False),
                    BACKUP_RECORDS.c.backup_type == requirement.backup_type,
                    BACKUP_RECORDS.c.source_path.is_not_distinct_from(requirement.source_path),
                )
            )
            .order_by(BACKUP_RECORDS.c.id.desc())
            .limit(1),
        )
        if existing:
            return row_to_backup_record(existing)
        plan = backup_plan_for_requirement(run_id=run_id, ticket_id=ticket_id, requirement=requirement)
        record_row = self._one(
            conn,
            insert(BACKUP_RECORDS)
            .values(
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
            )
            .returning(BACKUP_RECORDS),
        )
        record = row_to_backup_record(record_row)
        self._insert_event(
            conn,
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

    def _locked_run_and_step(self, conn: Connection, run_id: int, step_id: int) -> tuple[Run, ProposedStep]:
        run_row = self._run_row(conn, run_id, lock=True)
        if not run_row:
            raise RunNotFoundError(f"Run {run_id} was not found")
        step_row = self._step_row(conn, run_id, step_id, lock=True)
        if not step_row:
            raise RunTransitionError(f"Step {step_id} does not belong to run {run_id}")
        return row_to_run(run_row), row_to_step(step_row)

    def _assert_step_approvable(self, run: Run, step: ProposedStep) -> None:
        if run.status != RunStatus.AWAITING_STEP_APPROVAL:
            raise RunTransitionError("A step can only be approved while approval is pending")
        if step.status != StepStatus.PROPOSED:
            raise RunTransitionError("Only proposed steps can be approved")
        if step.safety_verdict == "blocked":
            raise RunTransitionError("Blocked steps cannot be approved")

    def _assert_fix_policy(self, conn: Connection, run: Run, step: ProposedStep) -> None:
        if step.phase != "fix":
            return
        if step.risk_class == "READ_ONLY" or is_targeted_backup_command(step.command):
            return
        rows = self._all(
            conn,
            select(INSPECTED_SOURCES)
            .where(and_(INSPECTED_SOURCES.c.run_id == run.id, INSPECTED_SOURCES.c.supports.in_(("root_cause", "fix_choice"))))
            .order_by(INSPECTED_SOURCES.c.id),
        )
        evidence_sources = [row_to_inspected_source(row) for row in rows]
        if not evidence_sources:
            self._insert_event(
                conn,
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
                self._insert_event(
                    conn,
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

        self._insert_event(
            conn,
            run.id,
            actor="safety_layer",
            event_type="fix.evidence_verified",
            summary="Fix proposal references recorded root-cause or fix-choice evidence.",
            command=step.command,
            risk_class=step.risk_class,
            payload={"step_id": step.id, "inspected_source_ids": [source.id for source in evidence_sources]},
        )

    def _assert_backup_requirement_satisfied(self, conn: Connection, run: Run, step: ProposedStep) -> None:
        requirement = backup_requirement_for_command(step.command, step.risk_class)
        if not requirement.required:
            return
        rows = self._all(conn, select(BACKUP_RECORDS).where(BACKUP_RECORDS.c.run_id == run.id).order_by(BACKUP_RECORDS.c.id))
        records = [row_to_backup_record(row) for row in rows]
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
        self._insert_event(
            conn,
            run.id,
            actor="backup_service",
            event_type="backup.approval_requested",
            summary=requirement.reason,
            command=step.command,
            payload={"step_id": step.id, "source_path": requirement.source_path, "backup_type": requirement.backup_type},
        )
        raise RunTransitionError("Medium-risk persistent changes require a matching backup record or backup.not_applicable event before approval")

    def _queue_command_execution(self, conn: Connection, run_id: int, step_id: int) -> None:
        self._insert_event(
            conn,
            run_id,
            actor="system",
            event_type="command.execution_requested",
            summary="Approved command queued for worker execution.",
            payload={"step_id": step_id},
        )
        self._enqueue_outbox(conn, run_id, "command.execution_requested", {"step_id": step_id})

    def _enqueue_outbox(self, conn: Connection, run_id: int | None, event_type: str, payload: JsonObject) -> OutboxEvent:
        row = self._one(
            conn,
            insert(OUTBOX_EVENTS).values(run_id=run_id, event_type=event_type, payload=payload).returning(OUTBOX_EVENTS),
        )
        return row_to_outbox_event(row)

    def _cancel_pending_agent_plan(self, conn: Connection, run_id: int) -> None:
        self._cancel_pending_outbox_event(conn, run_id, "agent.plan_requested")

    def _cancel_pending_outbox_event(self, conn: Connection, run_id: int, event_type: str) -> None:
        conn.execute(
            update(OUTBOX_EVENTS)
            .where(
                and_(
                    OUTBOX_EVENTS.c.run_id == run_id,
                    OUTBOX_EVENTS.c.event_type == event_type,
                    OUTBOX_EVENTS.c.status == OutboxStatus.PENDING.value,
                )
            )
            .values(status=OutboxStatus.COMPLETED.value, completed_at=_utc_now())
        )

    def _recover_stale_outbox_events(self, conn: Connection, *, stale_after_s: int) -> int:
        cutoff = _utc_now() - timedelta(seconds=stale_after_s)
        rows = self._all(
            conn,
            update(OUTBOX_EVENTS)
            .where(and_(OUTBOX_EVENTS.c.status == OutboxStatus.PROCESSING.value, OUTBOX_EVENTS.c.claimed_at.is_not(None), OUTBOX_EVENTS.c.claimed_at < cutoff))
            .values(status=OutboxStatus.PENDING.value, claimed_at=None, available_at=_utc_now(), error=None)
            .returning(OUTBOX_EVENTS.c.id, OUTBOX_EVENTS.c.run_id, OUTBOX_EVENTS.c.event_type),
        )
        for row in rows:
            if row["run_id"] is None:
                continue
            self._insert_event(
                conn,
                row["run_id"],
                actor="worker",
                event_type="outbox.recovered",
                summary="Stale processing outbox event recovered for retry.",
                payload={"outbox_event_id": row["id"], "event_type": row["event_type"]},
            )
        return len(rows)

    def _insert_event(
        self,
        conn: Connection,
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
    ) -> None:
        conn.execute(
            insert(RUN_EVENTS).values(
                run_id=run_id,
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
                payload=payload,
            )
        )

    def _insert_redaction_event(
        self,
        conn: Connection,
        run_id: int,
        *,
        surface: str,
        field_name: str,
        command_execution_id: int | None = None,
        inspected_source_id: int | None = None,
        activity_draft_id: int | None = None,
    ) -> RedactionEvent:
        row = self._one(
            conn,
            insert(REDACTION_EVENTS)
            .values(
                run_id=run_id,
                command_execution_id=command_execution_id,
                inspected_source_id=inspected_source_id,
                activity_draft_id=activity_draft_id,
                surface=surface,
                field_name=field_name,
            )
            .returning(REDACTION_EVENTS),
        )
        return row_to_redaction_event(row)

    def _mark_execution_truncated(self, conn: Connection, run_id: int, execution: CommandExecution) -> None:
        current = self._one_or_none(conn, select(COMMAND_EXECUTIONS.c.output_truncated).where(COMMAND_EXECUTIONS.c.id == execution.id))
        if current and current["output_truncated"]:
            return
        conn.execute(update(COMMAND_EXECUTIONS).where(COMMAND_EXECUTIONS.c.id == execution.id).values(output_truncated=True))
        self._insert_event(
            conn,
            run_id,
            actor="ssh_runner",
            event_type="terminal.output_truncated",
            summary="Command output exceeded the configured storage cap.",
            payload={"command_execution_id": execution.id, "output_limit_bytes": execution.output_limit_bytes},
        )

    def _record_ledgers_for_completed_command(
        self,
        conn: Connection,
        run_id: int,
        execution: CommandExecution,
        step: ProposedStep,
    ) -> None:
        redaction_row = self._one_or_none(
            conn,
            select(func.bool_or(COMMAND_OUTPUT_CHUNKS.c.redacted).label("redacted")).where(COMMAND_OUTPUT_CHUNKS.c.command_execution_id == execution.id),
        )
        redacted = bool(redaction_row and redaction_row["redacted"])
        for source in detect_inspected_sources(
            command=execution.approved_command,
            sanitized_stdout=execution.sanitized_stdout,
            sanitized_stderr=execution.sanitized_stderr,
            purpose=step.purpose,
            phase=step.phase,
            redacted=redacted,
        ):
            source_row = self._one(
                conn,
                insert(INSPECTED_SOURCES)
                .values(
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
                )
                .returning(INSPECTED_SOURCES),
            )
            inspected_source = row_to_inspected_source(source_row)
            self._insert_event(
                conn,
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
                self._insert_event(
                    conn,
                    run_id,
                    actor="evidence_detector",
                    event_type="evidence.source_redacted",
                    summary="Evidence source excerpt contained redacted secret-like material.",
                    command=execution.approved_command,
                    payload={"inspected_source_id": inspected_source.id},
                )
                self._insert_redaction_event(
                    conn,
                    run_id,
                    command_execution_id=execution.id,
                    inspected_source_id=inspected_source.id,
                    surface="evidence",
                    field_name="sanitized_excerpt",
                )

        detected_backup = detect_backup_record(
            run_id=run_id,
            ticket_id=row_to_run(self._existing_run_row(conn, run_id)).ticket_id,
            command_execution_id=execution.id,
            command=execution.approved_command,
            output=f"{execution.sanitized_stdout}\n{execution.sanitized_stderr}",
        )
        if detected_backup:
            run = row_to_run(self._existing_run_row(conn, run_id))
            record_row = self._one(
                conn,
                insert(BACKUP_RECORDS)
                .values(
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
                )
                .returning(BACKUP_RECORDS),
            )
            record = row_to_backup_record(record_row)
            self._insert_event(
                conn,
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

    def _insert_restore_completed_if_applicable(
        self,
        conn: Connection,
        run_id: int,
        execution: CommandExecution,
        step: ProposedStep,
    ) -> None:
        if step.phase != "restore" or execution.status != CommandExecutionStatus.COMPLETED:
            return
        record_row = self._one_or_none(
            conn,
            select(BACKUP_RECORDS)
            .where(and_(BACKUP_RECORDS.c.run_id == run_id, BACKUP_RECORDS.c.restore_command == execution.approved_command))
            .order_by(BACKUP_RECORDS.c.id.desc())
            .limit(1),
        )
        if not record_row:
            return
        record = row_to_backup_record(record_row)
        self._insert_event(
            conn,
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

    def _insert_validation_result(self, conn: Connection, run_id: int, execution: CommandExecution) -> ValidationResult:
        evaluation = _evaluate_validation_result(execution)
        row = self._one(
            conn,
            insert(VALIDATION_RESULTS)
            .values(
                run_id=run_id,
                command_execution_id=execution.id,
                check_type=evaluation["check_type"],
                target=evaluation["target"],
                passed=evaluation["passed"],
                summary=evaluation["summary"],
                evidence=evaluation["evidence"],
            )
            .returning(VALIDATION_RESULTS),
        )
        result = row_to_validation_result(row)
        self._insert_event(
            conn,
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

    def _insert_validation_expectations_for_fix(
        self,
        conn: Connection,
        run_id: int,
        execution: CommandExecution,
        step: ProposedStep,
    ) -> list[ValidationExpectation]:
        run = row_to_run(self._existing_run_row(conn, run_id))
        expectations: list[ValidationExpectation] = []
        for spec in _validation_expectation_specs(run, execution, step):
            row = self._one(
                conn,
                insert(VALIDATION_EXPECTATIONS)
                .values(
                    run_id=run_id,
                    fix_command_execution_id=execution.id,
                    check_type=spec["check_type"],
                    target=spec["target"],
                    expected_result=spec["expected_result"],
                    relation_to_customer_symptom=spec["relation_to_customer_symptom"],
                    required=True,
                    status="pending",
                )
                .returning(VALIDATION_EXPECTATIONS),
            )
            expectations.append(row_to_validation_expectation(row))
        return expectations

    def _apply_validation_result_to_suite(self, conn: Connection, run_id: int, result: ValidationResult) -> str:
        expectations = self._latest_validation_expectations(conn, run_id)
        if not expectations:
            fix_completed = self._has_completed_fix_execution(conn, run_id)
            execution = row_to_command_execution(
                self._one(
                    conn,
                    select(COMMAND_EXECUTIONS).where(and_(COMMAND_EXECUTIONS.c.id == result.command_execution_id, COMMAND_EXECUTIONS.c.run_id == run_id)),
                )
            )
            self._insert_event(
                conn,
                run_id,
                actor="validation",
                event_type="validation.suite_missing",
                summary=(
                    "Validation result recorded, but no required validation suite exists for the latest fix."
                    if fix_completed
                    else "Validation result recorded for a run with no completed fix command."
                ),
                command=execution.approved_command,
                payload={"validation_result_id": result.id},
            )
            if fix_completed:
                return "incomplete"
            return "passed" if result.passed else "failed"

        match = _matching_validation_expectation(expectations, result)
        if match and match.status == "pending":
            row = self._one(
                conn,
                update(VALIDATION_EXPECTATIONS)
                .where(VALIDATION_EXPECTATIONS.c.id == match.id)
                .values(status="passed" if result.passed else "failed", validation_result_id=result.id, updated_at=_utc_now())
                .returning(VALIDATION_EXPECTATIONS),
            )
            updated = row_to_validation_expectation(row)
            self._insert_event(
                conn,
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
            expectations = self._latest_validation_expectations(conn, run_id)

        return _validation_suite_status(expectations)

    def _latest_validation_expectations(self, conn: Connection, run_id: int) -> list[ValidationExpectation]:
        latest_fix_row = self._one_or_none(
            conn,
            select(VALIDATION_EXPECTATIONS.c.fix_command_execution_id)
            .where(and_(VALIDATION_EXPECTATIONS.c.run_id == run_id, VALIDATION_EXPECTATIONS.c.fix_command_execution_id.is_not(None)))
            .order_by(VALIDATION_EXPECTATIONS.c.id.desc())
            .limit(1),
        )
        if latest_fix_row:
            rows = self._all(
                conn,
                select(VALIDATION_EXPECTATIONS)
                .where(
                    and_(
                        VALIDATION_EXPECTATIONS.c.run_id == run_id,
                        VALIDATION_EXPECTATIONS.c.fix_command_execution_id == latest_fix_row["fix_command_execution_id"],
                    )
                )
                .order_by(VALIDATION_EXPECTATIONS.c.id),
            )
        else:
            rows = self._all(
                conn,
                select(VALIDATION_EXPECTATIONS).where(VALIDATION_EXPECTATIONS.c.run_id == run_id).order_by(VALIDATION_EXPECTATIONS.c.id),
            )
        return [row_to_validation_expectation(row) for row in rows]

    def _fail_integration_request(
        self,
        conn: Connection,
        run_id: int,
        integration_request_id: int,
        *,
        error: str,
        terminal: bool = False,
    ) -> IntegrationRequest:
        current = self._integration_request_row(conn, run_id, integration_request_id, lock=True)
        if not current:
            raise RunTransitionError(f"Integration request {integration_request_id} does not belong to run {run_id}")
        current_request = row_to_integration_request(current)
        if current_request.status == IntegrationRequestStatus.ACTIVITY_CREATED and not terminal:
            status = IntegrationRequestStatus.ACTIVITY_CREATED
        else:
            status = IntegrationRequestStatus.DEAD_LETTER if terminal else IntegrationRequestStatus.FAILED
        row = self._one(
            conn,
            update(INTEGRATION_REQUESTS)
            .where(and_(INTEGRATION_REQUESTS.c.id == integration_request_id, INTEGRATION_REQUESTS.c.run_id == run_id))
            .values(status=status.value, error=error, updated_at=_utc_now())
            .returning(INTEGRATION_REQUESTS),
        )
        self._insert_event(
            conn,
            run_id,
            actor="phoenix",
            event_type="integration.failed",
            summary=error,
            payload={
                "integration_request_id": integration_request_id,
                "status": status.value,
                "phoenix_activity_id": current_request.phoenix_activity_id,
            },
            error=error,
        )
        return row_to_integration_request(row)

    def _apply_completed_command_transition(
        self,
        conn: Connection,
        run_id: int,
        command_execution_id: int,
        completed: CommandExecution,
        step_for_status: ProposedStep,
        status: CommandExecutionStatus,
    ) -> None:
        if step_for_status.phase == "validation" and status == CommandExecutionStatus.COMPLETED:
            validation_result = self._insert_validation_result(conn, run_id, completed)
            suite_status = self._apply_validation_result_to_suite(conn, run_id, validation_result)
            conn.execute(
                update(RUNS)
                .where(RUNS.c.id == run_id)
                .values(
                    status=RunStatus.READY_FOR_ACTIVITY.value if suite_status == "passed" else RunStatus.VALIDATING.value,
                    pending_step=None,
                    validation_result=validation_result.summary,
                )
            )
            if suite_status == "passed":
                self._insert_event(
                    conn,
                    run_id,
                    actor="validation",
                    event_type="validation.suite_passed",
                    summary="All required validation checks passed for the latest fix.",
                    payload={"validation_result_id": validation_result.id},
                )
                self._insert_event(
                    conn,
                    run_id,
                    actor="agent",
                    event_type="agent.activity_draft_requested",
                    summary="Activity draft generation requested after all required validation checks passed.",
                    payload={"reason": "validation_suite_passed", "validation_result_id": validation_result.id},
                )
                self._enqueue_outbox(
                    conn,
                    run_id,
                    "agent.activity_draft_requested",
                    {"reason": "validation_suite_passed", "validation_result_id": validation_result.id},
                )
            elif suite_status == "failed" or not validation_result.passed:
                self._insert_event(
                    conn,
                    run_id,
                    actor="agent",
                    event_type="agent.plan_requested",
                    summary="Follow-up planning requested after validation failed.",
                    payload={"reason": "validation_failed", "validation_result_id": validation_result.id},
                )
                self._enqueue_outbox(conn, run_id, "agent.plan_requested", {"reason": "validation_failed"})
            else:
                self._insert_event(
                    conn,
                    run_id,
                    actor="agent",
                    event_type="agent.plan_requested",
                    summary="Additional validation planning requested because the required suite is incomplete.",
                    payload={"reason": "validation_suite_incomplete", "validation_result_id": validation_result.id},
                )
                self._enqueue_outbox(conn, run_id, "agent.plan_requested", {"reason": "validation_suite_incomplete"})
        elif step_for_status.phase == "validation":
            validation_result = self._insert_validation_result(conn, run_id, completed)
            self._apply_validation_result_to_suite(conn, run_id, validation_result)
            conn.execute(
                update(RUNS)
                .where(RUNS.c.id == run_id)
                .values(status=RunStatus.VALIDATING.value, pending_step=None, validation_result=validation_result.summary)
            )
            self._insert_event(
                conn,
                run_id,
                actor="agent",
                event_type="agent.plan_requested",
                summary="Follow-up planning requested after validation failed.",
                payload={"reason": "validation_failed", "validation_result_id": validation_result.id},
            )
            self._enqueue_outbox(conn, run_id, "agent.plan_requested", {"reason": "validation_failed"})
        elif status == CommandExecutionStatus.COMPLETED and step_for_status.phase == "fix":
            expectations = self._insert_validation_expectations_for_fix(conn, run_id, completed, step_for_status)
            conn.execute(update(RUNS).where(RUNS.c.id == run_id).values(status=RunStatus.VALIDATING.value, pending_step=None))
            self._insert_event(
                conn,
                run_id,
                actor="validation",
                event_type="validation.suite_required",
                summary="Required validation suite persisted for the approved fix.",
                command=completed.approved_command,
                payload={
                    "step_id": step_for_status.id,
                    "command_execution_id": command_execution_id,
                    "validation_expectation_ids": [expectation.id for expectation in expectations],
                    "required_checks": [expectation.check_type for expectation in expectations],
                },
            )
            self._insert_event(
                conn,
                run_id,
                actor="validation",
                event_type="validation.required",
                summary="Approved fix completed; validation planning is required before activity drafting.",
                command=completed.approved_command,
                payload={"step_id": step_for_status.id, "command_execution_id": command_execution_id},
            )
            self._insert_event(
                conn,
                run_id,
                actor="agent",
                event_type="agent.plan_requested",
                summary="Validation planning requested after the approved fix completed.",
                payload={"reason": "fix_completed_validation_required"},
            )
            self._enqueue_outbox(conn, run_id, "agent.plan_requested", {"reason": "fix_completed_validation_required"})
        elif status == CommandExecutionStatus.COMPLETED:
            conn.execute(update(RUNS).where(RUNS.c.id == run_id).values(status=RunStatus.INVESTIGATING.value, pending_step=None))
            self._insert_event(
                conn,
                run_id,
                actor="agent",
                event_type="agent.plan_requested",
                summary="Observation interpretation requested after command completion.",
                payload={"reason": "observation_recorded", "command_execution_id": command_execution_id},
            )
            self._enqueue_outbox(conn, run_id, "agent.plan_requested", {"reason": "observation_recorded"})
        elif _runner_failed_before_remote_observation(completed):
            conn.execute(update(RUNS).where(RUNS.c.id == run_id).values(status=RunStatus.FAILED.value, pending_step=None))
        else:
            conn.execute(update(RUNS).where(RUNS.c.id == run_id).values(status=RunStatus.INVESTIGATING.value, pending_step=None))
            self._insert_event(
                conn,
                run_id,
                actor="agent",
                event_type="agent.plan_requested",
                summary="Follow-up planning requested after command failure.",
                payload={"reason": "command_failed", "command_execution_id": command_execution_id},
            )
            self._enqueue_outbox(conn, run_id, "agent.plan_requested", {"reason": "command_failed"})

    def _assert_activity_ready(self, run: Run) -> None:
        with self._connect() as conn:
            expectations = self._latest_validation_expectations(conn, run.id)
            suite_status = _validation_suite_status(expectations)
            validation_only_ready = (
                not expectations
                and not self._has_completed_fix_execution(conn, run.id)
                and self._has_passing_validation_result(conn, run.id)
            )
        if suite_status == "failed":
            raise RunTransitionError("A failed validation check requires a new fix and validation suite before activity drafting")
        if suite_status != "passed" and not validation_only_ready:
            raise RunTransitionError("Activity draft and submission require a completed validation suite")
        if run.status != RunStatus.READY_FOR_ACTIVITY:
            raise RunTransitionError("Activity draft and submission require the run to be ready for activity")

    def _assert_activity_evidence_sufficient(self, run: Run) -> None:
        pass

    def _has_completed_fix_execution(self, conn: Connection, run_id: int) -> bool:
        row = self._one_or_none(
            conn,
            select(COMMAND_EXECUTIONS.c.id)
            .select_from(COMMAND_EXECUTIONS.join(PROPOSED_STEPS, PROPOSED_STEPS.c.id == COMMAND_EXECUTIONS.c.proposed_step_id))
            .where(and_(COMMAND_EXECUTIONS.c.run_id == run_id, PROPOSED_STEPS.c.phase == "fix", COMMAND_EXECUTIONS.c.status == CommandExecutionStatus.COMPLETED.value))
            .limit(1),
        )
        return row is not None

    def _has_passing_validation_result(self, conn: Connection, run_id: int) -> bool:
        row = self._one_or_none(
            conn,
            select(VALIDATION_RESULTS.c.id).where(and_(VALIDATION_RESULTS.c.run_id == run_id, VALIDATION_RESULTS.c.passed.is_(True))).limit(1),
        )
        return row is not None

    def _assert_activity_draft_complete(self, run: Run, draft: ActivityDraft) -> None:
        if draft.ticket_id != run.ticket_id:
            raise RunTransitionError("Activity draft ticket_id must match the troubleshooting run")
        required_fields = ["summary", "root_cause", "actions_taken", "commands_summary", "validation_result"]
        missing = [field for field in required_fields if not (getattr(draft, field) or "").strip()]
        if missing:
            raise RunTransitionError(f"Activity draft is missing required field(s): {', '.join(missing)}")

    def _run_row(self, conn: Connection, run_id: int, *, lock: bool = False):
        stmt = select(RUNS).where(RUNS.c.id == run_id)
        if lock:
            stmt = stmt.with_for_update()
        return self._one_or_none(conn, stmt)

    def _existing_run_row(self, conn: Connection, run_id: int):
        row = self._run_row(conn, run_id)
        if not row:
            raise RunNotFoundError(f"Run {run_id} was not found")
        return row

    def _step_row(self, conn: Connection, run_id: int, step_id: int, *, lock: bool = False):
        stmt = select(PROPOSED_STEPS).where(and_(PROPOSED_STEPS.c.id == step_id, PROPOSED_STEPS.c.run_id == run_id))
        if lock:
            stmt = stmt.with_for_update()
        return self._one_or_none(conn, stmt)

    def _integration_request_row(self, conn: Connection, run_id: int, integration_request_id: int, *, lock: bool = False):
        stmt = select(INTEGRATION_REQUESTS).where(and_(INTEGRATION_REQUESTS.c.id == integration_request_id, INTEGRATION_REQUESTS.c.run_id == run_id))
        if lock:
            stmt = stmt.with_for_update()
        return self._one_or_none(conn, stmt)

    @staticmethod
    def _one(conn: Connection, statement: Any):
        row = conn.execute(statement).mappings().fetchone()
        if row is None:
            raise RunTransitionError("Expected database row was not returned")
        return row

    @staticmethod
    def _one_or_none(conn: Connection, statement: Any):
        return conn.execute(statement).mappings().fetchone()

    @staticmethod
    def _all(conn: Connection, statement: Any):
        return conn.execute(statement).mappings().fetchall()
