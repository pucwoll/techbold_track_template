from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import unittest

from app.audit_store import AuditStore
from app.run_store import InMemoryRunStore, PostgresRunStore, RunTransitionError
from app.schemas import ActivityDraft, CommandExecutionStatus, IntegrationRequestStatus, OutboxStatus, RunStatus, StepStatus


REPO_ROOT = Path(__file__).resolve().parents[2]


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


class RunStoreTest(unittest.TestCase):
    def test_postgres_persistence_uses_sqlalchemy_and_alembic_not_psycopg_runner(self) -> None:
        from app import phoenix_cache, run_store

        run_store_source = (REPO_ROOT / "backend" / "app" / "run_store.py").read_text()
        cache_source = (REPO_ROOT / "backend" / "app" / "phoenix_cache.py").read_text()

        self.assertNotIn("import psycopg", run_store_source)
        self.assertNotIn("psycopg.connect", run_store_source)
        self.assertNotIn("run_migrations", run_store_source)
        self.assertNotIn("POSTGRES_SCHEMA_SQL", run_store_source)
        self.assertFalse((REPO_ROOT / "backend" / "app" / "persistence_schema.py").exists())
        self.assertFalse((REPO_ROOT / "backend" / "app" / "migrations.py").exists())
        self.assertNotIn("import psycopg", cache_source)
        self.assertNotIn("psycopg.connect", cache_source)
        self.assertTrue(hasattr(run_store.PostgresRunStore, "_engine"))
        self.assertTrue(hasattr(phoenix_cache.PostgresPhoenixCache, "_engine"))

    def test_alembic_startup_helper_runs_head_for_application_schema(self) -> None:
        from app.database import alembic_config, run_database_migrations

        config = alembic_config("postgresql+psycopg://user:pass@localhost/db")

        self.assertEqual(config.get_main_option("sqlalchemy.url"), "postgresql+psycopg://user:pass@localhost/db")
        self.assertTrue((REPO_ROOT / "backend" / "alembic.ini").exists())
        self.assertTrue((REPO_ROOT / "backend" / "alembic" / "versions" / "20260607_0001_initial_run_store.py").exists())
        self.assertTrue(callable(run_database_migrations))

    def test_create_run_persists_snapshots_and_initial_events(self) -> None:
        store = InMemoryRunStore()

        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )

        self.assertEqual(run.ticket_id, 7001)
        self.assertEqual(run.status, RunStatus.AWAITING_CONNECTION_APPROVAL)
        self.assertEqual(run.ticket_snapshot["title"], "API down")
        self.assertEqual(run.customer_system_snapshot["system"]["ip"], "10.0.0.5")

        events = store.list_events(run.id)
        self.assertEqual([event.event_type for event in events], ["run.created", "connection.approval_requested"])
        self.assertEqual(events[0].actor, "technician")
        self.assertEqual(events[1].actor, "system")
        self.assertEqual(events[1].payload["target"]["username"], "azureuser")

        after_first_event = store.list_events(run.id, after_id=events[0].id)
        self.assertEqual([event.event_type for event in after_first_event], ["connection.approval_requested"])

    def test_run_store_satisfies_named_audit_store_boundary(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )

        self.assertIsInstance(store, AuditStore)
        store.append_event(
            run.id,
            actor="test",
            event_type="audit.boundary_checked",
            summary="Audit event appended through the named boundary.",
            payload={"source": "unit_test"},
        )

        self.assertEqual(store.list_events(run.id)[-1].event_type, "audit.boundary_checked")

    def test_approve_connection_transitions_run_and_appends_audit_events(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )

        approved = store.approve_connection(run.id, approved_by="Ada Lovelace")

        self.assertEqual(approved.status, RunStatus.INVESTIGATING)
        events = store.list_events(run.id)
        self.assertEqual(
            [event.event_type for event in events],
            ["run.created", "connection.approval_requested", "connection.approved", "agent.plan_requested"],
        )
        self.assertEqual(events[2].approval_status, "approved")
        self.assertEqual(events[2].payload["approved_by"], "Ada Lovelace")

    def test_connection_approval_cannot_be_recorded_twice(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        with self.assertRaises(RunTransitionError):
            store.approve_connection(run.id, approved_by="Ada Lovelace")

    def test_sqlalchemy_models_cover_postgres_tables_jsonb_indexes_and_constraints(self) -> None:
        from sqlalchemy.dialects.postgresql import JSONB

        from app.persistence_models import Base

        metadata = Base.metadata
        expected_tables = {
            "runs",
            "run_events",
            "proposed_steps",
            "command_executions",
            "command_output_chunks",
            "inspected_sources",
            "validation_results",
            "validation_expectations",
            "backup_records",
            "activity_drafts",
            "integration_requests",
            "redaction_events",
            "outbox_events",
            "technician_cache",
            "tickets_cache",
            "customer_system_cache",
        }
        self.assertTrue(expected_tables.issubset(metadata.tables.keys()))

        jsonb_columns = {
            ("runs", "ticket_snapshot"),
            ("runs", "customer_system_snapshot"),
            ("runs", "current_hypotheses"),
            ("runs", "pending_step"),
            ("runs", "activity_draft"),
            ("run_events", "payload"),
            ("proposed_steps", "safety_notes"),
            ("outbox_events", "payload"),
            ("activity_drafts", "draft"),
            ("integration_requests", "activity_payload"),
            ("technician_cache", "technician_snapshot"),
            ("tickets_cache", "ticket_snapshot"),
            ("customer_system_cache", "customer_system_snapshot"),
        }
        for table_name, column_name in jsonb_columns:
            self.assertIsInstance(metadata.tables[table_name].c[column_name].type, JSONB)

        constraint_names = {
            constraint.name
            for table in metadata.tables.values()
            for constraint in table.constraints
            if constraint.name
        }
        for constraint_name in [
            "runs_status_check",
            "proposed_steps_source_check",
            "proposed_steps_status_check",
            "command_executions_status_check",
            "command_output_chunks_execution_run_fk",
            "run_events_terminal_payload_check",
            "backup_records_not_applicable_shape_check",
        ]:
            self.assertIn(constraint_name, constraint_names)

        index_names = {index.name for table in metadata.tables.values() for index in table.indexes}
        for index_name in [
            "runs_ticket_started_idx",
            "run_events_run_id_idx",
            "proposed_steps_one_active_step_per_run_idx",
            "outbox_events_pending_idx",
            "tickets_cache_status_idx",
        ]:
            self.assertIn(index_name, index_names)

    def test_sqlalchemy_outbox_claim_query_preserves_skip_locked(self) -> None:
        from sqlalchemy.dialects import postgresql

        from app.persistence_models import claimable_outbox_events_query

        compiled = str(claimable_outbox_events_query().compile(dialect=postgresql.dialect()))

        self.assertIn("FOR UPDATE SKIP LOCKED", compiled)
        self.assertIn("outbox_events.status IN", compiled)

    def test_alembic_scaffold_targets_persistence_models_and_initial_migration_keeps_guards(self) -> None:
        backend_root = REPO_ROOT / "backend"
        alembic_env = (backend_root / "alembic" / "env.py").read_text()
        initial_migration = next((backend_root / "alembic" / "versions").glob("*initial_run_store.py")).read_text()

        self.assertIn("target_metadata = Base.metadata", alembic_env)
        self.assertIn("prevent_run_events_mutation", initial_migration)
        self.assertIn("proposed_steps_approval_guard", initial_migration)
        self.assertIn("FOR UPDATE SKIP LOCKED", initial_migration)

    def test_final_spec_lists_validation_ledgers_and_requires_validation_without_override(self) -> None:
        final_spec = (REPO_ROOT / ".agents" / "plans" / "techbold-final-spec.md").read_text()

        self.assertIn("- `validation_results`", final_spec)
        self.assertIn("- `validation_expectations`", final_spec)
        self.assertIn("activity submission requires a completed validation suite", final_spec)
        self.assertNotIn("explicit technician override", final_spec)

    def test_postgres_run_store_has_backup_requirement_guard_used_by_approval_path(self) -> None:
        self.assertTrue(hasattr(PostgresRunStore, "_assert_backup_requirement_satisfied"))

    def test_manual_step_is_classified_and_waits_for_approval(self) -> None:
        store = InMemoryRunStore()
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
            purpose="Check failed services before changing anything.",
        )

        self.assertEqual(step.status, StepStatus.PROPOSED)
        self.assertEqual(step.source, "manual")
        self.assertEqual(step.safety_verdict, "allowed")
        self.assertEqual(step.risk_class, "READ_ONLY")
        self.assertEqual(store.get_run(run.id).status, RunStatus.AWAITING_STEP_APPROVAL)
        self.assertEqual(store.get_run(run.id).pending_step["id"], step.id)
        self.assertEqual(
            [event.event_type for event in store.list_events(run.id)][-3:],
            ["manual_step.entered", "step.proposed", "step.safety_classified"],
        )
        classification = store.list_events(run.id)[-1]
        self.assertEqual(classification.payload["verdict"], "allowed")
        self.assertEqual(classification.payload["risk_class"], "READ_ONLY")
        self.assertEqual(classification.payload["summary"], "Read-only diagnostic command allowed.")

    def test_blocked_manual_step_is_logged_but_not_pending(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        step = store.create_manual_step(
            run.id,
            command="cat /etc/shadow",
            entered_by="Ada Lovelace",
            purpose="Inspect credentials",
        )

        self.assertEqual(step.status, StepStatus.BLOCKED)
        self.assertEqual(step.safety_verdict, "blocked")
        self.assertIsNone(store.get_run(run.id).pending_step)
        self.assertEqual(store.get_run(run.id).status, RunStatus.INVESTIGATING)
        classification = store.list_events(run.id)[-1]
        self.assertEqual(classification.approval_status, "blocked")
        self.assertEqual(classification.payload["verdict"], "blocked")
        self.assertEqual(classification.payload["risk_class"], "BLOCKED")
        self.assertEqual(classification.payload["blocked_reason"], "Reading likely secret material is blocked.")

    def test_approving_step_creates_execution_outbox_event(self) -> None:
        store = InMemoryRunStore()
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

        updated = store.approve_step(run.id, step.id, approved_by="Ada Lovelace")

        self.assertEqual(updated.status, RunStatus.EXECUTING)
        self.assertIsNone(updated.pending_step)
        approved_step = store.get_step(run.id, step.id)
        self.assertEqual(approved_step.status, StepStatus.APPROVED)
        self.assertEqual(approved_step.approved_command, "systemctl --failed")
        outbox_item = store.claim_next_outbox_event()
        self.assertIsNotNone(outbox_item)
        self.assertEqual(outbox_item.event_type, "command.execution_requested")
        self.assertEqual(outbox_item.payload["step_id"], step.id)

    def test_stale_processing_outbox_event_is_recovered_for_reclaim(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        claimed = store.claim_next_outbox_event()
        self.assertIsNotNone(claimed)
        store._outbox[claimed.id] = claimed.model_copy(  # noqa: SLF001
            update={"claimed_at": datetime.now(UTC) - timedelta(minutes=10)}
        )

        recovered_count = store.recover_stale_outbox_events(stale_after_s=60)
        reclaimed = store.claim_next_outbox_event()

        self.assertEqual(recovered_count, 1)
        self.assertIsNotNone(reclaimed)
        self.assertEqual(reclaimed.id, claimed.id)
        self.assertEqual(reclaimed.attempts, 2)

    def test_failed_outbox_event_retries_after_backoff(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        claimed = store.claim_next_outbox_event()
        self.assertIsNotNone(claimed)

        store.fail_outbox_event(claimed.id, error="temporary failure")
        failed = store._outbox[claimed.id]  # noqa: SLF001

        self.assertEqual(failed.status, OutboxStatus.FAILED)
        self.assertIsNone(store.claim_next_outbox_event())

        store._outbox[claimed.id] = failed.model_copy(  # noqa: SLF001
            update={"available_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        retried = store.claim_next_outbox_event()

        self.assertIsNotNone(retried)
        self.assertEqual(retried.id, claimed.id)
        self.assertEqual(retried.attempts, 2)

    def test_list_outbox_events_returns_failed_and_dead_letter_items_for_run(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        claimed = store.claim_next_outbox_event()
        self.assertIsNotNone(claimed)

        store.fail_outbox_event(claimed.id, error="temporary failure")
        failed_events = store.list_outbox_events(run.id, statuses={OutboxStatus.FAILED, OutboxStatus.DEAD_LETTER})

        self.assertEqual(len(failed_events), 1)
        self.assertEqual(failed_events[0].status, OutboxStatus.FAILED)
        self.assertEqual(failed_events[0].error, "temporary failure")

        store._outbox[claimed.id] = failed_events[0].model_copy(  # noqa: SLF001
            update={"available_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        second_claim = store.claim_next_outbox_event()
        self.assertIsNotNone(second_claim)
        store.fail_outbox_event(second_claim.id, error="still failing")
        second_failed = store._outbox[claimed.id]  # noqa: SLF001
        store._outbox[claimed.id] = second_failed.model_copy(  # noqa: SLF001
            update={"available_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        third_claim = store.claim_next_outbox_event()
        self.assertIsNotNone(third_claim)
        store.fail_outbox_event(third_claim.id, error="permanent failure")

        visible_events = store.list_outbox_events(run.id, statuses={OutboxStatus.FAILED, OutboxStatus.DEAD_LETTER})
        self.assertEqual(len(visible_events), 1)
        self.assertEqual(visible_events[0].status, OutboxStatus.DEAD_LETTER)
        self.assertEqual(visible_events[0].attempts, 3)
        self.assertEqual(visible_events[0].error, "permanent failure")

    def test_edit_and_approve_reclassifies_before_queueing(self) -> None:
        store = InMemoryRunStore()
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
            purpose="Check nginx.",
        )

        updated = store.edit_and_approve_step(
            run.id,
            step.id,
            command="journalctl -u nginx --no-pager -n 80",
            approved_by="Ada Lovelace",
        )

        self.assertEqual(updated.status, RunStatus.EXECUTING)
        approved_step = store.get_step(run.id, step.id)
        self.assertEqual(approved_step.approved_command, "journalctl -u nginx --no-pager -n 80")
        self.assertEqual(approved_step.risk_class, "READ_ONLY")
        self.assertEqual(store.list_events(run.id)[-2].event_type, "step.edited_and_approved")

    def test_blocked_edit_cannot_be_approved(self) -> None:
        store = InMemoryRunStore()
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
            purpose="Check nginx.",
        )

        with self.assertRaises(RunTransitionError):
            store.edit_and_approve_step(
                run.id,
                step.id,
                command="rm -rf /var/lib/postgresql",
                approved_by="Ada Lovelace",
            )

    def test_unapproved_blocked_and_aborted_steps_never_start_command_execution(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        proposed = store.create_manual_step(
            run.id,
            command="systemctl status nginx --no-pager",
            entered_by="Ada Lovelace",
            purpose="Check nginx before approval.",
        )
        with self.assertRaisesRegex(RunTransitionError, "Only approved"):
            store.start_command_execution(run.id, proposed.id)

        store.reject_step(run.id, proposed.id, rejected_by="Ada Lovelace", reason="Need a different diagnostic.")
        blocked = store.create_manual_step(
            run.id,
            command="cat /etc/shadow",
            entered_by="Ada Lovelace",
            purpose="Unsafe credential read.",
        )
        with self.assertRaisesRegex(RunTransitionError, "Only approved"):
            store.start_command_execution(run.id, blocked.id)

        approved = store.create_manual_step(
            run.id,
            command="systemctl --failed",
            entered_by="Ada Lovelace",
            purpose="Check failed units.",
        )
        store.approve_step(run.id, approved.id, approved_by="Ada Lovelace")
        store.abort_run(run.id, aborted_by="Ada Lovelace", reason="Stop before worker runs.")
        with self.assertRaisesRegex(RunTransitionError, "Aborted runs"):
            store.start_command_execution(run.id, approved.id)

        self.assertEqual(store.list_command_executions(run.id), [])
        self.assertEqual(store.get_run(run.id).status, RunStatus.ABORTED)

    def test_reject_retry_and_abort_are_audited_state_transitions(self) -> None:
        store = InMemoryRunStore()
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
            purpose="Check nginx.",
        )

        rejected = store.reject_step(run.id, step.id, rejected_by="Ada Lovelace", reason="Need broader context first.")

        self.assertEqual(rejected.status, RunStatus.INVESTIGATING)
        self.assertEqual(store.get_step(run.id, step.id).status, StepStatus.REJECTED)
        retrying = store.retry_run(run.id, requested_by="Ada Lovelace")
        self.assertEqual(retrying.status, RunStatus.INVESTIGATING)
        self.assertEqual(store.claim_next_outbox_event().event_type, "agent.plan_requested")

        aborted = store.abort_run(run.id, aborted_by="Ada Lovelace", reason="Demo stop.")

        self.assertEqual(aborted.status, RunStatus.ABORTED)
        self.assertEqual(store.list_events(run.id)[-1].event_type, "run.aborted")
        with self.assertRaises(RunTransitionError):
            store.retry_run(run.id, requested_by="Ada Lovelace")

    def test_retry_supersedes_dead_lettered_planner_events(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        planner_event = store.claim_next_outbox_event()
        self.assertIsNotNone(planner_event)
        store._outbox[planner_event.id] = planner_event.model_copy(update={"attempts": 3})
        store.fail_outbox_event(planner_event.id, error="Planner diagnostics exhausted.")

        store.retry_run(run.id, requested_by="Ada Lovelace")

        attention_events = store.list_outbox_events(
            run.id,
            statuses={OutboxStatus.FAILED, OutboxStatus.DEAD_LETTER},
        )
        self.assertEqual(attention_events, [])
        events = store.list_outbox_events(run.id)
        self.assertEqual(events[0].status, OutboxStatus.COMPLETED)
        self.assertEqual(events[-1].status, OutboxStatus.PENDING)
        self.assertEqual(events[-1].event_type, "agent.plan_requested")

    def test_command_chunks_are_redacted_capped_and_finalize_execution(self) -> None:
        store = InMemoryRunStore(command_output_limit_bytes=32)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="printf secret",
            entered_by="Ada Lovelace",
            purpose="Check redaction.",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")

        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(
            run.id,
            execution.id,
            stream="stdout",
            content="TOKEN=abc123\nthis line continues past the cap",
            redacted=True,
        )
        completed = store.complete_command_execution(
            run.id,
            execution.id,
            exit_code=0,
            duration_ms=25,
            error=None,
            timed_out=False,
        )

        self.assertEqual(completed.status, CommandExecutionStatus.COMPLETED)
        chunks = store.list_command_output_chunks(run.id)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].content, "[REDACTED_SECRET]\nthis line cont")
        self.assertTrue(completed.output_truncated)
        self.assertIn("terminal.output_truncated", [event.event_type for event in store.list_events(run.id)])

    def test_redaction_events_are_recorded_for_terminal_output_and_evidence(self) -> None:
        store = InMemoryRunStore(command_output_limit_bytes=200)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="cat /var/log/nginx/error.log",
            entered_by="Ada Lovelace",
            purpose="Check redaction ledger.",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)

        store.append_command_output_chunk(
            run.id,
            execution.id,
            stream="stdout",
            content="TOKEN=sk-demo-secret\nnginx failed\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, execution.id, exit_code=1, duration_ms=20, error=None, timed_out=False)

        redaction_events = store.list_redaction_events(run.id)
        surfaces = [(event.surface, event.field_name) for event in redaction_events]

        self.assertIn(("stdout", "stdout"), surfaces)
        self.assertIn(("evidence", "sanitized_excerpt"), surfaces)
        self.assertTrue(all(event.command_execution_id == execution.id for event in redaction_events))
        self.assertNotIn("sk-demo-secret", store.list_command_executions(run.id)[0].sanitized_stdout)

    def test_activity_draft_redaction_records_event_and_persists_redacted_content(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="systemctl is-active nginx",
            entered_by="Ada Lovelace",
            purpose="Validate nginx.",
            phase="validation",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(run.id, execution.id, stream="stdout", content="active\n", redacted=False)
        store.complete_command_execution(run.id, execution.id, exit_code=0, duration_ms=15, error=None, timed_out=False)

        draft = ActivityDraft(
            ticket_id=run.ticket_id,
            start_datetime=run.started_at.isoformat(),
            end_datetime=run.started_at.isoformat(),
            description="Investigated service.",
            summary="PASSWORD=hunter2",
            root_cause="Service had a stale config.",
            actions_taken="Restarted nginx.",
            commands_summary="systemctl is-active nginx",
            validation_result="active",
        )

        saved = store.save_activity_draft(run.id, draft)
        updated_run = store.get_run(run.id)
        activity_redactions = [event for event in store.list_redaction_events(run.id) if event.surface == "activity"]

        self.assertEqual(saved.summary, "[REDACTED_SECRET]")
        self.assertEqual(updated_run.activity_draft["summary"], "[REDACTED_SECRET]")
        self.assertEqual(len(activity_redactions), 1)
        self.assertEqual(activity_redactions[0].field_name, "summary")

    def test_stale_command_outbox_cannot_start_same_step_twice(self) -> None:
        store = InMemoryRunStore()
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

        first_execution = store.start_command_execution(run.id, step.id)

        with self.assertRaises(RunTransitionError):
            store.start_command_execution(run.id, step.id)

        self.assertEqual([execution.id for execution in store.list_command_executions(run.id)], [first_execution.id])

    def test_completed_inspection_command_records_evidence_source(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="journalctl -u nginx --no-pager -n 80",
            entered_by="Ada Lovelace",
            purpose="Inspect nginx errors.",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(
            run.id,
            execution.id,
            stream="stderr",
            content="nginx: bind() to 0.0.0.0:80 failed (98: Address already in use)\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, execution.id, exit_code=0, duration_ms=30, error=None, timed_out=False)

        sources = store.list_inspected_sources(run.id)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].source_type, "journal")
        self.assertEqual(sources[0].source_name, "nginx")
        self.assertEqual(sources[0].command_execution_id, execution.id)
        self.assertEqual(sources[0].supports, "root_cause")
        self.assertIn("bind", sources[0].finding)
        self.assertIn("evidence.source_detected", [event.event_type for event in store.list_events(run.id)])

    def test_medium_risk_persistent_change_requires_backup_or_not_applicable_record(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="sed -i 's/listen 8080/listen 80/' /etc/nginx/sites-enabled/default",
            entered_by="Ada Lovelace",
            purpose="Apply minimal nginx listen-port fix.",
            phase="fix",
        )

        with self.assertRaises(RunTransitionError):
            store.approve_step(run.id, step.id, approved_by="Ada Lovelace")

        store.reject_step(run.id, step.id, rejected_by="Ada Lovelace", reason="Need evidence first.")
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
            content="nginx: bind() to 0.0.0.0:8080 failed (98: Address already in use)\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, diagnostic_execution.id, exit_code=0, duration_ms=30, error=None, timed_out=False)
        step = store.create_manual_step(
            run.id,
            command="sed -i 's/listen 8080/listen 80/' /etc/nginx/sites-enabled/default",
            entered_by="Ada Lovelace",
            purpose="Apply minimal nginx listen-port fix.",
            phase="fix",
        )
        store.record_backup_not_applicable(
            run.id,
            source_path="/etc/nginx/sites-enabled/default",
            reason="Demo fixture explicitly marks rollback not applicable.",
            recorded_by="Ada Lovelace",
        )
        approved = store.approve_step(run.id, step.id, approved_by="Ada Lovelace")

        self.assertEqual(approved.status, RunStatus.FIXING)
        records = store.list_backup_records(run.id)
        not_applicable = [record for record in records if record.backup_type == "not_applicable"][0]
        self.assertEqual(not_applicable.source_path, "/etc/nginx/sites-enabled/default")
        self.assertIn("backup.not_applicable", [event.event_type for event in store.list_events(run.id)])

    def test_persistent_fix_proposal_records_backup_plan_before_approval(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        step = store.create_manual_step(
            run.id,
            command="sed -i 's/listen 8080/listen 80/' /etc/nginx/sites-enabled/default",
            entered_by="Ada Lovelace",
            purpose="Apply minimal nginx listen-port fix.",
            phase="fix",
        )

        records = store.list_backup_records(run.id)
        event_types = [event.event_type for event in store.list_events(run.id)]

        self.assertEqual(step.risk_class, "MEDIUM_RISK")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].source_path, "/etc/nginx/sites-enabled/default")
        self.assertEqual(records[0].backup_type, "file_copy")
        self.assertEqual(
            records[0].backup_path,
            f"/var/backups/techbold-autopilot/7001/{run.id}/default.prechange",
        )
        self.assertEqual(
            records[0].restore_command,
            f"cp -a /var/backups/techbold-autopilot/7001/{run.id}/default.prechange /etc/nginx/sites-enabled/default",
        )
        self.assertTrue(records[0].backup_required)
        self.assertFalse(records[0].backup_created)
        self.assertIn("backup.planned", event_types)
        self.assertEqual(store.get_run(run.id).status, RunStatus.AWAITING_STEP_APPROVAL)

    def test_backup_command_creates_rollback_record(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="cp -a /etc/nginx/sites-enabled/default /var/backups/techbold-autopilot/7001/1/default.20260606",
            entered_by="Ada Lovelace",
            purpose="Create targeted config backup.",
            phase="fix",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)
        store.complete_command_execution(run.id, execution.id, exit_code=0, duration_ms=18, error=None, timed_out=False)

        records = store.list_backup_records(run.id)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].backup_type, "file_copy")
        self.assertEqual(records[0].source_path, "/etc/nginx/sites-enabled/default")
        self.assertIn("cp -a", records[0].restore_command)
        self.assertIn("backup.created", [event.event_type for event in store.list_events(run.id)])

    def test_metadata_snapshot_command_records_pre_change_file_metadata(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="stat -c 'owner=%U group=%G mode=%a size=%s mtime=%Y checksum=abc123' /srv/app/uploads",
            entered_by="Ada Lovelace",
            purpose="Record ownership and mode before changing uploaded asset directory permissions.",
            phase="fix",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(
            run.id,
            execution.id,
            stream="stdout",
            content="owner=www-data group=www-data mode=755 size=4096 mtime=1717675200 checksum=abc123\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, execution.id, exit_code=0, duration_ms=12, error=None, timed_out=False)

        records = store.list_backup_records(run.id)

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
        self.assertIn("backup.created", [event.event_type for event in store.list_events(run.id)])

    def test_service_state_snapshot_command_records_pre_change_systemd_state(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="systemctl show -p ActiveState -p UnitFileState nginx",
            entered_by="Ada Lovelace",
            purpose="Record nginx service state before restart.",
            phase="fix",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(
            run.id,
            execution.id,
            stream="stdout",
            content="ActiveState=active\nUnitFileState=enabled\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, execution.id, exit_code=0, duration_ms=11, error=None, timed_out=False)

        records = store.list_backup_records(run.id)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].backup_type, "service_state")
        self.assertEqual(records[0].source_path, "nginx")
        self.assertIn("active", records[0].reason)
        self.assertIn("enabled", records[0].reason)
        self.assertEqual(records[0].restore_command, "systemctl enable --now nginx")
        self.assertTrue(records[0].stored_content)
        self.assertTrue(records[0].persistent_across_reboot)
        self.assertIn("backup.created", [event.event_type for event in store.list_events(run.id)])

    def test_service_restart_proposal_records_service_state_backup_requirement(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        step = store.create_manual_step(
            run.id,
            command="systemctl restart nginx",
            entered_by="Ada Lovelace",
            purpose="Restart only the affected nginx service.",
            phase="fix",
        )

        records = store.list_backup_records(run.id)

        self.assertEqual(step.risk_class, "LOW_RISK")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].backup_type, "service_state")
        self.assertEqual(records[0].source_path, "nginx")
        self.assertTrue(records[0].backup_required)
        self.assertFalse(records[0].backup_created)
        self.assertIn("backup.planned", [event.event_type for event in store.list_events(run.id)])

    def test_approving_service_enable_uses_prior_status_as_automatic_backup(self) -> None:
        store = InMemoryRunStore()
        system = customer_system_snapshot()
        system["system"]["notes"] = ""
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=system,
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        status_step = store.create_manual_step(
            run.id,
            command="systemctl status customer-status --no-pager",
            entered_by="Ada Lovelace",
            purpose="Inspect customer-status before changing it.",
            phase="diagnostic",
        )
        store.approve_step(run.id, status_step.id, approved_by="Ada Lovelace")
        status_execution = store.start_command_execution(run.id, status_step.id)
        store.append_command_output_chunk(
            run.id,
            status_execution.id,
            stream="stdout",
            content=(
                "Loaded: loaded (/etc/systemd/system/customer-status.service; disabled; preset: enabled)\n"
                "Active: inactive (dead)\n"
            ),
            redacted=False,
        )
        store.complete_command_execution(
            run.id,
            status_execution.id,
            exit_code=3,
            duration_ms=12,
            error=None,
            timed_out=False,
        )
        fix_step = store.create_manual_step(
            run.id,
            command="sudo -n systemctl enable --now customer-status",
            entered_by="Ada Lovelace",
            purpose="Enable and start the discovered customer status service.",
            phase="fix",
        )

        approved = store.approve_step(run.id, fix_step.id, approved_by="Ada Lovelace")

        self.assertEqual(approved.status, RunStatus.FIXING)
        matching_backups = [
            record
            for record in store.list_backup_records(run.id)
            if record.source_path == "customer-status" and record.backup_type == "service_state"
        ]
        self.assertTrue(any(record.backup_created for record in matching_backups))
        self.assertTrue(
            any(record.restore_command == "systemctl disable --now customer-status" for record in matching_backups)
        )

    def test_completed_fix_moves_run_to_validation_and_requests_validation_plan(self) -> None:
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
            content="nginx failed with connection refused errors\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, diagnostic_execution.id, exit_code=0, duration_ms=20, error=None, timed_out=False)
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

        approved = store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)
        store.complete_command_execution(run.id, execution.id, exit_code=0, duration_ms=44, error=None, timed_out=False)

        self.assertEqual(approved.status, RunStatus.FIXING)
        self.assertEqual(store.get_run(run.id).status, RunStatus.VALIDATING)
        event_types = [event.event_type for event in store.list_events(run.id)]
        self.assertIn("validation.required", event_types)
        self.assertIn("agent.plan_requested", event_types)

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

        unrelated_fix = store.create_manual_step(
            run.id,
            command="systemctl restart mysql",
            entered_by="Ada Lovelace",
            purpose="Restart mysql even though the evidence is for nginx.",
            phase="fix",
        )

        with self.assertRaisesRegex(RunTransitionError, "unrelated service"):
            store.approve_step(run.id, unrelated_fix.id, approved_by="Ada Lovelace")

        store.reject_step(run.id, unrelated_fix.id, rejected_by="Ada Lovelace", reason="Wrong service.")
        related_fix = store.create_manual_step(
            run.id,
            command="systemctl restart nginx",
            entered_by="Ada Lovelace",
            purpose="Restart only nginx based on recorded root-cause evidence.",
            phase="fix",
        )
        store.record_backup_not_applicable(
            run.id,
            source_path="nginx",
            reason="Restart does not alter persistent service enablement.",
            recorded_by="Ada Lovelace",
        )
        approved = store.approve_step(run.id, related_fix.id, approved_by="Ada Lovelace")

        self.assertEqual(approved.status, RunStatus.FIXING)

    def test_validation_suite_required_before_activity_after_fix(self) -> None:
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

        fix_step = store.create_manual_step(
            run.id,
            command="systemctl restart nginx",
            entered_by="Ada Lovelace",
            purpose="Restart only nginx based on root-cause evidence.",
            phase="fix",
        )
        store.record_backup_not_applicable(
            run.id,
            source_path="nginx",
            reason="Restart does not alter persistent service enablement.",
            recorded_by="Ada Lovelace",
        )
        store.approve_step(run.id, fix_step.id, approved_by="Ada Lovelace")
        fix_execution = store.start_command_execution(run.id, fix_step.id)
        store.complete_command_execution(run.id, fix_execution.id, exit_code=0, duration_ms=44, error=None, timed_out=False)

        expectations = store.list_validation_expectations(run.id)
        self.assertEqual(
            [expectation.check_type for expectation in expectations],
            ["service_health", "customer_benefit", "logs_clean", "persistence"],
        )
        self.assertTrue(all(expectation.required for expectation in expectations))
        self.assertTrue(all(expectation.expected_result for expectation in expectations))
        self.assertTrue(all(expectation.relation_to_customer_symptom for expectation in expectations))

        service_step = store.create_manual_step(
            run.id,
            command="systemctl is-active nginx",
            entered_by="Ada Lovelace",
            purpose="Validate nginx service is active.",
            phase="validation",
        )
        store.approve_step(run.id, service_step.id, approved_by="Ada Lovelace")
        service_execution = store.start_command_execution(run.id, service_step.id)
        store.append_command_output_chunk(run.id, service_execution.id, stream="stdout", content="active\n", redacted=False)
        store.complete_command_execution(run.id, service_execution.id, exit_code=0, duration_ms=12, error=None, timed_out=False)

        self.assertEqual(store.get_run(run.id).status, RunStatus.VALIDATING)
        with self.assertRaisesRegex(RunTransitionError, "validation suite"):
            store.create_activity_draft(run.id)

        validation_commands = [
            ("curl -I http://localhost", "HTTP/1.1 200 OK\n"),
            ("journalctl -u nginx --since -5min --no-pager -n 50", "No recent bind errors\n"),
            ("systemctl restart nginx", ""),
        ]
        for command, output in validation_commands:
            validation_step = store.create_manual_step(
                run.id,
                command=command,
                entered_by="Ada Lovelace",
                purpose=f"Run required validation: {command}",
                phase="validation",
            )
            if command == "systemctl restart nginx":
                store.record_backup_not_applicable(
                    run.id,
                    source_path="nginx",
                    reason="Technician-approved persistence restart does not alter persistent service enablement.",
                    recorded_by="Ada Lovelace",
                )
            store.approve_step(run.id, validation_step.id, approved_by="Ada Lovelace")
            validation_execution = store.start_command_execution(run.id, validation_step.id)
            if output:
                store.append_command_output_chunk(run.id, validation_execution.id, stream="stdout", content=output, redacted=False)
            store.complete_command_execution(run.id, validation_execution.id, exit_code=0, duration_ms=12, error=None, timed_out=False)

        updated_expectations = store.list_validation_expectations(run.id)
        self.assertTrue(all(expectation.status == "passed" for expectation in updated_expectations))
        self.assertNotIn("reboot", [expectation.check_type for expectation in updated_expectations])
        self.assertEqual(store.get_run(run.id).status, RunStatus.READY_FOR_ACTIVITY)
        self.assertIn("validation.suite_passed", [event.event_type for event in store.list_events(run.id)])
        self.assertIn(
            "agent.activity_draft_requested",
            [event.event_type for event in store.list_outbox_events(run.id, statuses=set(OutboxStatus))],
        )
        self.assertIn("HTTP/1.1 200 OK", store.create_activity_draft(run.id).validation_result)

    def test_fix_validation_uses_customer_endpoint_from_ticket_and_queues_draft(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot={
                **ticket_snapshot(),
                "description": (
                    "The status API is unavailable at http://localhost:8080/health. "
                    "Public validation Run: sudo /opt/hackathon/public-test.sh"
                ),
            },
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        diagnostic_step = store.create_manual_step(
            run.id,
            command="systemctl status customer-status --no-pager",
            entered_by="Ada Lovelace",
            purpose="Inspect customer-status.",
        )
        store.approve_step(run.id, diagnostic_step.id, approved_by="Ada Lovelace")
        diagnostic_execution = store.start_command_execution(run.id, diagnostic_step.id)
        store.append_command_output_chunk(
            run.id,
            diagnostic_execution.id,
            stream="stdout",
            content="Loaded: disabled\nActive: inactive (dead)\n",
            redacted=False,
        )
        store.complete_command_execution(
            run.id,
            diagnostic_execution.id,
            exit_code=3,
            duration_ms=10,
            error=None,
            timed_out=False,
        )
        fix_step = store.create_manual_step(
            run.id,
            command="sudo -n systemctl enable --now customer-status",
            entered_by="Ada Lovelace",
            purpose="Enable and start customer-status.",
            phase="fix",
        )
        store.approve_step(run.id, fix_step.id, approved_by="Ada Lovelace")
        fix_execution = store.start_command_execution(run.id, fix_step.id)
        store.complete_command_execution(
            run.id,
            fix_execution.id,
            exit_code=0,
            duration_ms=20,
            error=None,
            timed_out=False,
        )

        expectations = store.list_validation_expectations(run.id)

        customer_check = next(item for item in expectations if item.check_type == "customer_benefit")
        public_check = next(item for item in expectations if item.check_type == "public_validation")
        self.assertEqual(customer_check.target, "http://localhost:8080/health")
        self.assertEqual(public_check.target, "sudo /opt/hackathon/public-test.sh")

    def test_environment_port_fix_restarts_customer_service_before_endpoint_validation(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot={
                **ticket_snapshot(),
                "description": "The status API is unavailable at http://localhost:8080/health.",
            },
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        diagnostic_step = store.create_manual_step(
            run.id,
            command="grep -E '^PORT=[0-9]+$' /etc/customer-status.env",
            entered_by="Ada Lovelace",
            purpose="Inspect the customer-status listen port.",
        )
        store.approve_step(run.id, diagnostic_step.id, approved_by="Ada Lovelace")
        diagnostic_execution = store.start_command_execution(run.id, diagnostic_step.id)
        store.append_command_output_chunk(
            run.id,
            diagnostic_execution.id,
            stream="stdout",
            content="PORT=8008\n",
            redacted=False,
        )
        store.complete_command_execution(
            run.id,
            diagnostic_execution.id,
            exit_code=0,
            duration_ms=10,
            error=None,
            timed_out=False,
        )
        fix_step = store.create_manual_step(
            run.id,
            command=(
                "sudo -n sed -i.techbold-prechange "
                "'s/^PORT=[0-9]\\+$/PORT=8080/' /etc/customer-status.env"
            ),
            entered_by="Ada Lovelace",
            purpose="Correct the persistent customer-status listen port.",
            phase="fix",
        )
        store.approve_step(run.id, fix_step.id, approved_by="Ada Lovelace")
        fix_execution = store.start_command_execution(run.id, fix_step.id)
        store.complete_command_execution(
            run.id,
            fix_execution.id,
            exit_code=0,
            duration_ms=20,
            error=None,
            timed_out=False,
        )

        expectations = store.list_validation_expectations(run.id)

        self.assertEqual(
            [expectation.check_type for expectation in expectations],
            ["service_health", "persistence", "customer_benefit", "logs_clean"],
        )
        self.assertTrue(all(expectation.target != "nginx" for expectation in expectations))
        self.assertEqual(expectations[0].target, "customer-status")
        self.assertEqual(expectations[1].target, "customer-status")
        backups = store.list_backup_records(run.id)
        self.assertTrue(
            any(
                record.source_path == "/etc/customer-status.env"
                and record.backup_path == "/etc/customer-status.env.techbold-prechange"
                and record.backup_created
                for record in backups
            )
        )

    def test_failed_validation_requires_new_fix_loop_before_activity(self) -> None:
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
        integration_diagnostic_outbox = store.claim_next_outbox_event()
        self.assertIsNotNone(integration_diagnostic_outbox)
        self.assertEqual(integration_diagnostic_outbox.event_type, "command.execution_requested")
        store.complete_outbox_event(integration_diagnostic_outbox.id)
        diagnostic_execution = store.start_command_execution(run.id, diagnostic_step.id)
        store.append_command_output_chunk(
            run.id,
            diagnostic_execution.id,
            stream="stderr",
            content="nginx failed with connection refused errors\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, diagnostic_execution.id, exit_code=0, duration_ms=30, error=None, timed_out=False)

        fix_step = store.create_manual_step(
            run.id,
            command="systemctl restart nginx",
            entered_by="Ada Lovelace",
            purpose="Restart nginx based on root-cause evidence.",
            phase="fix",
        )
        store.record_backup_not_applicable(
            run.id,
            source_path="nginx",
            reason="Restart does not alter persistent service enablement.",
            recorded_by="Ada Lovelace",
        )
        store.approve_step(run.id, fix_step.id, approved_by="Ada Lovelace")
        fix_execution = store.start_command_execution(run.id, fix_step.id)
        store.complete_command_execution(run.id, fix_execution.id, exit_code=0, duration_ms=44, error=None, timed_out=False)

        customer_step = store.create_manual_step(
            run.id,
            command="curl -I http://localhost",
            entered_by="Ada Lovelace",
            purpose="Validate customer-facing endpoint.",
            phase="validation",
        )
        store.approve_step(run.id, customer_step.id, approved_by="Ada Lovelace")
        customer_execution = store.start_command_execution(run.id, customer_step.id)
        store.append_command_output_chunk(run.id, customer_execution.id, stream="stderr", content="Connection refused\n", redacted=False)
        store.complete_command_execution(
            run.id,
            customer_execution.id,
            exit_code=7,
            duration_ms=18,
            error="Connection refused",
            timed_out=False,
        )

        self.assertEqual(store.get_run(run.id).status, RunStatus.VALIDATING)
        self.assertTrue(any(expectation.status == "failed" for expectation in store.list_validation_expectations(run.id)))
        with self.assertRaisesRegex(RunTransitionError, "new fix"):
            store.create_activity_draft(run.id)

    def test_mocked_incident_fixture_covers_diagnosis_backup_fix_and_validation(self) -> None:
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
            purpose="Inspect nginx journal for the customer-facing outage.",
        )
        store.approve_step(run.id, diagnostic_step.id, approved_by="Ada Lovelace")
        diagnostic_execution = store.start_command_execution(run.id, diagnostic_step.id)
        store.append_command_output_chunk(
            run.id,
            diagnostic_execution.id,
            stream="stderr",
            content="nginx: bind() to 0.0.0.0:8080 failed (98: Address already in use)\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, diagnostic_execution.id, exit_code=0, duration_ms=30, error=None, timed_out=False)
        root_source = store.list_inspected_sources(run.id)[0]

        fix_step = store.create_manual_step(
            run.id,
            command="sed -i 's/listen 8080/listen 80/' /etc/nginx/sites-enabled/default",
            entered_by="Ada Lovelace",
            purpose=f"Apply a targeted nginx listen-port fix based on source #{root_source.id}.",
            phase="fix",
        )
        store.record_backup_not_applicable(
            run.id,
            source_path="/etc/nginx/sites-enabled/default",
            reason="Mock fixture uses a disposable config file.",
            recorded_by="Ada Lovelace",
        )
        store.approve_step(run.id, fix_step.id, approved_by="Ada Lovelace")
        fix_execution = store.start_command_execution(run.id, fix_step.id)
        store.complete_command_execution(run.id, fix_execution.id, exit_code=0, duration_ms=44, error=None, timed_out=False)

        for command, output in [
            ("systemctl is-active nginx", "active\n"),
            ("curl -I http://localhost", "HTTP/1.1 200 OK\n"),
            ("journalctl -u nginx --since -5min --no-pager -n 50", "No invalid port errors\n"),
            ("systemctl restart nginx", ""),
        ]:
            validation_step = store.create_manual_step(
                run.id,
                command=command,
                entered_by="Ada Lovelace",
                purpose=f"Validate fixture recovery with {command}.",
                phase="validation",
            )
            if command == "systemctl restart nginx":
                store.record_backup_not_applicable(
                    run.id,
                    source_path="nginx",
                    reason="Technician-approved persistence restart does not alter persistent service enablement.",
                    recorded_by="Ada Lovelace",
                )
            store.approve_step(run.id, validation_step.id, approved_by="Ada Lovelace")
            validation_execution = store.start_command_execution(run.id, validation_step.id)
            if output:
                store.append_command_output_chunk(run.id, validation_execution.id, stream="stdout", content=output, redacted=False)
            store.complete_command_execution(run.id, validation_execution.id, exit_code=0, duration_ms=15, error=None, timed_out=False)

        self.assertEqual(store.get_run(run.id).status, RunStatus.READY_FOR_ACTIVITY)
        self.assertIn("backup.not_applicable", [event.event_type for event in store.list_events(run.id)])
        self.assertEqual(len(store.list_validation_results(run.id)), 4)
        self.assertTrue(store.create_activity_draft(run.id).validation_result)

    def test_passed_validation_records_result_and_allows_activity_draft(self) -> None:
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
        step = store.create_manual_step(
            run.id,
            command="systemctl is-active nginx",
            entered_by="Ada Lovelace",
            purpose="Validate nginx service is active.",
            phase="validation",
        )

        approved = store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(run.id, execution.id, stream="stdout", content="active\n", redacted=False)
        store.complete_command_execution(run.id, execution.id, exit_code=0, duration_ms=12, error=None, timed_out=False)
        results = store.list_validation_results(run.id)
        draft = store.create_activity_draft(run.id)

        self.assertEqual(approved.status, RunStatus.VALIDATING)
        self.assertEqual(store.get_run(run.id).status, RunStatus.READY_FOR_ACTIVITY)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].check_type, "service_health")
        self.assertTrue(results[0].passed)
        self.assertIn("active", results[0].summary)
        self.assertIn("active", draft.validation_result)
        self.assertIn("validation.passed", [event.event_type for event in store.list_events(run.id)])

    def test_failed_validation_records_result_and_blocks_activity_draft(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        step = store.create_manual_step(
            run.id,
            command="curl -I http://localhost",
            entered_by="Ada Lovelace",
            purpose="Validate customer-facing endpoint.",
            phase="validation",
        )

        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(run.id, execution.id, stream="stderr", content="Connection refused\n", redacted=False)
        store.complete_command_execution(run.id, execution.id, exit_code=7, duration_ms=18, error="Connection refused", timed_out=False)
        results = store.list_validation_results(run.id)

        self.assertEqual(store.get_run(run.id).status, RunStatus.VALIDATING)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].check_type, "customer_benefit")
        self.assertFalse(results[0].passed)
        self.assertIn("Connection refused", results[0].summary)
        self.assertIn("validation.failed", [event.event_type for event in store.list_events(run.id)])
        with self.assertRaises(RunTransitionError):
            store.create_activity_draft(run.id)

    def test_activity_draft_requires_passed_validation_result(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")

        with self.assertRaises(RunTransitionError):
            store.create_activity_draft(run.id)

    def test_activity_draft_uses_events_commands_evidence_and_backups(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        store.record_backup_not_applicable(
            run.id,
            source_path="/etc/nginx/sites-enabled/default",
            reason="No persistent write performed in this validation-only test.",
            recorded_by="Ada Lovelace",
        )
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
        step = store.create_manual_step(
            run.id,
            command="systemctl is-active nginx",
            entered_by="Ada Lovelace",
            purpose="Validate nginx service.",
            phase="validation",
        )
        store.approve_step(run.id, step.id, approved_by="Ada Lovelace")
        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(run.id, execution.id, stream="stdout", content="active\n", redacted=False)
        store.complete_command_execution(run.id, execution.id, exit_code=0, duration_ms=12, error=None, timed_out=False)
        backup_record = store.list_backup_records(run.id)[0]
        approval_event_ids = [
            event.id
            for event in store.list_events(run.id)
            if event.event_type in {"step.approved", "step.edited_and_approved"}
        ]
        validation_result = store.list_validation_results(run.id)[0]

        draft = store.create_activity_draft(run.id)

        self.assertEqual(draft.ticket_id, 7001)
        self.assertIn("API down", draft.summary)
        self.assertIn(f"command execution #{execution.id}", draft.commands_summary)
        self.assertIn("systemctl is-active nginx", draft.commands_summary)
        self.assertIn(f"Backup record #{backup_record.id}", draft.description)
        self.assertIn(f"Technician approval event IDs: {approval_event_ids[0]}", draft.actions_taken)
        self.assertIn(f"Backup record IDs considered: {backup_record.id}", draft.actions_taken)
        self.assertIn(f"Validation result IDs considered: {validation_result.id}", draft.actions_taken)
        self.assertIn(f"validation result #{validation_result.id}", draft.validation_result)
        self.assertIn("active", draft.validation_result)
        self.assertEqual(store.get_run(run.id).status, RunStatus.READY_FOR_ACTIVITY)

    def test_activity_claims_cite_inspected_source_ids(self) -> None:
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
        store.complete_command_execution(
            run.id,
            diagnostic_execution.id,
            exit_code=0,
            duration_ms=30,
            error=None,
            timed_out=False,
        )
        root_source = store.list_inspected_sources(run.id)[0]

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
        store.append_command_output_chunk(
            run.id,
            validation_execution.id,
            stream="stdout",
            content="HTTP/1.1 200 OK\n",
            redacted=False,
        )
        store.complete_command_execution(
            run.id,
            validation_execution.id,
            exit_code=0,
            duration_ms=15,
            error=None,
            timed_out=False,
        )
        validation_source = [source for source in store.list_inspected_sources(run.id) if source.supports == "validation"][0]

        draft = store.create_activity_draft(run.id)

        self.assertIn(f"source #{root_source.id}", draft.root_cause)
        self.assertIn(f"source #{validation_source.id}", draft.validation_result)

    def test_activity_generation_requires_concrete_evidence_before_draft(self) -> None:
        store = InMemoryRunStore()
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        validation_step = store.create_manual_step(
            run.id,
            command="systemctl is-active nginx",
            entered_by="Ada Lovelace",
            purpose="Validate nginx service.",
            phase="validation",
        )
        store.approve_step(run.id, validation_step.id, approved_by="Ada Lovelace")
        validation_outbox = store.claim_next_outbox_event()
        self.assertIsNotNone(validation_outbox)
        self.assertEqual(validation_outbox.event_type, "command.execution_requested")
        store.complete_outbox_event(validation_outbox.id)
        validation_execution = store.start_command_execution(run.id, validation_step.id)
        store.append_command_output_chunk(run.id, validation_execution.id, stream="stdout", content="active\n", redacted=False)
        store.complete_command_execution(run.id, validation_execution.id, exit_code=0, duration_ms=12, error=None, timed_out=False)

        with self.assertRaises(RunTransitionError):
            store.create_activity_draft(run.id)

    def test_technician_activity_draft_save_records_edit_event_and_redacts_content(self) -> None:
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
        validation_step = store.create_manual_step(
            run.id,
            command="curl -I http://localhost",
            entered_by="Ada Lovelace",
            purpose="Validate customer-facing endpoint.",
            phase="validation",
        )
        store.approve_step(run.id, validation_step.id, approved_by="Ada Lovelace")
        integration_validation_outbox = store.claim_next_outbox_event()
        self.assertIsNotNone(integration_validation_outbox)
        self.assertEqual(integration_validation_outbox.event_type, "command.execution_requested")
        store.complete_outbox_event(integration_validation_outbox.id)
        validation_execution = store.start_command_execution(run.id, validation_step.id)
        store.append_command_output_chunk(run.id, validation_execution.id, stream="stdout", content="HTTP/1.1 200 OK\n", redacted=False)
        store.complete_command_execution(run.id, validation_execution.id, exit_code=0, duration_ms=15, error=None, timed_out=False)
        draft = store.create_activity_draft(run.id)
        edited = draft.model_copy(
            update={
                "summary": "PASSWORD=hunter2",
                "commands_summary": "\n".join(["stdout> noisy raw terminal line"] * 20),
            }
        )

        saved = store.save_activity_draft(run.id, edited, edited_by="Ada Lovelace")

        event_types = [event.event_type for event in store.list_events(run.id)]
        self.assertEqual(saved.summary, "[REDACTED_SECRET]")
        self.assertNotIn("stdout>", saved.commands_summary)
        self.assertIn("[omitted noisy raw output]", saved.commands_summary)
        self.assertIn("activity.draft_edited", event_types)
        self.assertEqual(store.get_run(run.id).activity_draft["summary"], "[REDACTED_SECRET]")

    def test_activity_submission_is_queued_as_durable_integration_request(self) -> None:
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
        queued_diagnostic = store.claim_next_outbox_event()
        self.assertIsNotNone(queued_diagnostic)
        self.assertEqual(queued_diagnostic.event_type, "command.execution_requested")
        store.complete_outbox_event(queued_diagnostic.id)
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
        queued_validation = store.claim_next_outbox_event()
        self.assertIsNotNone(queued_validation)
        self.assertEqual(queued_validation.event_type, "command.execution_requested")
        store.complete_outbox_event(queued_validation.id)
        validation_execution = store.start_command_execution(run.id, validation_step.id)
        store.append_command_output_chunk(run.id, validation_execution.id, stream="stdout", content="HTTP/1.1 200 OK\n", redacted=False)
        store.complete_command_execution(run.id, validation_execution.id, exit_code=0, duration_ms=15, error=None, timed_out=False)
        store.create_activity_draft(run.id)

        request = store.queue_activity_submission(run.id)
        outbox_event = store.claim_next_outbox_event()

        self.assertEqual(request.status, IntegrationRequestStatus.PENDING)
        self.assertEqual(request.ticket_id, 7001)
        self.assertIn("root_cause", request.activity_payload)
        self.assertEqual(outbox_event.event_type, "integration.activity_submission_requested")
        self.assertEqual(outbox_event.payload["integration_request_id"], request.id)
        self.assertEqual(store.get_run(run.id).status, RunStatus.READY_FOR_ACTIVITY)


if __name__ == "__main__":
    unittest.main()
