from __future__ import annotations

import unittest

from app.run_store import InMemoryRunStore, RunTransitionError
from app.safety_layer import classify_command
from app.schemas import RunStatus


def ticket_snapshot() -> dict[str, object]:
    return {
        "id": 7001,
        "title": "API down",
        "description": "Customer cannot reach status endpoint",
        "priority": "high",
        "status": "OPEN",
        "customer_id": 5001,
        "customer_name": "Nordlicht Logistik GmbH",
        "tags": ["api", "urgent"],
    }


def customer_system_snapshot() -> dict[str, object]:
    return {
        "ticket_id": 7001,
        "customer_id": 5001,
        "system": {
            "ip": "10.0.0.5",
            "port": 22,
            "username": "azureuser",
            "os": "Ubuntu 24.04",
            "notes": "nginx reverse proxy",
        },
    }


class Milestone6RunLoopTest(unittest.TestCase):
    def _create_run_with_root_cause_evidence(self) -> tuple[InMemoryRunStore, int]:
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
        diagnostic_execution = store.start_command_execution(run.id, diagnostic_step.id)
        store.append_command_output_chunk(
            run.id,
            diagnostic_execution.id,
            stream="stderr",
            content="nginx: bind() to 0.0.0.0:80 failed (98: Address already in use)\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, diagnostic_execution.id, exit_code=0, duration_ms=30, error=None, timed_out=False)
        return store, run.id

    def _complete_fix(self, store: InMemoryRunStore, run_id: int, command: str = "systemctl restart nginx") -> None:
        fix_step = store.create_manual_step(
            run_id,
            command=command,
            entered_by="Ada Lovelace",
            purpose="Apply a minimal fix based on recorded root-cause evidence.",
            phase="fix",
        )
        if command.startswith("systemctl restart nginx"):
            store.record_backup_not_applicable(
                run_id,
                source_path="nginx",
                reason="Restart validation does not alter persistent service enablement.",
                recorded_by="Ada Lovelace",
            )
        store.approve_step(run_id, fix_step.id, approved_by="Ada Lovelace")
        fix_execution = store.start_command_execution(run_id, fix_step.id)
        store.complete_command_execution(run_id, fix_execution.id, exit_code=0, duration_ms=44, error=None, timed_out=False)

    def _complete_validation(self, store: InMemoryRunStore, run_id: int, command: str, output: str, exit_code: int = 0) -> None:
        step = store.create_manual_step(
            run_id,
            command=command,
            entered_by="Ada Lovelace",
            purpose=f"Run required validation: {command}",
            phase="validation",
        )
        if command.startswith("systemctl restart nginx"):
            store.record_backup_not_applicable(
                run_id,
                source_path="nginx",
                reason="Technician-approved persistence restart does not alter persistent service enablement.",
                recorded_by="Ada Lovelace",
            )
        store.approve_step(run_id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run_id, step.id)
        if output:
            store.append_command_output_chunk(run_id, execution.id, stream="stdout", content=output, redacted=False)
        store.complete_command_execution(
            run_id,
            execution.id,
            exit_code=exit_code,
            duration_ms=12,
            error=None if exit_code == 0 else "Validation failed.",
            timed_out=False,
        )

    def test_fix_approval_requires_root_cause_evidence_and_related_service(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        premature_fix = store.create_manual_step(
            run.id,
            command="systemctl restart nginx",
            entered_by="Ada Lovelace",
            purpose="Restart nginx before evidence exists.",
            phase="fix",
        )

        with self.assertRaisesRegex(RunTransitionError, "root-cause or fix-choice evidence"):
            store.approve_step(run.id, premature_fix.id, approved_by="Ada Lovelace")

        store.reject_step(run.id, premature_fix.id, rejected_by="Ada Lovelace", reason="Need evidence first.")
        store, run_id = self._create_run_with_root_cause_evidence()
        unrelated_fix = store.create_manual_step(
            run_id,
            command="systemctl restart mysql",
            entered_by="Ada Lovelace",
            purpose="Restart mysql even though the evidence is for nginx.",
            phase="fix",
        )

        with self.assertRaisesRegex(RunTransitionError, "unrelated service"):
            store.approve_step(run_id, unrelated_fix.id, approved_by="Ada Lovelace")

        store.reject_step(run_id, unrelated_fix.id, rejected_by="Ada Lovelace", reason="Wrong service.")
        related_fix = store.create_manual_step(
            run_id,
            command="systemctl restart nginx",
            entered_by="Ada Lovelace",
            purpose="Restart only nginx based on recorded root-cause evidence.",
            phase="fix",
        )
        store.record_backup_not_applicable(
            run_id,
            source_path="nginx",
            reason="Restart does not alter persistent service enablement.",
            recorded_by="Ada Lovelace",
        )
        approved = store.approve_step(run_id, related_fix.id, approved_by="Ada Lovelace")

        self.assertEqual(approved.status, RunStatus.FIXING)

    def test_validation_suite_required_before_activity_after_fix(self) -> None:
        store, run_id = self._create_run_with_root_cause_evidence()
        self._complete_fix(store, run_id)

        expectations = store.list_validation_expectations(run_id)

        self.assertEqual(
            [expectation.check_type for expectation in expectations],
            ["service_health", "customer_benefit", "logs_clean", "persistence"],
        )
        self.assertTrue(all(expectation.required for expectation in expectations))
        self.assertTrue(all(expectation.expected_result for expectation in expectations))
        self.assertTrue(all(expectation.relation_to_customer_symptom for expectation in expectations))

        self._complete_validation(store, run_id, "systemctl is-active nginx", "active\n")

        self.assertEqual(store.get_run(run_id).status, RunStatus.VALIDATING)
        with self.assertRaisesRegex(RunTransitionError, "validation suite"):
            store.create_activity_draft(run_id)

        for command, output in [
            ("curl -I http://localhost", "HTTP/1.1 200 OK\n"),
            ("journalctl -u nginx --since -5min --no-pager -n 50", "No recent bind errors\n"),
            ("systemctl restart nginx", ""),
        ]:
            self._complete_validation(store, run_id, command, output)

        updated_expectations = store.list_validation_expectations(run_id)
        self.assertTrue(all(expectation.status == "passed" for expectation in updated_expectations))
        self.assertNotIn("reboot", [expectation.check_type for expectation in updated_expectations])
        self.assertEqual(store.get_run(run_id).status, RunStatus.READY_FOR_ACTIVITY)
        self.assertIn("validation.suite_passed", [event.event_type for event in store.list_events(run_id)])
        self.assertIn("HTTP/1.1 200 OK", store.create_activity_draft(run_id).validation_result)

    def test_failed_validation_requires_new_fix_loop_before_activity(self) -> None:
        store, run_id = self._create_run_with_root_cause_evidence()
        self._complete_fix(store, run_id)

        self._complete_validation(store, run_id, "curl -I http://localhost", "Connection refused\n", exit_code=7)

        self.assertEqual(store.get_run(run_id).status, RunStatus.VALIDATING)
        self.assertTrue(any(expectation.status == "failed" for expectation in store.list_validation_expectations(run_id)))
        with self.assertRaisesRegex(RunTransitionError, "new fix"):
            store.create_activity_draft(run_id)

    def test_fix_policy_blocks_unnecessary_package_installs(self) -> None:
        verdict = classify_command("apt-get install -y nginx")

        self.assertEqual(verdict.verdict, "blocked")
        self.assertIn("Package installation", verdict.summary)

    def test_reboot_validation_is_not_required_but_can_be_technician_approved(self) -> None:
        store, run_id = self._create_run_with_root_cause_evidence()
        self._complete_fix(store, run_id)

        self.assertNotIn("reboot", [expectation.check_type for expectation in store.list_validation_expectations(run_id)])

        reboot_step = store.create_manual_step(
            run_id,
            command="systemctl reboot",
            entered_by="Ada Lovelace",
            purpose="Technician-approved reboot validation for persistence uncertainty.",
            phase="validation",
        )
        approved = store.approve_step(run_id, reboot_step.id, approved_by="Ada Lovelace")
        reboot_execution = store.start_command_execution(run_id, reboot_step.id)
        store.complete_command_execution(run_id, reboot_execution.id, exit_code=0, duration_ms=15, error=None, timed_out=False)

        self.assertEqual(approved.status, RunStatus.VALIDATING)
        self.assertEqual(store.list_validation_results(run_id)[-1].check_type, "reboot")

    def test_mocked_incident_fixture_covers_diagnosis_backup_fix_and_validation(self) -> None:
        store, run_id = self._create_run_with_root_cause_evidence()
        root_source = store.list_inspected_sources(run_id)[0]
        fix_step = store.create_manual_step(
            run_id,
            command="sed -i 's/listen 8080/listen 80/' /etc/nginx/sites-enabled/default",
            entered_by="Ada Lovelace",
            purpose=f"Apply a targeted nginx listen-port fix based on source #{root_source.id}.",
            phase="fix",
        )
        store.record_backup_not_applicable(
            run_id,
            source_path="/etc/nginx/sites-enabled/default",
            reason="Mock fixture uses a disposable config file.",
            recorded_by="Ada Lovelace",
        )
        store.approve_step(run_id, fix_step.id, approved_by="Ada Lovelace")
        fix_execution = store.start_command_execution(run_id, fix_step.id)
        store.complete_command_execution(run_id, fix_execution.id, exit_code=0, duration_ms=44, error=None, timed_out=False)

        for command, output in [
            ("systemctl is-active nginx", "active\n"),
            ("curl -I http://localhost", "HTTP/1.1 200 OK\n"),
            ("journalctl -u nginx --since -5min --no-pager -n 50", "No invalid port errors\n"),
            ("systemctl restart nginx", ""),
        ]:
            self._complete_validation(store, run_id, command, output)

        self.assertEqual(store.get_run(run_id).status, RunStatus.READY_FOR_ACTIVITY)
        self.assertIn("backup.not_applicable", [event.event_type for event in store.list_events(run_id)])
        self.assertEqual(len(store.list_validation_results(run_id)), 4)
        self.assertTrue(store.create_activity_draft(run_id).validation_result)


if __name__ == "__main__":
    unittest.main()
