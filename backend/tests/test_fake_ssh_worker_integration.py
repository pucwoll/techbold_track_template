from __future__ import annotations

import unittest

from app.run_store import InMemoryRunStore
from app.schemas import CommandExecutionStatus, RunStatus
from app.ssh_runner import CommandExecutionResult, CommandTarget
from app.worker import Worker

from tests.test_run_store import customer_system_snapshot, ticket_snapshot


class StreamingFakeSSH:
    def __init__(self) -> None:
        self.calls: list[tuple[CommandTarget, str, int]] = []

    def execute(
        self,
        *,
        target: CommandTarget,
        command: str,
        timeout_s: int,
        on_chunk,
    ) -> CommandExecutionResult:
        self.calls.append((target, command, timeout_s))
        on_chunk("stdout", "nginx.service - failed\n")
        on_chunk("stderr", "bind() to 0.0.0.0:80 failed\n")
        on_chunk("stdout", "See journal for details\n")
        return CommandExecutionResult(exit_code=3, timed_out=False, duration_ms=17, error="Command exited with 3.")


class DiagnosticThenFixPlanner:
    def __init__(self) -> None:
        self.contexts: list[dict[str, object]] = []

    def propose(self, context: dict[str, object]) -> dict[str, object]:
        self.contexts.append(context)
        recent_results = context.get("recent_command_results")
        if not isinstance(recent_results, list) or not recent_results:
            return {
                "phase": "diagnostic",
                "command": "systemctl status nginx --no-pager",
                "purpose": "Check nginx service state.",
                "hypothesis": "nginx may be failed.",
                "expected_signal": "Service status explains the outage.",
                "risk_level": "read_only",
                "requires_service_restart": False,
                "persistence_consideration": "No persistent change.",
                "rollback_plan": "No rollback required.",
                "stop_if": "Stop if secret output appears.",
            }
        return {
            "phase": "fix",
            "command": "systemctl restart nginx",
            "purpose": "Restart only nginx after service evidence showed it failed.",
            "hypothesis": "nginx failed to bind and needs a targeted restart.",
            "expected_signal": "Restart exits cleanly and validation can begin.",
            "risk_level": "low",
            "requires_service_restart": True,
            "persistence_consideration": "Restart does not change persistent configuration.",
            "rollback_plan": "No rollback required; service state is tracked separately.",
            "stop_if": "Stop if restart affects unrelated services.",
            "evidence_references": ["command_execution:1"],
        }


class FakeSSHWorkerIntegrationTest(unittest.TestCase):
    def test_fake_ssh_streaming_is_persisted_and_worker_queues_follow_up_planning(self) -> None:
        store = InMemoryRunStore()
        runner = StreamingFakeSSH()
        worker = Worker(store=store, runner=runner, command_timeout_s=9, command_output_limit_bytes=200)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="systemctl status nginx --no-pager",
            entered_by="Ada Lovelace",
            purpose="Check nginx before choosing a fix.",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")

        processed = worker.process_next()

        self.assertTrue(processed)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0][1], "systemctl status nginx --no-pager")
        execution = store.list_command_executions(run.id)[0]
        self.assertEqual(execution.status, CommandExecutionStatus.FAILED)
        self.assertEqual(execution.sanitized_stdout, "nginx.service - failed\nSee journal for details\n")
        self.assertIn("bind() to 0.0.0.0:80 failed", execution.sanitized_stderr)
        chunks = store.list_command_output_chunks(run.id)
        self.assertEqual([chunk.sequence for chunk in chunks], [1, 2, 3])
        self.assertEqual([chunk.stream for chunk in chunks], ["stdout", "stderr", "stdout"])
        self.assertIn("agent.plan_requested", [event.event_type for event in store.list_events(run.id)])

    def test_planner_diagnostic_approval_fake_output_then_next_step(self) -> None:
        store = InMemoryRunStore()
        runner = StreamingFakeSSH()
        planner = DiagnosticThenFixPlanner()
        worker = Worker(
            store=store,
            runner=runner,
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

        self.assertTrue(worker.process_next())
        pending_diagnostic = store.get_run(run.id).pending_step
        self.assertIsNotNone(pending_diagnostic)
        self.assertEqual(pending_diagnostic["phase"], "diagnostic")
        store.approve_step(run.id, int(pending_diagnostic["id"]), approved_by="Ada Lovelace")

        self.assertTrue(worker.process_next())
        self.assertTrue(worker.process_next())

        follow_up = store.get_run(run.id).pending_step
        self.assertIsNotNone(follow_up)
        self.assertEqual(follow_up["phase"], "fix")
        self.assertEqual(follow_up["command"], "systemctl restart nginx")
        self.assertEqual(store.get_run(run.id).status, RunStatus.AWAITING_STEP_APPROVAL)
        self.assertEqual(len(planner.contexts), 2)
        self.assertEqual(planner.contexts[1]["recent_command_results"][0]["command"], "systemctl status nginx --no-pager")
        self.assertEqual(store.list_inspected_sources(run.id)[0].supports, "root_cause")


if __name__ == "__main__":
    unittest.main()
