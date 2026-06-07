from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote
from uuid import uuid4
import unittest

from sqlalchemy import delete
from sqlalchemy import text
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateSchema
from sqlalchemy.schema import DropSchema

from app.database import create_database_engine, run_database_migrations
from app.persistence_models import Base, OutboxEventRecord, RunEventRecord
from app.persistence_models import claimable_outbox_events_query
from app.run_store import PostgresRunStore, RunTransitionError
from app.schemas import OutboxStatus, RunStatus
from tests.test_run_store import customer_system_snapshot, ticket_snapshot


TEST_DATABASE_URL = os.environ.get("TECHBOLD_TEST_DATABASE_URL")


class PostgresPersistenceIntegrationTest(unittest.TestCase):
    _base_database_url: str | None = None
    _postgres_container: Any = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._base_database_url = TEST_DATABASE_URL
        if cls._base_database_url:
            return
        try:
            from testcontainers.postgres import PostgresContainer
        except ImportError as error:
            raise unittest.SkipTest("Set TECHBOLD_TEST_DATABASE_URL or install testcontainers[postgres] to run Postgres integration tests") from error

        container = PostgresContainer("postgres:17-alpine", driver="psycopg")
        try:
            container.start()
        except Exception as error:
            raise unittest.SkipTest(f"testcontainers Postgres is unavailable: {error}") from error
        cls._postgres_container = container
        cls._base_database_url = container.get_connection_url()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._postgres_container is not None:
            cls._postgres_container.stop()

    def setUp(self) -> None:
        assert self._base_database_url is not None
        self.base_database_url = self._base_database_url
        self.schema_name = f"techbold_test_{uuid4().hex[:12]}"
        self.base_engine = create_database_engine(self.base_database_url)
        with self.base_engine.begin() as conn:
            conn.execute(CreateSchema(self.schema_name))
        self.database_url = self._url_with_search_path(self.base_database_url, self.schema_name)

    def tearDown(self) -> None:
        with self.base_engine.begin() as conn:
            conn.execute(DropSchema(self.schema_name, cascade=True, if_exists=True))
        self.base_engine.dispose()

    def test_fresh_database_migrates_with_alembic(self) -> None:
        run_database_migrations(self.database_url)
        engine = create_database_engine(self.database_url)
        try:
            with engine.connect() as conn:
                table_names = set(conn.dialect.get_table_names(conn))
        finally:
            engine.dispose()

        self.assertTrue({"runs", "run_events", "outbox_events", "alembic_version"}.issubset(table_names))
        self.assertIn("runs", Base.metadata.tables)

    def test_run_state_outbox_and_timeline_survive_api_and_worker_store_restarts(self) -> None:
        api_store = PostgresRunStore(self.database_url)
        run = api_store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )

        backend_after_restart = PostgresRunStore(self.database_url)
        reloaded_run = backend_after_restart.get_run(run.id)
        first_events = backend_after_restart.list_events(run.id)

        self.assertEqual(reloaded_run.status, RunStatus.AWAITING_CONNECTION_APPROVAL)
        self.assertEqual(
            [event.event_type for event in first_events],
            ["run.created", "connection.approval_requested"],
        )

        approved = backend_after_restart.approve_connection(run.id, approved_by="Ada Lovelace")
        self.assertEqual(approved.status, RunStatus.INVESTIGATING)

        worker_after_restart = PostgresRunStore(self.database_url)
        claimed = worker_after_restart.claim_next_outbox_event()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.run_id, run.id)
        self.assertEqual(claimed.event_type, "agent.plan_requested")
        worker_after_restart.complete_outbox_event(claimed.id)

        backend_after_worker_restart = PostgresRunStore(self.database_url)
        final_run = backend_after_worker_restart.get_run(run.id)
        final_events = backend_after_worker_restart.list_events(run.id)
        outbox_events = backend_after_worker_restart.list_outbox_events(run.id, statuses=None)

        self.assertEqual(final_run.status, RunStatus.INVESTIGATING)
        self.assertEqual(
            [event.event_type for event in final_events],
            ["run.created", "connection.approval_requested", "connection.approved", "agent.plan_requested"],
        )
        self.assertEqual([event.id for event in final_events], sorted(event.id for event in final_events))
        self.assertEqual(len(outbox_events), 1)
        self.assertEqual(outbox_events[0].status, OutboxStatus.COMPLETED)

    def test_run_events_are_append_only_at_the_database_layer(self) -> None:
        store = PostgresRunStore(self.database_url)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )

        engine = create_database_engine(self.database_url)
        try:
            with engine.begin() as conn:
                event = conn.execute(
                    select(RunEventRecord).where(RunEventRecord.run_id == run.id).order_by(RunEventRecord.id).limit(1)
                ).mappings().one()
                original_summary = event["summary"]

                with self.assertRaises(SQLAlchemyError):
                    conn.execute(
                        update(RunEventRecord)
                        .where(RunEventRecord.id == event["id"])
                        .values(summary="mutated")
                    )
                with self.assertRaises(SQLAlchemyError):
                    conn.execute(delete(RunEventRecord).where(RunEventRecord.id == event["id"]))
        finally:
            engine.dispose()

        self.assertEqual(store.list_events(run.id)[0].summary, original_summary)

    def test_outbox_skip_locked_allows_parallel_workers_to_claim_different_rows(self) -> None:
        store = PostgresRunStore(self.database_url)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        store.retry_run(run.id, requested_by="Ada Lovelace", reason="Queue a second planner event.")

        engine = create_database_engine(self.database_url)
        try:
            with engine.connect() as conn_a, engine.connect() as conn_b:
                trans_a = conn_a.begin()
                trans_b = conn_b.begin()
                try:
                    first = conn_a.execute(claimable_outbox_events_query()).mappings().fetchone()
                    second = conn_b.execute(claimable_outbox_events_query()).mappings().fetchone()
                    self.assertIsNotNone(first)
                    self.assertIsNotNone(second)
                    self.assertNotEqual(first["id"], second["id"])
                finally:
                    trans_b.rollback()
                    trans_a.rollback()
        finally:
            engine.dispose()

    def test_stale_processing_outbox_event_is_recovered_in_postgres(self) -> None:
        store = PostgresRunStore(self.database_url)
        run = store.create_run(
            ticket_id=7001,
            ticket_snapshot=ticket_snapshot(),
            customer_system_snapshot=customer_system_snapshot(),
        )
        store.approve_connection(run.id, approved_by="Ada Lovelace")
        claimed = store.claim_next_outbox_event()
        self.assertIsNotNone(claimed)

        engine = create_database_engine(self.database_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    update(OutboxEventRecord)
                    .where(OutboxEventRecord.id == claimed.id)
                    .values(claimed_at=text("now() - interval '10 minutes'"))
                )
        finally:
            engine.dispose()

        recovered_count = store.recover_stale_outbox_events(stale_after_s=60)
        reclaimed = store.claim_next_outbox_event()

        self.assertEqual(recovered_count, 1)
        self.assertIsNotNone(reclaimed)
        self.assertEqual(reclaimed.id, claimed.id)
        self.assertEqual(reclaimed.attempts, 2)
        self.assertIn("outbox.recovered", [event.event_type for event in store.list_events(run.id)])

    def test_activity_draft_is_gated_by_concrete_validation_in_postgres(self) -> None:
        store = PostgresRunStore(self.database_url)
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
            purpose="Inspect nginx root-cause evidence.",
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

        with self.assertRaises(RunTransitionError):
            store.create_activity_draft(run.id)

        validation_step = store.create_manual_step(
            run.id,
            command="curl -I http://localhost",
            entered_by="Ada Lovelace",
            purpose="Validate customer-facing endpoint.",
            phase="validation",
        )
        store.approve_step(run.id, validation_step.id, approved_by="Ada Lovelace")
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

        draft = store.create_activity_draft(run.id)

        self.assertIn("validation result #", draft.validation_result)
        self.assertIn("HTTP/1.1 200 OK", draft.validation_result)
        self.assertEqual(store.get_run(run.id).status, RunStatus.READY_FOR_ACTIVITY)

    @staticmethod
    def _url_with_search_path(database_url: str, schema_name: str) -> str:
        separator = "&" if "?" in database_url else "?"
        return f"{database_url}{separator}options={quote(f'-csearch_path={schema_name}', safe='')}"
