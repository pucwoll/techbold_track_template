from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

import yaml

from app.agent_orchestrator import PlannerOutputError
from app.run_store import InMemoryRunStore
from app.phoenix_client import PhoenixAPIError
from app.schemas import CommandExecutionStatus, IntegrationRequestStatus, OutboxStatus, RunStatus
from app.ssh_runner import CommandExecutionResult, CommandTarget
from app.worker import Worker

from tests.test_run_store import customer_system_snapshot, ticket_snapshot


class FakeRunner:
    def execute(
        self,
        *,
        target: CommandTarget,
        command: str,
        timeout_s: int,
        on_chunk,
    ) -> CommandExecutionResult:
        self.target = target
        self.command = command
        self.timeout_s = timeout_s
        on_chunk("stdout", "Authorization: Bearer sk-demo-secret\n")
        on_chunk("stderr", "warning: harmless\n")
        return CommandExecutionResult(exit_code=0, timed_out=False, duration_ms=12, error=None)


class ScriptedRunner:
    def __init__(
        self,
        *,
        chunks: list[tuple[str, str]] | None = None,
        result: CommandExecutionResult | None = None,
    ) -> None:
        self.chunks = chunks or []
        self.result = result or CommandExecutionResult(exit_code=0, timed_out=False, duration_ms=12, error=None)
        self.command: str | None = None
        self.target: CommandTarget | None = None
        self.timeout_s: int | None = None
        self.called = False

    def execute(
        self,
        *,
        target: CommandTarget,
        command: str,
        timeout_s: int,
        on_chunk,
    ) -> CommandExecutionResult:
        self.called = True
        self.target = target
        self.command = command
        self.timeout_s = timeout_s
        for stream, content in self.chunks:
            on_chunk(stream, content)
        return self.result


