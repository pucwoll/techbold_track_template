from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.agent_orchestrator import (
    OpenAIChatPlannerAdapter,
    PlannerStep,
    activity_writer,
    build_planner_context,
    fix_planner,
    observation_interpreter,
    parse_planner_output,
    planner_phase_transition,
    deterministic_next_step,
    system_context_planner,
    ticket_analyzer,
    validation_planner,
)
from app.run_store import InMemoryRunStore
from app.schemas import CommandExecution, CommandExecutionStatus, RunStatus, ValidationExpectation
from tests.test_run_store import customer_system_snapshot, ticket_snapshot


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeOpenAICompletions:
    def __init__(self) -> None:
        self.captured: dict[str, object] = {}

    def parse(self, **kwargs: object) -> object:
        self.captured = kwargs
        planner_step = PlannerStep(
            phase="diagnostic",
            command="systemctl status nginx --no-pager",
            purpose="Check nginx state.",
            hypothesis="nginx may be failed.",
            expected_signal="Service status explains health.",
            risk_level="read_only",
            requires_service_restart=False,
            persistence_consideration="No persistent change.",
            rollback_plan="No rollback required.",
            stop_if="Stop on secrets.",
        )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        parsed=planner_step,
                        content=None,
                        refusal=None,
                    )
                )
            ]
        )


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.completions = FakeOpenAICompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class OpenAIChatPlannerAdapterTest(unittest.TestCase):
    def test_adapter_uses_openai_sdk_structured_parse_and_returns_planner_object(self) -> None:
        fake_client = FakeOpenAIClient()
        adapter = OpenAIChatPlannerAdapter(
            api_key="sk-test",
            model="gpt-test",
            client=fake_client,
            timeout_s=3.0,
        )

        output = adapter.propose({"ticket_snapshot": {"title": "nginx down"}, "required_output_schema": {}})

        self.assertEqual(output["phase"], "diagnostic")
        captured = fake_client.completions.captured
        self.assertEqual(captured["model"], "gpt-test")
        self.assertIs(captured["response_format"], PlannerStep)
        messages = captured["messages"]
        self.assertIsInstance(messages, list)
        self.assertIn("exactly one next SSH command", messages[0]["content"])
        self.assertIn("nginx down", messages[1]["content"])

    def test_adapter_redacts_secrets_before_sending_planner_context(self) -> None:
        fake_client = FakeOpenAIClient()
        adapter = OpenAIChatPlannerAdapter(
            api_key="sk-test",
            model="gpt-test",
            client=fake_client,
        )

        adapter.propose(
            {
                "ticket_snapshot": {
                    "title": "nginx down",
                    "description": "Authorization: Bearer sk-demo-secret-token",
                },
                "openai_api_key": "sk-raw-secret",
            }
        )

        user_content = fake_client.completions.captured["messages"][1]["content"]
        self.assertNotIn("sk-demo-secret-token", user_content)
        self.assertNotIn("sk-raw-secret", user_content)
        self.assertIn("[REDACTED_SECRET]", user_content)


