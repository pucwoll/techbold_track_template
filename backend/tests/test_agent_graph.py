from __future__ import annotations

import unittest
from unittest.mock import patch

from app.agent_graph import CommandExecutionWorkflow, PlannerWorkflow
from app.agent_orchestrator import PlannerOutputError
from app.run_store import InMemoryRunStore
from app.schemas import CommandExecutionStatus
from app.ssh_runner import CommandExecutionResult, CommandTarget
from tests.test_run_store import customer_system_snapshot, ticket_snapshot


class RecordingPlanner:
    def __init__(self, command: str = "systemctl status nginx --no-pager") -> None:
        self.command = command
        self.calls = 0

    def propose(self, context: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        return {
            "phase": "diagnostic",
            "command": self.command,
            "purpose": "Check nginx state.",
            "hypothesis": "nginx may be failed.",
            "expected_signal": "Service status explains health.",
            "risk_level": "read_only",
            "requires_service_restart": False,
            "persistence_consideration": "No persistent change.",
            "rollback_plan": "No rollback required.",
            "stop_if": "Stop on secrets.",
        }


class InvalidPlanner:
    def __init__(self) -> None:
        self.calls = 0

    def propose(self, context: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        return {"not": "valid planner output"}


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.target: CommandTarget | None = None

    def execute(
        self,
        *,
        target: CommandTarget,
        command: str,
        timeout_s: int,
        on_chunk,
    ) -> CommandExecutionResult:
        self.target = target
        self.commands.append(command)
        on_chunk("stdout", "active\n")
        return CommandExecutionResult(exit_code=0, timed_out=False, duration_ms=7, error=None)


class AgentGraphWorkflowTest(unittest.TestCase):
    def test_command_target_uses_last_ticket_id_digit_as_ssh_key_number(self) -> None:
        store = InMemoryRunStore()
        runner = RecordingRunner()
        workflow = CommandExecutionWorkflow(
            store=store,
            runner=runner,
            command_timeout_s=9,
        )
        run = store.create_run(
            ticket_id=7004,
            ticket_snapshot={**ticket_snapshot(), "id": 7004},
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="ss -H -ltn",
            entered_by="Ada Lovelace",
            purpose="Inspect sockets.",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        outbox_event = store.claim_next_outbox_event()

        workflow.invoke(outbox_event)

        self.assertIsNotNone(runner.target)
        self.assertEqual(runner.target.key_number, 4)

    def test_planner_graph_records_nodes_and_stops_at_technician_approval_checkpoint(self) -> None:
        store = InMemoryRunStore()
        planner = RecordingPlanner()
        workflow = PlannerWorkflow(store=store, planner_adapter=planner, command_timeout_s=9)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        outbox_event = store.claim_next_outbox_event()

        state = workflow.invoke(outbox_event)

        self.assertEqual(
            workflow.node_names,
            (
                "planner_context",
                "llm_planning",
                "deterministic_fallback",
                "validation_planning",
                "safety_classification",
                "approval_checkpoint",
            ),
        )
        self.assertEqual(planner.calls, 1)
        self.assertEqual(state["planner_source"], "llm")
        self.assertEqual(state["nodes_visited"], ["planner_context", "llm_planning", "validation_planning", "safety_classification", "approval_checkpoint"])
        self.assertEqual(state["approval_checkpoint"]["controls"], ["approve", "edit", "reject", "retry", "abort"])
        self.assertEqual(state["safety_verdict"]["verdict"], "allowed")
        self.assertEqual(store.list_command_executions(run.id), [])
        pending_step = store.get_run(run.id).pending_step
        self.assertIsNotNone(pending_step)
        self.assertEqual(pending_step["status"], "proposed")
        self.assertEqual(pending_step["command"], "systemctl status nginx --no-pager")

    def test_planner_graph_retries_invalid_llm_output_then_uses_deterministic_fallback(self) -> None:
        store = InMemoryRunStore()
        planner = InvalidPlanner()
        workflow = PlannerWorkflow(store=store, planner_adapter=planner, command_timeout_s=9)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        outbox_event = store.claim_next_outbox_event()

        state = workflow.invoke(outbox_event)

        self.assertEqual(planner.calls, 2)
        self.assertEqual(state["planner_source"], "deterministic")
        self.assertIn("deterministic_fallback", state["nodes_visited"])
        event_types = [event.event_type for event in store.list_events(run.id)]
        self.assertEqual(event_types.count("agent.output_invalid"), 2)
        self.assertIn("agent.fallback_used", event_types)

    def test_blocked_llm_command_is_classified_but_never_queued_for_execution(self) -> None:
        store = InMemoryRunStore()
        workflow = PlannerWorkflow(store=store, planner_adapter=RecordingPlanner("cat /etc/shadow"), command_timeout_s=9)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        outbox_event = store.claim_next_outbox_event()

        state = workflow.invoke(outbox_event)

        self.assertEqual(state["safety_verdict"]["verdict"], "blocked")
        self.assertIsNone(store.get_run(run.id).pending_step)
        self.assertEqual(store.list_command_executions(run.id), [])
        classification = next(
            event for event in reversed(store.list_events(run.id)) if event.event_type == "step.safety_classified"
        )
        self.assertEqual(classification.approval_status, "blocked")
        self.assertIn("secret material", classification.summary)

    def test_planner_graph_never_proposes_an_already_executed_command(self) -> None:
        store = InMemoryRunStore()
        repeated_command = "systemctl status nginx --no-pager"
        workflow = PlannerWorkflow(
            store=store,
            planner_adapter=RecordingPlanner(repeated_command),
            command_timeout_s=9,
        )
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        initial_plan_event = store.claim_next_outbox_event()
        self.assertIsNotNone(initial_plan_event)
        store.complete_outbox_event(initial_plan_event.id)
        step = store.create_manual_step(
            run.id,
            command=repeated_command,
            entered_by="Ada Lovelace",
            purpose="Record the completed diagnostic command.",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution_event = store.claim_next_outbox_event()
        self.assertIsNotNone(execution_event)
        store.complete_outbox_event(execution_event.id)
        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(
            run.id,
            execution.id,
            stream="stdout",
            content="nginx.service is active\n",
            redacted=False,
        )
        store.complete_command_execution(
            run.id,
            execution.id,
            exit_code=0,
            duration_ms=7,
            error=None,
            timed_out=False,
        )
        follow_up_plan_event = store.claim_next_outbox_event()

        state = workflow.invoke(follow_up_plan_event)

        self.assertFalse(state.get("halted", False))
        pending_step = store.get_run(run.id).pending_step
        self.assertIsNotNone(pending_step)
        self.assertNotEqual(pending_step["command"], repeated_command)
        self.assertIn("agent.repeated_command_replaced", [event.event_type for event in store.list_events(run.id)])

    def test_planner_error_recovers_with_emergency_diagnostic(self) -> None:
        store = InMemoryRunStore()
        workflow = PlannerWorkflow(store=store, planner_adapter=None, command_timeout_s=9)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        outbox_event = store.claim_next_outbox_event()
        self.assertIsNotNone(outbox_event)

        with patch(
            "app.agent_graph.deterministic_next_step",
            side_effect=PlannerOutputError("No untried deterministic diagnostic command remains."),
        ):
            state = workflow.invoke(outbox_event)

        self.assertFalse(state.get("halted", False))
        self.assertIsNotNone(store.get_run(run.id).pending_step)
        self.assertIn("agent.planning_recovered", [event.event_type for event in store.list_events(run.id)])
        self.assertNotIn("agent.planning_exhausted", [event.event_type for event in store.list_events(run.id)])

    def test_command_execution_graph_runs_only_after_approved_step_and_records_validation_node(self) -> None:
        store = InMemoryRunStore()
        runner = RecordingRunner()
        workflow = CommandExecutionWorkflow(
            store=store,
            runner=runner,
            command_timeout_s=9,
        )
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
            purpose="Check failed services.",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        outbox_event = store.claim_next_outbox_event()

        state = workflow.invoke(outbox_event)

        self.assertEqual(workflow.node_names, ("command_request", "command_execution", "validation"))
        self.assertEqual(state["nodes_visited"], ["command_request", "command_execution", "validation"])
        self.assertEqual(runner.commands, ["systemctl --failed"])
        executions = store.list_command_executions(run.id)
        self.assertEqual(len(executions), 1)
        self.assertEqual(executions[0].status, CommandExecutionStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
