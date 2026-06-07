from __future__ import annotations

import unittest

from app.run_store import InMemoryRunStore


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


class Milestone5BackupLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryRunStore()
        self.run = self.store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        self.store.approve_connection(self.run.id, approved_by="Ada Lovelace")

    def test_metadata_snapshot_command_records_pre_change_file_metadata(self) -> None:
        step = self.store.create_manual_step(
            self.run.id,
            command="stat -c 'owner=%U group=%G mode=%a size=%s mtime=%Y checksum=abc123' /srv/app/uploads",
            entered_by="Ada Lovelace",
            purpose="Record ownership and mode before changing uploaded asset directory permissions.",
            phase="diagnostic",
        )
        self.store.approve_step(self.run.id, step.id, approved_by="Ada Lovelace")
        execution = self.store.start_command_execution(self.run.id, step.id)
        self.store.append_command_output_chunk(
            self.run.id,
            execution.id,
            stream="stdout",
            content="owner=www-data group=www-data mode=755 size=4096 mtime=1717675200 checksum=abc123\n",
            redacted=False,
        )
        self.store.complete_command_execution(self.run.id, execution.id, exit_code=0, duration_ms=12, error=None, timed_out=False)

        records = self.store.list_backup_records(self.run.id)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].backup_type, "metadata_snapshot")
        self.assertEqual(records[0].source_path, "/srv/app/uploads")
        self.assertEqual(records[0].owner_before, "www-data")
        self.assertEqual(records[0].group_before, "www-data")
        self.assertEqual(records[0].mode_before, "755")
        self.assertEqual(records[0].size_before, 4096)
        self.assertEqual(records[0].mtime_before, "1717675200")
        self.assertEqual(records[0].checksum_before, "abc123")
        self.assertEqual(records[0].pre_change_hash, "abc123")
        self.assertIn("backup.created", [event.event_type for event in self.store.list_events(self.run.id)])

    def test_service_state_snapshot_command_records_pre_change_systemd_state(self) -> None:
        step = self.store.create_manual_step(
            self.run.id,
            command="systemctl show -p ActiveState -p UnitFileState nginx",
            entered_by="Ada Lovelace",
            purpose="Record nginx service state before restart.",
            phase="diagnostic",
        )
        self.store.approve_step(self.run.id, step.id, approved_by="Ada Lovelace")
        execution = self.store.start_command_execution(self.run.id, step.id)
        self.store.append_command_output_chunk(
            self.run.id,
            execution.id,
            stream="stdout",
            content="ActiveState=active\nUnitFileState=enabled\n",
            redacted=False,
        )
        self.store.complete_command_execution(self.run.id, execution.id, exit_code=0, duration_ms=11, error=None, timed_out=False)

        records = self.store.list_backup_records(self.run.id)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].backup_type, "service_state")
        self.assertEqual(records[0].source_path, "nginx")
        self.assertIn("active", records[0].reason)
        self.assertIn("enabled", records[0].reason)
        self.assertEqual(records[0].restore_command, "systemctl enable --now nginx")
        self.assertTrue(records[0].stored_content)
        self.assertTrue(records[0].persistent_across_reboot)
        self.assertIn("backup.created", [event.event_type for event in self.store.list_events(self.run.id)])

    def test_service_restart_proposal_records_service_state_backup_requirement(self) -> None:
        step = self.store.create_manual_step(
            self.run.id,
            command="systemctl restart nginx",
            entered_by="Ada Lovelace",
            purpose="Restart only the affected nginx service.",
            phase="fix",
        )

        records = self.store.list_backup_records(self.run.id)

        self.assertEqual(step.risk_class, "LOW_RISK")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].backup_type, "service_state")
        self.assertEqual(records[0].source_path, "nginx")
        self.assertTrue(records[0].backup_required)
        self.assertFalse(records[0].backup_created)
        self.assertIn("backup.planned", [event.event_type for event in self.store.list_events(self.run.id)])

    def test_config_diff_command_records_sanitized_diff_without_secret_values(self) -> None:
        step = self.store.create_manual_step(
            self.run.id,
            command=(
                "diff -u /var/backups/techbold-autopilot/7001/1/default.prechange "
                "/etc/nginx/sites-enabled/default"
            ),
            entered_by="Ada Lovelace",
            purpose="Record sanitized nginx config diff after the targeted fix.",
            phase="diagnostic",
        )
        self.store.approve_step(self.run.id, step.id, approved_by="Ada Lovelace")
        execution = self.store.start_command_execution(self.run.id, step.id)
        self.store.append_command_output_chunk(
            self.run.id,
            execution.id,
            stream="stdout",
            content=(
                "--- /var/backups/techbold-autopilot/7001/1/default.prechange\n"
                "+++ /etc/nginx/sites-enabled/default\n"
                "-API_TOKEN=old-secret-value\n"
                "+API_TOKEN=new-secret-value\n"
                "-listen 8080;\n"
                "+listen 80;\n"
            ),
            redacted=False,
        )
        self.store.complete_command_execution(self.run.id, execution.id, exit_code=0, duration_ms=9, error=None, timed_out=False)

        records = self.store.list_backup_records(self.run.id)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].backup_type, "config_dump")
        self.assertEqual(records[0].source_path, "/etc/nginx/sites-enabled/default")
        self.assertEqual(records[0].backup_path, "/var/backups/techbold-autopilot/7001/1/default.prechange")
        self.assertIn("-listen 8080;", records[0].sanitized_diff or "")
        self.assertIn("+listen 80;", records[0].sanitized_diff or "")
        self.assertNotIn("old-secret-value", records[0].sanitized_diff or "")
        self.assertNotIn("new-secret-value", records[0].sanitized_diff or "")
        self.assertTrue(records[0].redacted)
        self.assertTrue(records[0].stored_content)
        self.assertEqual(
            records[0].restore_command,
            "cp -a /var/backups/techbold-autopilot/7001/1/default.prechange /etc/nginx/sites-enabled/default",
        )
        self.assertIn("backup.created", [event.event_type for event in self.store.list_events(self.run.id)])

    def test_restore_command_is_proposed_approved_executed_and_audited_from_backup_record(self) -> None:
        backup_step = self.store.create_manual_step(
            self.run.id,
            command="cp -a /etc/nginx/sites-enabled/default /var/backups/techbold-autopilot/7001/1/default.prechange",
            entered_by="Ada Lovelace",
            purpose="Create targeted config backup.",
            phase="diagnostic",
        )
        self.store.approve_step(self.run.id, backup_step.id, approved_by="Ada Lovelace")
        backup_execution = self.store.start_command_execution(self.run.id, backup_step.id)
        self.store.complete_command_execution(self.run.id, backup_execution.id, exit_code=0, duration_ms=18, error=None, timed_out=False)
        backup_record = self.store.list_backup_records(self.run.id)[0]

        restore_step = self.store.propose_restore_command(
            self.run.id,
            backup_record.id,
            proposed_by="Ada Lovelace",
            reason="Restore the exact nginx config file from the recorded rollback copy.",
        )
        self.store.approve_step(self.run.id, restore_step.id, approved_by="Ada Lovelace")
        restore_execution = self.store.start_command_execution(self.run.id, restore_step.id)
        self.store.complete_command_execution(
            self.run.id,
            restore_execution.id,
            exit_code=0,
            duration_ms=20,
            error=None,
            timed_out=False,
        )

        event_types = [event.event_type for event in self.store.list_events(self.run.id)]

        self.assertEqual(restore_step.command, backup_record.restore_command)
        self.assertEqual(restore_step.phase, "restore")
        self.assertIn("backup.restore_proposed", event_types)
        self.assertIn("step.approved", event_types)
        self.assertIn("command.completed", event_types)
        self.assertIn("backup.restored", event_types)


if __name__ == "__main__":
    unittest.main()