class InvalidPlanner:
    def __init__(self) -> None:
        self.calls = 0

    def propose(self, context: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        return {"not": "a valid planner response"}


class ValidPlanner:
    def propose(self, context: dict[str, object]) -> dict[str, object]:
        self.context = context
        return {
            "phase": "diagnostic",
            "command": "systemctl status nginx --no-pager",
            "purpose": "Check nginx state.",
            "hypothesis": "nginx may be failed.",
            "expected_signal": "Service status explains health.",
            "risk_level": "read_only",
            "requires_service_restart": False,
            "persistence_consideration": "No persistent change.",
            "rollback_plan": "No rollback required.",
            "stop_if": "Stop on secrets.",
        }


class FakePhoenixWriter:
    def __init__(self, *, fail_status_once: bool = False, fail_status_attempts: int = 0) -> None:
        self.activities: list[dict[str, object]] = []
        self.status_updates: list[tuple[int, str]] = []
        self.fail_status_once = fail_status_once
        self.fail_status_attempts = fail_status_attempts

    def create_activity(self, payload: dict[str, object]) -> dict[str, object]:
        self.activities.append(payload)
        return {
            **payload,
            "id": 9001,
            "team_id": 42,
            "team_name": "Service Desk",
            "employee_id": 101,
            "created_at": "2026-06-06T10:00:00Z",
        }

    def set_ticket_status(self, ticket_id: int, status: str) -> dict[str, object]:
        if self.fail_status_once or self.fail_status_attempts > 0:
            self.fail_status_once = False
            self.fail_status_attempts = max(0, self.fail_status_attempts - 1)
            raise PhoenixAPIError(503, "Phoenix status patch unavailable")
        self.status_updates.append((ticket_id, status))
        return {
            **ticket_snapshot(),
            "id": ticket_id,
            "status": status,
        }


class WorkerTest(unittest.TestCase):
    def _approved_worker_run(
        self,
        *,
        runner,
        command: str = "journalctl -u nginx --no-pager -n 5",
        phase: str = "diagnostic",
        output_limit_bytes: int = 200,
    ) -> tuple[InMemoryRunStore, Worker, int]:
        store = InMemoryRunStore(command_output_limit_bytes=output_limit_bytes)
        worker = Worker(store=store, runner=runner, command_timeout_s=9, command_output_limit_bytes=output_limit_bytes)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command=command,
            entered_by="Ada Lovelace",
            purpose="Run SSH worker test.",
            phase=phase,
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        return store, worker, run.id

    def _ready_run_with_activity_draft(self) -> tuple[InMemoryRunStore, int]:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        diagnostic_step = store.create_manual_step(
            run.id,
            command="journalctl -u nginx --no-pager -n 80",
            entered_by="Ada Lovelace",
            purpose="Inspect nginx errors.",
        )
        store.approve_step(run.id, diagnostic_step.id, approved_by="Ada Lovelace")
        diagnostic_outbox = store.claim_next_outbox_event()
        self.assertIsNotNone(diagnostic_outbox)
        self.assertEqual(diagnostic_outbox.event_type, "command.execution_requested")
        store.complete_outbox_event(diagnostic_outbox.id)
        diagnostic_execution = store.start_command_execution(run.id, diagnostic_step.id)
        store.append_command_output_chunk(
            run.id,
            diagnostic_execution.id,
            stream="stderr",
            content="nginx: bind() to 0.0.0.0:80 failed (98: Address already in use)\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, diagnostic_execution.id, exit_code=0, duration_ms=30, error=None, timed_out=False)
        validation_step = store.create_manual_step(
            run.id,
            command="curl -I http://localhost",
            entered_by="Ada Lovelace",
            purpose="Validate customer-facing endpoint.",
            phase="validation",
        )
        store.approve_step(run.id, validation_step.id, approved_by="Ada Lovelace")
        validation_outbox = store.claim_next_outbox_event()
        self.assertIsNotNone(validation_outbox)
        self.assertEqual(validation_outbox.event_type, "command.execution_requested")
        store.complete_outbox_event(validation_outbox.id)
        validation_execution = store.start_command_execution(run.id, validation_step.id)
        store.append_command_output_chunk(run.id, validation_execution.id, stream="stdout", content="HTTP/1.1 200 OK\n", redacted=False)
        store.complete_command_execution(run.id, validation_execution.id, exit_code=0, duration_ms=15, error=None, timed_out=False)
        store.create_activity_draft(run.id)
        store.queue_activity_submission(run.id)
        return store, run.id

    def test_worker_executes_approved_command_and_stores_redacted_live_chunks(self) -> None:
        store = InMemoryRunStore(command_output_limit_bytes=200)
        runner = FakeRunner()
        worker = Worker(store=store, runner=runner, command_timeout_s=9, command_output_limit_bytes=200)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="systemctl --failed",
            entered_by="Ada Lovelace",
            purpose="Check failed units.",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")

        processed = worker.process_next()

        self.assertTrue(processed)
        self.assertEqual(runner.command, "systemctl --failed")
        self.assertEqual(runner.target.host, "10.0.0.5")
        self.assertEqual(runner.timeout_s, 9)
        executions = store.list_command_executions(run.id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].status, CommandExecutionStatus.COMPLETED)
        self.assertEqual(executions[0].sanitized_stdout, "[REDACTED_SECRET]\n")
        self.assertNotIn("sk-demo-secret", executions[0].sanitized_stdout)
        chunks = store.list_command_output_chunks(run.id)
        self.assertEqual([chunk.stream for chunk in chunks], ["stdout", "stderr"])
        self.assertEqual(chunks[0].content, "[REDACTED_SECRET]\n")
        self.assertTrue(chunks[0].redacted)
        self.assertIn(
            "terminal.output_chunk",
            [event.event_type for event in store.list_events(run.id)],
        )
        self.assertEqual(store.get_run(run.id).status, RunStatus.INVESTIGATING)

    def test_worker_preserves_chunk_order_stderr_and_exact_approved_command(self) -> None:
        runner = ScriptedRunner(
            chunks=[
                ("stderr", "warning before output\n"),
                ("stdout", "active\n"),
                ("stderr", "warning after output\n"),
            ]
        )
        store, worker, run_id = self._approved_worker_run(runner=runner)

        processed = worker.process_next()

        self.assertTrue(processed)
        self.assertEqual(runner.command, "journalctl -u nginx --no-pager -n 5")
        chunks = store.list_command_output_chunks(run_id)
        self.assertEqual([chunk.sequence for chunk in chunks], [1, 2, 3])
        self.assertEqual([chunk.stream for chunk in chunks], ["stderr", "stdout", "stderr"])
        self.assertEqual([chunk.content for chunk in chunks], ["warning before output\n", "active\n", "warning after output\n"])

    def test_worker_records_nonzero_exit_as_failed_with_stderr(self) -> None:
        runner = ScriptedRunner(
            chunks=[("stderr", "nginx: configuration file test failed\n")],
            result=CommandExecutionResult(exit_code=7, timed_out=False, duration_ms=30, error="Command exited with 7."),
        )
        store, worker, run_id = self._approved_worker_run(runner=runner)

        processed = worker.process_next()

        self.assertTrue(processed)
        execution = store.list_command_executions(run_id)[0]
        self.assertEqual(execution.status, CommandExecutionStatus.FAILED)
        self.assertEqual(execution.exit_code, 7)
        self.assertIn("configuration file test failed", execution.sanitized_stderr)
        self.assertIn("command.failed", [event.event_type for event in store.list_events(run_id)])

    def test_worker_records_timeout_as_timed_out(self) -> None:
        runner = ScriptedRunner(
            chunks=[("stderr", "still running\n")],
            result=CommandExecutionResult(exit_code=None, timed_out=True, duration_ms=9000, error="Command timed out."),
        )
        store, worker, run_id = self._approved_worker_run(runner=runner)

        processed = worker.process_next()

        self.assertTrue(processed)
        execution = store.list_command_executions(run_id)[0]
        self.assertEqual(execution.status, CommandExecutionStatus.TIMED_OUT)
        self.assertTrue(any(event.event_type == "command.timed_out" for event in store.list_events(run_id)))

    def test_worker_stops_planning_when_ssh_authentication_fails_before_command_runs(self) -> None:
        runner = ScriptedRunner(
            result=CommandExecutionResult(
                exit_code=None,
                timed_out=False,
                duration_ms=294,
                error="Authentication failed.",
            ),
        )
        store, worker, run_id = self._approved_worker_run(runner=runner, command="ss -H -ltn")

        processed = worker.process_next()

        self.assertTrue(processed)
        execution = store.list_command_executions(run_id)[0]
        self.assertEqual(execution.status, CommandExecutionStatus.FAILED)
        self.assertEqual(store.get_run(run_id).status, RunStatus.FAILED)
        self.assertEqual(store.list_inspected_sources(run_id), [])
        follow_up_events = [
            event
            for event in store.list_events(run_id)
            if event.event_type == "agent.plan_requested" and event.payload.get("reason") == "command_failed"
        ]
        self.assertEqual(follow_up_events, [])
        self.assertIsNone(store.claim_next_outbox_event())

    def test_worker_enforces_output_cap_and_records_truncation(self) -> None:
        runner = ScriptedRunner(chunks=[("stdout", "x" * 80)])
        store, worker, run_id = self._approved_worker_run(runner=runner, output_limit_bytes=32)

        processed = worker.process_next()

        self.assertTrue(processed)
        execution = store.list_command_executions(run_id)[0]
        chunks = store.list_command_output_chunks(run_id)
        self.assertTrue(execution.output_truncated)
        self.assertEqual(len(chunks[0].content.encode("utf-8")), 32)
        self.assertIn("terminal.output_truncated", [event.event_type for event in store.list_events(run_id)])

    def test_worker_continues_from_fix_to_validation_planning(self) -> None:
        store = InMemoryRunStore()
        runner = FakeRunner()
        worker = Worker(store=store, runner=runner, command_timeout_s=9, command_output_limit_bytes=200)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        diagnostic_step = store.create_manual_step(
            run.id,
            command="journalctl -u nginx --no-pager -n 80",
            entered_by="Ada Lovelace",
            purpose="Inspect nginx errors before the fix.",
        )
        store.approve_step(run.id, diagnostic_step.id, approved_by="Ada Lovelace")
        diagnostic_execution = store.start_command_execution(run.id, diagnostic_step.id)
        store.append_command_output_chunk(
            run.id,
            diagnostic_execution.id,
            stream="stderr",
            content="nginx failed with connection refused errors\n",
            redacted=False,
        )
        store.complete_command_execution(
            run.id,
            diagnostic_execution.id,
            exit_code=0,
            duration_ms=20,
            error=None,
            timed_out=False,
        )
        while setup_event := store.claim_next_outbox_event():
            store.complete_outbox_event(setup_event.id)
        step = store.create_manual_step(
            run.id,
            command="systemctl restart nginx",
            entered_by="Ada Lovelace",
            purpose="Restart only the affected nginx service.",
            phase="fix",
        )
        store.record_backup_not_applicable(
            run.id,
            source_path="nginx",
            reason="Restart does not alter persistent service enablement.",
            recorded_by="Ada Lovelace",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")

        processed_command = worker.process_next()
        processed_validation_plan = worker.process_next()

        self.assertTrue(processed_command)
        self.assertTrue(processed_validation_plan)
        self.assertEqual(store.get_run(run.id).status, RunStatus.AWAITING_STEP_APPROVAL)
        pending_step = store.get_run(run.id).pending_step
        self.assertIsNotNone(pending_step)
        self.assertEqual(pending_step["phase"], "validation")
        self.assertIn("is-active", pending_step["command"])

    def test_worker_skips_execution_after_abort(self) -> None:
        store = InMemoryRunStore()
        runner = ScriptedRunner()
        worker = Worker(store=store, runner=runner, command_timeout_s=9, command_output_limit_bytes=200)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="systemctl --failed",
            entered_by="Ada Lovelace",
            purpose="Check failed units.",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        store.abort_run(run.id, aborted_by="Ada Lovelace", reason="Stop.")

        processed = worker.process_next()

        self.assertTrue(processed)
        self.assertFalse(runner.called)
        self.assertEqual(store.list_command_executions(run.id), [])
        self.assertEqual(store.get_run(run.id).status, RunStatus.ABORTED)
        self.assertEqual(store.list_events(run.id)[-1].event_type, "command.skipped")

    def test_worker_logs_invalid_llm_output_and_falls_back_to_structured_diagnostic(self) -> None:
        store = InMemoryRunStore()
        planner = InvalidPlanner()
        worker = Worker(
            store=store,
            runner=FakeRunner(),
            command_timeout_s=9,
            command_output_limit_bytes=200,
            planner_adapter=planner,
        )
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        processed = worker.process_next()

        self.assertTrue(processed)
        self.assertEqual(planner.calls, 2)
        events = store.list_events(run.id)
        event_types = [event.event_type for event in events]
        self.assertIn("agent.context_built", event_types)
        self.assertEqual(event_types.count("agent.output_invalid"), 2)
        self.assertIn("agent.fallback_used", event_types)
        self.assertLess(
            max(index for index, event_type in enumerate(event_types) if event_type == "agent.output_invalid"),
            event_types.index("agent.fallback_used"),
        )
        pending_step = store.get_run(run.id).pending_step
        self.assertIsNotNone(pending_step)
        self.assertEqual(pending_step["source"], "agent")
        self.assertEqual(pending_step["phase"], "diagnostic")
        self.assertIn("systemctl", pending_step["command"])

    def test_worker_recovers_plan_event_when_deterministic_planner_errors(self) -> None:
        store = InMemoryRunStore()
        worker = Worker(
            store=store,
            runner=FakeRunner(),
            command_timeout_s=9,
            command_output_limit_bytes=200,
        )
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        with patch(
            "app.agent_graph.deterministic_next_step",
            side_effect=PlannerOutputError("No untried deterministic diagnostic command remains."),
        ):
            processed = worker.process_next()

        self.assertTrue(processed)
        outbox_events = store.list_outbox_events(run.id, statuses=set(OutboxStatus))
        self.assertEqual(len(outbox_events), 1)
        self.assertEqual(outbox_events[0].status, OutboxStatus.COMPLETED)
        self.assertEqual(outbox_events[0].attempts, 1)
        self.assertIsNotNone(store.get_run(run.id).pending_step)
        self.assertIn("agent.planning_recovered", [event.event_type for event in store.list_events(run.id)])
        self.assertNotIn("agent.planning_exhausted", [event.event_type for event in store.list_events(run.id)])

    def test_worker_generates_activity_draft_for_completed_validation_suite(self) -> None:
        store = InMemoryRunStore()
        worker = Worker(
            store=store,
            runner=FakeRunner(),
            command_timeout_s=9,
            command_output_limit_bytes=200,
        )
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store._enqueue_outbox(run.id, "agent.activity_draft_requested", {"reason": "validation_suite_passed"})

        with patch("app.worker.draft_activity_with_graph") as draft_activity:
            processed = worker.process_next()

        self.assertTrue(processed)
        draft_activity.assert_called_once_with(store=store, run_id=run.id)

    def test_agent_plan_only_creates_proposed_step_without_ssh_execution(self) -> None:
        store = InMemoryRunStore()
        runner = ScriptedRunner()
        worker = Worker(
            store=store,
            runner=runner,
            command_timeout_s=9,
            command_output_limit_bytes=200,
            planner_adapter=ValidPlanner(),
        )
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        processed = worker.process_next()

        self.assertTrue(processed)
        self.assertFalse(runner.called)
        self.assertEqual(store.list_command_executions(run.id), [])
        pending_step = store.get_run(run.id).pending_step
        self.assertIsNotNone(pending_step)
        self.assertEqual(pending_step["source"], "agent")
        self.assertEqual(pending_step["status"], "proposed")

    def test_worker_submits_activity_then_marks_ticket_done_through_integration_request(self) -> None:
        store, run_id = self._ready_run_with_activity_draft()
        phoenix = FakePhoenixWriter()
        worker = Worker(
            store=store,
            runner=ScriptedRunner(),
            command_timeout_s=9,
            command_output_limit_bytes=200,
            phoenix_client=phoenix,
        )

        processed = worker.process_next()

        self.assertTrue(processed)
        requests = store.list_integration_requests(run_id)
        self.assertEqual(requests[0].status, IntegrationRequestStatus.COMPLETED)
        self.assertEqual(requests[0].phoenix_activity_id, 9001)
        self.assertEqual(phoenix.activities[0]["ticket_id"], 7001)
        self.assertEqual(phoenix.status_updates, [(7001, "DONE")])
        self.assertEqual(store.get_run(run_id).status, RunStatus.SUBMITTED)
        event_types = [event.event_type for event in store.list_events(run_id)]
        self.assertIn("activity.submitted", event_types)
        self.assertIn("ticket.status_updated", event_types)

    def test_worker_activity_payload_matches_phoenix_openapi_contract(self) -> None:
        store, run_id = self._ready_run_with_activity_draft()
        phoenix = FakePhoenixWriter()
        worker = Worker(
            store=store,
            runner=ScriptedRunner(),
            command_timeout_s=9,
            command_output_limit_bytes=200,
            phoenix_client=phoenix,
        )
        spec_path = Path(__file__).resolve().parents[2] / "docs" / "phoenix-openapi.yaml"
        spec = yaml.safe_load(spec_path.read_text())
        activity_schema = spec["components"]["schemas"]["ActivityCreate"]
        schema_fields = set(activity_schema["properties"])
        openapi_required = set(activity_schema["required"])
        scored_required = {
            "summary",
            "root_cause",
            "actions_taken",
            "commands_summary",
            "validation_result",
        }

        worker.process_next()
        payload = phoenix.activities[0]

        self.assertLessEqual(set(payload), schema_fields)
        self.assertEqual(openapi_required, {"ticket_id", "start_datetime", "end_datetime"})
        for field_name in openapi_required | scored_required:
            value = payload.get(field_name)
            self.assertIsInstance(value, str if field_name != "ticket_id" else int)
            if isinstance(value, str):
                self.assertTrue(value.strip(), field_name)

    def test_worker_retries_partial_activity_success_without_duplicate_activity(self) -> None:
        store, run_id = self._ready_run_with_activity_draft()
        phoenix = FakePhoenixWriter(fail_status_attempts=3)
        worker = Worker(
            store=store,
            runner=ScriptedRunner(),
            command_timeout_s=9,
            command_output_limit_bytes=200,
            phoenix_client=phoenix,
        )

        first_processed = worker.process_next()
        request_after_failure = store.list_integration_requests(run_id)[0]
        self.assertEqual(store.get_run(run_id).status, RunStatus.READY_FOR_ACTIVITY)
        outbox_after_failure = next(
            event
            for event in store.list_outbox_events(run_id)
            if event.event_type == "integration.activity_submission_requested"
        )
        store._outbox[outbox_after_failure.id] = outbox_after_failure.model_copy(  # noqa: SLF001
            update={"available_at": outbox_after_failure.created_at}
        )
        second_processed = worker.process_next()

        self.assertTrue(first_processed)
        self.assertEqual(request_after_failure.status, IntegrationRequestStatus.ACTIVITY_CREATED)
        self.assertEqual(request_after_failure.phoenix_activity_id, 9001)
        self.assertIn("status patch unavailable", request_after_failure.error)
        self.assertEqual(len(phoenix.activities), 1)
        self.assertTrue(second_processed)
        self.assertEqual(len(phoenix.activities), 1)
        self.assertEqual(phoenix.status_updates, [(7001, "DONE")])
        self.assertEqual(store.list_integration_requests(run_id)[0].status, IntegrationRequestStatus.COMPLETED)

    def test_worker_retries_guarded_status_patch_without_duplicate_activity(self) -> None:
        store, run_id = self._ready_run_with_activity_draft()
        phoenix = FakePhoenixWriter(fail_status_attempts=2)
        worker = Worker(
            store=store,
            runner=ScriptedRunner(),
            command_timeout_s=9,
            command_output_limit_bytes=200,
            phoenix_client=phoenix,
        )

        processed = worker.process_next()

        self.assertTrue(processed)
        self.assertEqual(len(phoenix.activities), 1)
        self.assertEqual(phoenix.status_updates, [(7001, "DONE")])
        request = store.list_integration_requests(run_id)[0]
        self.assertEqual(request.status, IntegrationRequestStatus.COMPLETED)
        self.assertEqual(request.phoenix_activity_id, 9001)


if __name__ == "__main__":
    unittest.main()