class PlannerPhaseFunctionTest(unittest.TestCase):
    def test_named_planner_functions_expose_basic_phase_contracts(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        command = CommandExecution(
            id=1,
            run_id=run.id,
            proposed_step_id=1,
            approved_command="journalctl -u nginx --no-pager -n 80",
            status=CommandExecutionStatus.COMPLETED,
            target_host="10.0.0.5",
            target_port=22,
            target_username="azureuser",
            timeout_s=30,
            output_limit_bytes=20_000,
            completed_at=datetime.now(UTC),
            exit_code=0,
            sanitized_stdout="nginx failed with bind() address already in use",
        )

        ticket_context = ticket_analyzer(run)
        diagnostic = system_context_planner(run, ticket_context)
        observation = observation_interpreter(run, command, diagnostic)
        fix = fix_planner(run, observation=observation, latest_command=command)
        validation = validation_planner(run, service="nginx", latest_command=command)
        activity = activity_writer(run=run, events=[], commands=[command], inspected_sources=[])

        self.assertIn("nginx", ticket_context["service_candidates"])
        self.assertEqual(ticket_context["customer_benefit"], "Customer cannot reach status endpoint")
        self.assertIsInstance(diagnostic, PlannerStep)
        self.assertEqual(diagnostic.phase, "diagnostic")
        self.assertEqual(observation["root_cause_candidate"], "nginx reported a bind/listen failure.")
        self.assertIsInstance(fix, PlannerStep)
        self.assertEqual(fix.phase, "fix")
        self.assertIn("nginx", fix.command)
        self.assertIsInstance(validation, PlannerStep)
        self.assertEqual(validation.phase, "validation")
        self.assertEqual(activity["ticket_id"], 7001)
        self.assertIn("commands_summary", activity)

    def test_fix_planner_enables_discovered_service_when_status_shows_disabled_and_inactive(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot={
                **ticket_snapshot(),
                "title": "Status API intermittently unavailable",
            },
            customer_system_snapshot=customer_system_snapshot(),
        )
        status_command = CommandExecution(
            id=8,
            run_id=run.id,
            proposed_step_id=108,
            approved_command="systemctl status customer-status --no-pager",
            status=CommandExecutionStatus.COMPLETED,
            target_host="52.157.108.111",
            target_port=22,
            target_username="azureuser",
            timeout_s=30,
            output_limit_bytes=20_000,
            completed_at=datetime.now(UTC),
            exit_code=3,
            sanitized_stdout=(
                "Loaded: loaded (/etc/systemd/system/customer-status.service; disabled; preset: enabled)\n"
                "Active: inactive (dead)\n"
            ),
        )

        observation = observation_interpreter(
            run,
            status_command,
            SimpleNamespace(phase="diagnostic"),
        )
        fix = fix_planner(run, observation=observation, latest_command=status_command)

        self.assertEqual(fix.command, "sudo -n systemctl enable --now customer-status")
        self.assertEqual(fix.phase, "fix")
        self.assertTrue(fix.requires_service_restart)
        self.assertIn("persistent", fix.persistence_consideration.lower())
        self.assertIn("disable", fix.rollback_plan)
        self.assertEqual(fix.evidence_references, ["command_execution:8"])

    def test_deterministic_planner_validates_service_enabled_by_fix(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot={
                **ticket_snapshot(),
                "title": "Status API intermittently unavailable",
            },
            customer_system_snapshot=customer_system_snapshot(),
        )
        completed_fix = CommandExecution(
            id=9,
            run_id=run.id,
            proposed_step_id=109,
            approved_command="sudo -n systemctl enable --now customer-status",
            status=CommandExecutionStatus.COMPLETED,
            target_host="52.157.108.111",
            target_port=22,
            target_username="azureuser",
            timeout_s=30,
            output_limit_bytes=20_000,
            completed_at=datetime.now(UTC),
            exit_code=0,
        )

        validation = deterministic_next_step(
            run=run,
            commands=[completed_fix],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="fix"),
        )

        self.assertEqual(validation.command, "systemctl is-active customer-status")
        self.assertEqual(validation.phase, "validation")

    def test_post_fix_validation_can_repeat_a_pre_fix_customer_check(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot={
                **ticket_snapshot(),
                "description": "Status API is unavailable at http://localhost:8080/health",
            },
            customer_system_snapshot=customer_system_snapshot(),
        ).model_copy(update={"status": RunStatus.VALIDATING})
        now = datetime.now(UTC)
        commands = [
            CommandExecution(
                id=1,
                run_id=run.id,
                proposed_step_id=101,
                approved_command="curl -I http://localhost:8080/health",
                status=CommandExecutionStatus.FAILED,
                target_host="52.157.108.111",
                target_port=22,
                target_username="azureuser",
                timeout_s=30,
                output_limit_bytes=20_000,
                completed_at=now,
                exit_code=7,
            ),
            CommandExecution(
                id=2,
                run_id=run.id,
                proposed_step_id=102,
                approved_command="sudo -n systemctl enable --now customer-status",
                status=CommandExecutionStatus.COMPLETED,
                target_host="52.157.108.111",
                target_port=22,
                target_username="azureuser",
                timeout_s=30,
                output_limit_bytes=20_000,
                completed_at=now,
                exit_code=0,
            ),
            CommandExecution(
                id=3,
                run_id=run.id,
                proposed_step_id=103,
                approved_command="systemctl is-active customer-status",
                status=CommandExecutionStatus.COMPLETED,
                target_host="52.157.108.111",
                target_port=22,
                target_username="azureuser",
                timeout_s=30,
                output_limit_bytes=20_000,
                completed_at=now,
                exit_code=0,
                sanitized_stdout="active\n",
            ),
        ]
        expectations = [
            ValidationExpectation(
                id=1,
                run_id=run.id,
                fix_command_execution_id=2,
                check_type="service_health",
                target="customer-status",
                expected_result="Service is active.",
                relation_to_customer_symptom="Service health is required.",
                status="passed",
                created_at=now,
            ),
            ValidationExpectation(
                id=2,
                run_id=run.id,
                fix_command_execution_id=2,
                check_type="customer_benefit",
                target="http://localhost:8080/health",
                expected_result="Health endpoint succeeds.",
                relation_to_customer_symptom="Directly verifies the reported endpoint.",
                created_at=now,
            ),
        ]

        next_step = deterministic_next_step(
            run=run,
            commands=commands,
            step_for_execution=lambda step_id: SimpleNamespace(
                phase={101: "validation", 102: "fix", 103: "validation"}[step_id]
            ),
            validation_expectations=expectations,
        )

        self.assertEqual(next_step.command, "curl -I http://localhost:8080/health")
        self.assertEqual(next_step.phase, "validation")

    def test_persistence_validation_uses_non_interactive_sudo(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        ).model_copy(update={"status": RunStatus.VALIDATING})
        expectation = ValidationExpectation(
            id=1,
            run_id=run.id,
            fix_command_execution_id=1,
            check_type="persistence",
            target="customer-status",
            expected_result="Service survives restart.",
            relation_to_customer_symptom="The fix must persist.",
            created_at=datetime.now(UTC),
        )

        next_step = deterministic_next_step(
            run=run,
            commands=[],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="validation"),
            validation_expectations=[expectation],
        )

        self.assertEqual(next_step.command, "sudo -n systemctl restart customer-status")

    def test_required_public_validation_runs_exact_ticket_command(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        ).model_copy(update={"status": RunStatus.VALIDATING})
        expectation = ValidationExpectation(
            id=1,
            run_id=run.id,
            fix_command_execution_id=1,
            check_type="public_validation",
            target="sudo /opt/hackathon/public-test.sh",
            expected_result="Public validation passes.",
            relation_to_customer_symptom="Required by the ticket.",
            created_at=datetime.now(UTC),
        )

        next_step = deterministic_next_step(
            run=run,
            commands=[],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="validation"),
            validation_expectations=[expectation],
        )

        self.assertEqual(next_step.command, "sudo /opt/hackathon/public-test.sh")
        self.assertEqual(next_step.phase, "validation")

    def test_journal_follow_up_inspects_custom_service_unit_definition(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        journal = CommandExecution(
            id=1,
            run_id=run.id,
            proposed_step_id=101,
            approved_command="journalctl -u customer-status --no-pager -n 80",
            status=CommandExecutionStatus.COMPLETED,
            target_host="52.157.108.111",
            target_port=22,
            target_username="azureuser",
            timeout_s=30,
            output_limit_bytes=20_000,
            completed_at=datetime.now(UTC),
            exit_code=0,
            sanitized_stdout="No recent errors.\n",
        )

        next_step = deterministic_next_step(
            run=run,
            commands=[journal],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="diagnostic"),
        )

        self.assertEqual(next_step.command, "systemctl cat customer-status")

    def test_unit_with_environment_file_inspects_only_declared_port_setting(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot={
                **ticket_snapshot(),
                "description": "Status API unavailable at http://localhost:8080/health",
            },
            customer_system_snapshot=customer_system_snapshot(),
        )
        unit = CommandExecution(
            id=2,
            run_id=run.id,
            proposed_step_id=102,
            approved_command="systemctl cat customer-status",
            status=CommandExecutionStatus.COMPLETED,
            target_host="52.157.108.111",
            target_port=22,
            target_username="azureuser",
            timeout_s=30,
            output_limit_bytes=20_000,
            completed_at=datetime.now(UTC),
            exit_code=0,
            sanitized_stdout=(
                "[Service]\n"
                "EnvironmentFile=/etc/customer-status.env\n"
                "ExecStart=/usr/bin/python3 /opt/customer-status/app.py\n"
            ),
        )

        next_step = deterministic_next_step(
            run=run,
            commands=[unit],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="diagnostic"),
        )

        self.assertEqual(
            next_step.command,
            "grep -E '^PORT=[0-9]+$' /etc/customer-status.env",
        )
        self.assertEqual(next_step.phase, "diagnostic")

    def test_wrong_environment_port_proposes_targeted_persistent_fix(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot={
                **ticket_snapshot(),
                "description": "Status API unavailable at http://localhost:8080/health",
            },
            customer_system_snapshot={
                **customer_system_snapshot(),
                "system": {
                    **customer_system_snapshot()["system"],
                    "notes": "nginx reverse proxy",
                },
            },
        )
        commands = [
            CommandExecution(
                id=1,
                run_id=run.id,
                proposed_step_id=101,
                approved_command="systemctl status customer-status --no-pager",
                status=CommandExecutionStatus.COMPLETED,
                target_host="52.157.108.111",
                target_port=22,
                target_username="azureuser",
                timeout_s=30,
                output_limit_bytes=20_000,
                completed_at=datetime.now(UTC),
                exit_code=0,
                sanitized_stdout="Active: active (running)\n",
            ),
            CommandExecution(
                id=2,
                run_id=run.id,
                proposed_step_id=102,
                approved_command="grep -E '^PORT=[0-9]+$' /etc/customer-status.env",
                status=CommandExecutionStatus.COMPLETED,
                target_host="52.157.108.111",
                target_port=22,
                target_username="azureuser",
                timeout_s=30,
                output_limit_bytes=20_000,
                completed_at=datetime.now(UTC),
                exit_code=0,
                sanitized_stdout="PORT=8008\n",
            ),
        ]

        next_step = deterministic_next_step(
            run=run,
            commands=commands,
            step_for_execution=lambda _step_id: SimpleNamespace(phase="diagnostic"),
        )

        self.assertEqual(
            next_step.command,
            "sudo -n sed -i.techbold-prechange 's/^PORT=[0-9]\\+$/PORT=8080/' /etc/customer-status.env",
        )
        self.assertEqual(next_step.phase, "fix")
        self.assertNotIn("nginx", next_step.command)

    def test_exhausted_diagnostic_ladder_expands_without_repeating_commands(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot={
                **ticket_snapshot(),
                "description": "Status API unavailable at http://localhost:8080/health",
            },
            customer_system_snapshot=customer_system_snapshot(),
        )
        now = datetime.now(UTC)
        attempted_commands = [
            "systemctl status customer-status --no-pager",
            "journalctl -u customer-status --no-pager -n 80",
            "systemctl cat customer-status",
            (
                "systemctl show customer-status "
                "--property=LoadState,ActiveState,SubState,UnitFileState,ExecStart,Restart,NRestarts,Result --no-pager"
            ),
            "systemctl list-dependencies customer-status --all --no-pager",
            "ss -H -ltnp",
            "curl -fsS http://localhost:8080/health",
            "journalctl -u customer-status --since -15min --no-pager -n 200",
            "systemctl list-units --type=service --state=running --no-pager",
            "ps -eo pid,comm,args --sort=comm",
            "systemctl list-sockets --all --no-pager",
            "systemctl list-unit-files --type=service --no-pager",
        ]
        commands = [
            CommandExecution(
                id=index,
                run_id=run.id,
                proposed_step_id=100 + index,
                approved_command=command,
                status=CommandExecutionStatus.COMPLETED,
                target_host="52.157.108.111",
                target_port=22,
                target_username="azureuser",
                timeout_s=30,
                output_limit_bytes=20_000,
                completed_at=now,
                exit_code=0,
            )
            for index, command in enumerate(attempted_commands, start=1)
        ]

        next_step = deterministic_next_step(
            run=run,
            commands=commands,
            step_for_execution=lambda _step_id: SimpleNamespace(phase="diagnostic"),
        )

        self.assertNotIn(next_step.command, attempted_commands)
        self.assertRegex(next_step.command, r"journalctl -u customer-status --since -\d+min")

    def test_fix_planner_output_requires_evidence_or_more_diagnosis_explanation(self) -> None:
        payload = {
            "phase": "fix",
            "command": "systemctl restart nginx",
            "purpose": "Restart nginx.",
            "hypothesis": "nginx is failed.",
            "expected_signal": "Restart exits 0.",
            "risk_level": "low",
            "requires_service_restart": True,
            "persistence_consideration": "No persistent change.",
            "rollback_plan": "Restart can be repeated.",
            "stop_if": "Stop on unexpected errors.",
        }

        with self.assertRaisesRegex(ValueError, "evidence"):
            parse_planner_output(payload)

        with_evidence = parse_planner_output(payload | {"evidence_references": ["command_execution:1"]})
        with_gap = parse_planner_output(payload | {"needs_more_diagnosis": True, "diagnosis_gap": "Need journal evidence before editing config."})

        self.assertEqual(with_evidence.evidence_references, ["command_execution:1"])
        self.assertTrue(with_gap.needs_more_diagnosis)

    def test_planner_phase_transition_is_explicit_from_latest_step_and_observation(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        failed_status = CommandExecution(
            id=1,
            run_id=run.id,
            proposed_step_id=10,
            approved_command="systemctl status nginx --no-pager",
            status=CommandExecutionStatus.COMPLETED,
            target_host="10.0.0.5",
            target_port=22,
            target_username="azureuser",
            timeout_s=30,
            output_limit_bytes=20_000,
            completed_at=datetime.now(UTC),
            exit_code=3,
            sanitized_stdout="nginx.service - failed",
        )
        completed_fix = failed_status.model_copy(
            update={
                "id": 2,
                "proposed_step_id": 11,
                "approved_command": "systemctl restart nginx",
                "exit_code": 0,
                "sanitized_stdout": "",
            }
        )

        diagnostic_decision = planner_phase_transition(
            run=run,
            commands=[failed_status],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="diagnostic"),
        )
        fix_decision = planner_phase_transition(
            run=run,
            commands=[completed_fix],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="fix"),
        )

        self.assertEqual(diagnostic_decision.next_phase, "fix")
        self.assertEqual(diagnostic_decision.previous_phase, "diagnostic")
        self.assertIn("failed", diagnostic_decision.reason)
        self.assertEqual(fix_decision.next_phase, "validation")
        self.assertEqual(fix_decision.previous_phase, "fix")

    def test_planner_context_contains_required_state_sections(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )

        context = build_planner_context(
            run=run,
            events=store.list_events(run.id),
            commands=[],
            inspected_sources=[],
            backup_records=[],
            validation_results=[],
        )

        for key in [
            "ticket_snapshot",
            "customer_system_snapshot",
            "sanitized_timeline",
            "latest_evidence",
            "backup_state",
            "validation_state",
            "safety_rules",
        ]:
            self.assertIn(key, context)
        self.assertEqual(context["ticket_snapshot"]["id"], 7001)
        self.assertEqual(context["customer_system_snapshot"]["system"]["ip"], "10.0.0.5")
        self.assertEqual(context["sanitized_timeline"][0]["event_type"], "run.created")
        self.assertEqual(context["latest_evidence"], [])
        self.assertFalse(context["backup_state"]["has_backup"])
        self.assertFalse(context["validation_state"]["has_passed_validation"])
        self.assertIn("approval", context["safety_rules"])

    def test_deterministic_fallback_handles_resource_and_port_symptoms(self) -> None:
        store = InMemoryRunStore()
        disk_system = customer_system_snapshot()
        disk_system["system"]["notes"] = ""
        port_system = customer_system_snapshot()
        port_system["system"]["notes"] = ""
        disk_run = store.create_run(
            ticket_id=8001,
            ticket_snapshot={
                **ticket_snapshot(),
                "id": 8001,
                "title": "Uploads failing",
                "description": "Customer uploads fail because the server reports no space left on device.",
            },
            customer_system_snapshot=disk_system,
        )
        port_run = store.create_run(
            ticket_id=8002,
            ticket_snapshot={
                **ticket_snapshot(),
                "id": 8002,
                "title": "API port unavailable",
                "description": "Customer cannot connect to TCP port 8080.",
            },
            customer_system_snapshot=port_system,
        )

        disk_step = deterministic_next_step(run=disk_run, commands=[], step_for_execution=lambda _step_id: None)
        port_step = deterministic_next_step(run=port_run, commands=[], step_for_execution=lambda _step_id: None)

        self.assertEqual(disk_step.command, "df -h /")
        self.assertEqual(port_step.command, "ss -H -ltn")

    def test_port_diagnostic_avoids_process_owner_lookup_that_can_hang(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=8003,
            ticket_snapshot={
                **ticket_snapshot(),
                "id": 8003,
                "title": "API port unavailable",
                "description": "Customer cannot connect to TCP port 8080.",
            },
            customer_system_snapshot=customer_system_snapshot(),
        )

        step = system_context_planner(run)

        self.assertEqual(step.command, "ss -H -ltn")
        self.assertNotIn("p", step.command.split()[-1])
        self.assertIn("listening TCP", step.purpose)

    def test_deterministic_fallback_does_not_repeat_socket_diagnostic_after_successful_ss_observation(self) -> None:
        store = InMemoryRunStore()
        system = customer_system_snapshot()
        system["system"]["notes"] = ""
        run = store.create_run(
            ticket_id=8004,
            ticket_snapshot={
                **ticket_snapshot(),
                "id": 8004,
                "title": "API port unavailable",
                "description": "Customer cannot connect to TCP port 8080.",
            },
            customer_system_snapshot=system,
        )
        completed_socket_check = CommandExecution(
            id=22,
            run_id=run.id,
            proposed_step_id=122,
            approved_command="ss -ltnp",
            status=CommandExecutionStatus.COMPLETED,
            target_host="52.157.108.111",
            target_port=22,
            target_username="azureuser",
            timeout_s=30,
            output_limit_bytes=20_000,
            completed_at=datetime.now(UTC),
            exit_code=0,
            sanitized_stdout=(
                "State Recv-Q Send-Q Local Address:Port Peer Address:PortProcess\n"
                "LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n"
            ),
        )

        next_step = deterministic_next_step(
            run=run,
            commands=[completed_socket_check],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="diagnostic"),
        )

        self.assertNotIn("ss ", f"{next_step.command} ")
        self.assertEqual(next_step.command, "systemctl --failed")

    def test_deterministic_fallback_does_not_repeat_failed_validation_command(self) -> None:
        store = InMemoryRunStore()
        system = customer_system_snapshot()
        system["system"]["notes"] = ""
        run = store.create_run(
            ticket_id=8005,
            ticket_snapshot={
                **ticket_snapshot(),
                "id": 8005,
                "title": "API port unavailable",
                "description": "Customer cannot connect to TCP port 8080.",
            },
            customer_system_snapshot=system,
        )
        completed_commands = [
            CommandExecution(
                id=command_id,
                run_id=run.id,
                proposed_step_id=100 + command_id,
                approved_command=command,
                status=CommandExecutionStatus.COMPLETED,
                target_host="52.157.108.111",
                target_port=22,
                target_username="azureuser",
                timeout_s=30,
                output_limit_bytes=20_000,
                completed_at=datetime.now(UTC),
                exit_code=exit_code,
                sanitized_stdout=stdout,
                sanitized_stderr=stderr,
            )
            for command_id, command, exit_code, stdout, stderr in [
                (1, "ss -H -ltn", 0, "LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n", ""),
                (2, "systemctl --failed", 0, "0 loaded units listed.\n", ""),
                (3, "curl -I http://localhost", 7, "", "curl: (7) Failed to connect to localhost port 80\n"),
            ]
        ]

        next_step = deterministic_next_step(
            run=run,
            commands=completed_commands,
            step_for_execution=lambda step_id: SimpleNamespace(
                phase="validation" if step_id == 103 else "diagnostic"
            ),
        )

        self.assertNotEqual(next_step.command, "curl -I http://localhost")
        self.assertNotIn(next_step.command, [command.approved_command for command in completed_commands])
        self.assertEqual(next_step.phase, "diagnostic")

    def test_deterministic_fallback_advances_after_running_service_inventory(self) -> None:
        store = InMemoryRunStore()
        system = customer_system_snapshot()
        system["system"]["notes"] = ""
        run = store.create_run(
            ticket_id=8006,
            ticket_snapshot={
                **ticket_snapshot(),
                "id": 8006,
                "title": "API port unavailable",
                "description": "Customer cannot connect to TCP port 8080.",
            },
            customer_system_snapshot=system,
        )
        command_rows = [
            (1, "ss -H -ltn", 0, "LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n", "diagnostic"),
            (2, "systemctl --failed", 0, "0 loaded units listed.\n", "diagnostic"),
            (3, "curl -I http://localhost", 7, "", "validation"),
            (
                4,
                "systemctl list-units --type=service --state=running --no-pager",
                0,
                "ssh.service loaded active running OpenBSD Secure Shell server\n",
                "diagnostic",
            ),
        ]
        completed_commands = [
            CommandExecution(
                id=command_id,
                run_id=run.id,
                proposed_step_id=100 + command_id,
                approved_command=command,
                status=CommandExecutionStatus.COMPLETED,
                target_host="52.157.108.111",
                target_port=22,
                target_username="azureuser",
                timeout_s=30,
                output_limit_bytes=20_000,
                completed_at=datetime.now(UTC),
                exit_code=exit_code,
                sanitized_stdout=stdout,
            )
            for command_id, command, exit_code, stdout, _phase in command_rows
        ]
        phases = {100 + command_id: phase for command_id, _command, _exit_code, _stdout, phase in command_rows}

        next_step = deterministic_next_step(
            run=run,
            commands=completed_commands,
            step_for_execution=lambda step_id: SimpleNamespace(phase=phases[step_id]),
        )

        self.assertEqual(next_step.command, "ps -eo pid,comm,args --sort=comm")
        self.assertEqual(next_step.phase, "diagnostic")

    def test_deterministic_fallback_inspects_ticket_relevant_installed_service(self) -> None:
        store = InMemoryRunStore()
        system = customer_system_snapshot()
        system["system"]["notes"] = ""
        run = store.create_run(
            ticket_id=8007,
            ticket_snapshot={
                **ticket_snapshot(),
                "id": 8007,
                "title": "Status API intermittently unavailable",
                "description": "Customer cannot reach the status endpoint.",
            },
            customer_system_snapshot=system,
        )
        commands = [
            CommandExecution(
                id=index,
                run_id=run.id,
                proposed_step_id=200 + index,
                approved_command=command,
                status=CommandExecutionStatus.COMPLETED,
                target_host="52.157.108.111",
                target_port=22,
                target_username="azureuser",
                timeout_s=30,
                output_limit_bytes=20_000,
                completed_at=datetime.now(UTC),
                exit_code=exit_code,
                sanitized_stdout=stdout,
                sanitized_stderr=stderr,
            )
            for index, command, exit_code, stdout, stderr in [
                (1, "ss -H -ltn", 0, "LISTEN 0 4096 0.0.0.0:22 0.0.0.0:*\n", ""),
                (2, "systemctl --failed", 0, "0 loaded units listed.\n", ""),
                (3, "curl -I http://localhost", 7, "", "curl: (7) Failed to connect\n"),
                (4, "systemctl list-units --type=service --state=running --no-pager", 0, "ssh.service active running\n", ""),
                (5, "ps -eo pid,comm,args --sort=comm", 0, "1 systemd /usr/lib/systemd/systemd\n", ""),
                (6, "systemctl list-sockets --all --no-pager", 0, "0.0.0.0:22 ssh.socket ssh.service\n", ""),
                (
                    7,
                    "systemctl list-unit-files --type=service --no-pager",
                    0,
                    "customer-status.service disabled enabled\nssh.service disabled enabled\n",
                    "",
                ),
            ]
        ]

        next_step = deterministic_next_step(
            run=run,
            commands=commands,
            step_for_execution=lambda step_id: SimpleNamespace(
                phase="validation" if step_id == 203 else "diagnostic"
            ),
        )

        self.assertEqual(next_step.command, "systemctl status customer-status --no-pager")
        self.assertEqual(next_step.phase, "diagnostic")

    def test_service_discovery_prefers_customer_status_over_unrelated_boot_unit(self) -> None:
        store = InMemoryRunStore()
        system = customer_system_snapshot()
        system["system"]["notes"] = ""
        run = store.create_run(
            ticket_id=8008,
            ticket_snapshot={
                **ticket_snapshot(),
                "id": 8008,
                "title": "Status API intermittently unavailable",
                "description": "Customer cannot reach the status endpoint.",
            },
            customer_system_snapshot=system,
        )
        unit_list = CommandExecution(
            id=7,
            run_id=run.id,
            proposed_step_id=207,
            approved_command="systemctl list-unit-files --type=service --no-pager",
            status=CommandExecutionStatus.COMPLETED,
            target_host="52.157.108.111",
            target_port=22,
            target_username="azureuser",
            timeout_s=30,
            output_limit_bytes=20_000,
            completed_at=datetime.now(UTC),
            exit_code=0,
            sanitized_stdout=(
                "customer-status.service disabled enabled\n"
                "plymouth-switch-root-initramfs.service static -\n"
            ),
        )

        next_step = deterministic_next_step(
            run=run,
            commands=[unit_list],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="diagnostic"),
        )

        self.assertEqual(next_step.command, "systemctl status customer-status --no-pager")

    def test_failed_fix_returns_to_diagnosis_instead_of_validation(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=8009,
            ticket_snapshot={
                **ticket_snapshot(),
                "id": 8009,
                "title": "Status API intermittently unavailable",
            },
            customer_system_snapshot=customer_system_snapshot(),
        )
        failed_fix = CommandExecution(
            id=10,
            run_id=run.id,
            proposed_step_id=210,
            approved_command="systemctl restart customer-status",
            status=CommandExecutionStatus.FAILED,
            target_host="52.157.108.111",
            target_port=22,
            target_username="azureuser",
            timeout_s=30,
            output_limit_bytes=20_000,
            completed_at=datetime.now(UTC),
            exit_code=1,
            sanitized_stderr="Interactive authentication required.",
        )

        next_step = deterministic_next_step(
            run=run,
            commands=[failed_fix],
            step_for_execution=lambda _step_id: SimpleNamespace(phase="fix"),
        )

        self.assertEqual(next_step.phase, "diagnostic")
        self.assertNotIn("is-active", next_step.command)


if __name__ == "__main__":
    unittest.main()
