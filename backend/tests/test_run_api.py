from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import unittest

from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from sse_starlette import EventSourceResponse

from app.main import (
    abort_run,
    app,
    approve_run_connection,
    approve_step,
    draft_run_activity,
    edit_and_approve_step,
    get_customer_system,
    get_me,
    get_run_dead_letter_outbox_events,
    get_ticket,
    get_run_integration_request,
    get_run_events,
    get_run_integration_requests,
    get_run_outbox_events,
    get_run_output_chunks,
    get_run_validation_results,
    list_tickets,
    propose_backup_restore,
    reject_step,
    raise_http_error,
    retry_run,
    save_run_activity_draft,
    start_run,
    stream_run_events,
    submit_run_activity,
    submit_manual_step,
)
from app.phoenix_cache import InMemoryPhoenixCache
from app.phoenix_client import PhoenixAPIError
from app.run_store import InMemoryRunStore
from app.schemas import (
    CommandApproval,
    CommandEditApproval,
    ConnectionApproval,
    ActivityDraft,
    BackupRestoreProposalCreate,
    IntegrationRequestStatus,
    ManualStepCreate,
    OutboxStatus,
    RunAbort,
    RunCreate,
    RunRetry,
    RunStatus,
    StepRejection,
    StepStatus,
    TicketStatus,
)


class FakePhoenixClient:
    def __init__(self) -> None:
        self.activities: list[dict[str, object]] = []
        self.status_updates: list[tuple[int, str]] = []
        self.list_ticket_calls: list[dict[str, str | None]] = []

    def get_me(self) -> dict[str, object]:
        return {
            "id": 101,
            "firstname": "Ada",
            "lastname": "Lovelace",
            "username": "ada",
            "teamname": "Service Desk",
        }

    def list_tickets(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
        sort: str | None = "date",
    ) -> list[dict[str, object]]:
        self.list_ticket_calls.append({"status": status, "priority": priority, "sort": sort})
        return [
            {
                "id": 7001,
                "title": "API down",
                "description": "Customer cannot reach status endpoint",
                "priority": priority or "high",
                "status": status or "OPEN",
                "customer_id": 5001,
                "customer_name": "Nordlicht Logistik GmbH",
                "tags": ["api", "urgent"],
                "created_at": "2026-06-06T09:00:00Z",
            }
        ]

    def get_ticket(self, ticket_id: int) -> dict[str, object]:
        return {
            "id": ticket_id,
            "title": "API down",
            "description": "Customer cannot reach status endpoint",
            "priority": "high",
            "status": "OPEN",
            "customer_id": 5001,
            "customer_name": "Nordlicht Logistik GmbH",
            "tags": ["api", "urgent"],
        }

    def get_customer_system(self, ticket_id: int) -> dict[str, object]:
        return {
            "ticket_id": ticket_id,
            "customer_id": 5001,
            "system": {
                "ip": "10.0.0.5",
                "port": 22,
                "username": "azureuser",
                "os": "Ubuntu 24.04",
            },
        }

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
        self.status_updates.append((ticket_id, status))
        return {
            "id": ticket_id,
            "title": "API down",
            "description": "Customer cannot reach status endpoint",
            "priority": "high",
            "status": status,
            "customer_id": 5001,
            "customer_name": "Nordlicht Logistik GmbH",
            "tags": ["api", "urgent"],
        }


class UnavailablePhoenixClient(FakePhoenixClient):
    def _raise_unavailable(self) -> None:
        raise PhoenixAPIError(503, "Phoenix ERP unavailable: connection refused")

    def get_me(self) -> dict[str, object]:
        self._raise_unavailable()

    def list_tickets(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
        sort: str | None = "date",
    ) -> list[dict[str, object]]:
        self._raise_unavailable()

    def get_ticket(self, ticket_id: int) -> dict[str, object]:
        self._raise_unavailable()

    def get_customer_system(self, ticket_id: int) -> dict[str, object]:
        self._raise_unavailable()


class RunApiTest(unittest.TestCase):
    def test_request_middleware_sets_request_id_header_and_structured_log_context(self) -> None:
        client = TestClient(app)

        with self.assertLogs("techbold.api", level="INFO") as logs:
            response = client.get("/health", headers={"X-Request-ID": "req-test-123"})

        joined_logs = "\n".join(logs.output)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "req-test-123")
        self.assertIn("API request", joined_logs)
        self.assertIn("req-test-123", joined_logs)
        self.assertIn("/health", joined_logs)

    def test_phoenix_endpoints_write_successful_responses_to_cache(self) -> None:
        cache = InMemoryPhoenixCache()
        client = FakePhoenixClient()

        me_response = Response()
        tickets_response = Response()
        ticket_response = Response()
        system_response = Response()

        me = get_me(response=me_response, client=client, cache=cache)
        tickets = list_tickets(
            response=tickets_response,
            status_filter=None,
            priority=None,
            sort="date",
            client=client,
            cache=cache,
        )
        ticket = get_ticket(7001, response=ticket_response, client=client, cache=cache)
        customer_system = get_customer_system(7001, response=system_response, client=client, cache=cache)

        self.assertEqual(me["username"], "ada")
        self.assertEqual(tickets[0]["id"], 7001)
        self.assertEqual(ticket["id"], 7001)
        self.assertEqual(customer_system["system"]["ip"], "10.0.0.5")
        self.assertEqual(me_response.headers["X-Techbold-Data-Source"], "phoenix")
        self.assertEqual(tickets_response.headers["X-Techbold-Data-Source"], "phoenix")
        self.assertEqual(ticket_response.headers["X-Techbold-Data-Source"], "phoenix")
        self.assertEqual(system_response.headers["X-Techbold-Data-Source"], "phoenix")
        self.assertEqual(cache.get_me()["username"], "ada")
        self.assertEqual(cache.get_ticket(7001)["title"], "API down")
        self.assertEqual(cache.get_customer_system(7001)["system"]["username"], "azureuser")
        self.assertEqual(cache.list_tickets(status=None, priority=None, sort="date")[0]["id"], 7001)

    def test_phoenix_endpoints_read_cache_when_backend_is_unavailable(self) -> None:
        cache = InMemoryPhoenixCache()
        healthy_client = FakePhoenixClient()
        list_tickets(response=Response(), status_filter=None, priority=None, sort="date", client=healthy_client, cache=cache)
        get_me(response=Response(), client=healthy_client, cache=cache)
        get_ticket(7001, response=Response(), client=healthy_client, cache=cache)
        get_customer_system(7001, response=Response(), client=healthy_client, cache=cache)

        failing_client = UnavailablePhoenixClient()
        me_response = Response()
        tickets_response = Response()
        ticket_response = Response()
        system_response = Response()

        self.assertEqual(get_me(response=me_response, client=failing_client, cache=cache)["username"], "ada")
        self.assertEqual(
            list_tickets(
                response=tickets_response,
                status_filter=None,
                priority=None,
                sort="date",
                client=failing_client,
                cache=cache,
            )[0]["id"],
            7001,
        )
        self.assertEqual(get_ticket(7001, response=ticket_response, client=failing_client, cache=cache)["title"], "API down")
        self.assertEqual(get_customer_system(7001, response=system_response, client=failing_client, cache=cache)["system"]["ip"], "10.0.0.5")
        self.assertEqual(me_response.headers["X-Techbold-Data-Source"], "cache")
        self.assertEqual(tickets_response.headers["X-Techbold-Data-Source"], "cache")
        self.assertEqual(ticket_response.headers["X-Techbold-Data-Source"], "cache")
        self.assertEqual(system_response.headers["X-Techbold-Data-Source"], "cache")

    def test_ticket_filter_and_sort_values_are_forwarded_to_phoenix(self) -> None:
        cache = InMemoryPhoenixCache()
        client = FakePhoenixClient()

        list_tickets(
            response=Response(),
            status_filter=TicketStatus.OPEN,
            priority="high",
            sort="priority",
            client=client,
            cache=cache,
        )

        self.assertEqual(client.list_ticket_calls, [{"status": "OPEN", "priority": "high", "sort": "priority"}])

    def test_cached_ticket_fallback_filters_and_sorts_by_supported_values(self) -> None:
        cache = InMemoryPhoenixCache()
        base_ticket = FakePhoenixClient().get_ticket(7001)
        cache.save_tickets(
            [
                {
                    **base_ticket,
                    "id": 7001,
                    "priority": "high",
                    "status": "OPEN",
                    "created_at": "2026-06-06T09:00:00Z",
                },
                {
                    **base_ticket,
                    "id": 7002,
                    "priority": "low",
                    "status": "OPEN",
                    "created_at": "2026-06-06T11:00:00Z",
                },
                {
                    **base_ticket,
                    "id": 7003,
                    "priority": "high",
                    "status": "DONE",
                    "created_at": "2026-06-06T12:00:00Z",
                },
            ]
        )

        response = list_tickets(
            response=Response(),
            status_filter=TicketStatus.OPEN,
            priority="high",
            sort="date",
            client=UnavailablePhoenixClient(),
            cache=cache,
        )

        self.assertEqual([item["id"] for item in response], [7001])

    def test_unavailable_phoenix_without_cache_returns_backend_error(self) -> None:
        cache = InMemoryPhoenixCache()
        failing_client = UnavailablePhoenixClient()

        with self.assertRaises(HTTPException) as raised:
            get_ticket(7001, response=Response(), client=failing_client, cache=cache)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("Phoenix ERP unavailable", raised.exception.detail)

    def test_phoenix_error_details_are_redacted_before_frontend_response(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            raise_http_error(
                PhoenixAPIError(
                    503,
                    "Phoenix upstream echoed Authorization: Bearer sk-live-secret\nPHOENIX_API_TOKEN=secret-token",
                )
            )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("[REDACTED_SECRET]", raised.exception.detail)
        self.assertNotIn("sk-live-secret", raised.exception.detail)
        self.assertNotIn("secret-token", raised.exception.detail)

    def test_start_run_loads_phoenix_snapshots_and_returns_connection_gate(self) -> None:
        store = InMemoryRunStore()

        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)

        self.assertEqual(run.status, RunStatus.AWAITING_CONNECTION_APPROVAL)
        self.assertEqual(run.ticket_snapshot["id"], 7001)
        self.assertEqual(run.customer_system_snapshot["system"]["ip"], "10.0.0.5")
        self.assertEqual(
            [event.event_type for event in get_run_events(run.id, run_store=store)],
            ["run.created", "connection.approval_requested"],
        )

    def test_approve_connection_returns_updated_run_and_pollable_events(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        first_events = get_run_events(run.id, run_store=store)

        approved = approve_run_connection(
            run.id,
            ConnectionApproval(approved_by="Ada Lovelace"),
            run_store=store,
        )

        self.assertEqual(approved.status, RunStatus.INVESTIGATING)
        new_events = get_run_events(run.id, after_id=first_events[-1].id, run_store=store)
        self.assertEqual([event.event_type for event in new_events], ["connection.approved", "agent.plan_requested"])

    def test_sse_stream_replays_after_id_events_in_order(self) -> None:
        async def run_assertion() -> None:
            store = InMemoryRunStore()
            run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
            first_events = get_run_events(run.id, run_store=store)
            approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)

            class RequestDisconnectsAfterBatch:
                async def is_disconnected(self) -> bool:
                    return False

            response = await stream_run_events(
                run.id,
                RequestDisconnectsAfterBatch(),
                after_id=first_events[-1].id,
                run_store=store,
            )
            self.assertIsInstance(response, EventSourceResponse)
            iterator = response.body_iterator
            chunks = [await anext(iterator), await anext(iterator)]
            text = "".join(_sse_chunk_to_text(chunk) for chunk in chunks)

            self.assertNotIn("run.created", text)
            self.assertNotIn("connection.approval_requested", text)
            self.assertIn("event: connection.approved", text)
            self.assertIn("event: agent.plan_requested", text)
            self.assertLess(text.index("event: connection.approved"), text.index("event: agent.plan_requested"))

        asyncio.run(run_assertion())

    def test_run_outbox_endpoint_returns_failed_and_dead_letter_events(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)
        claimed = store.claim_next_outbox_event()
        self.assertIsNotNone(claimed)

        store.fail_outbox_event(claimed.id, error="planner failed")
        failed_events = get_run_outbox_events(
            run.id,
            statuses=[OutboxStatus.FAILED, OutboxStatus.DEAD_LETTER],
            run_store=store,
        )
        self.assertEqual(len(failed_events), 1)
        self.assertEqual(failed_events[0].status, OutboxStatus.FAILED)
        self.assertEqual(failed_events[0].error, "planner failed")

        store._outbox[claimed.id] = failed_events[0].model_copy(  # noqa: SLF001
            update={"available_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        second_claim = store.claim_next_outbox_event()
        self.assertIsNotNone(second_claim)
        store.fail_outbox_event(second_claim.id, error="planner still failed")
        second_failed = store._outbox[claimed.id]  # noqa: SLF001
        store._outbox[claimed.id] = second_failed.model_copy(  # noqa: SLF001
            update={"available_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        third_claim = store.claim_next_outbox_event()
        self.assertIsNotNone(third_claim)
        store.fail_outbox_event(third_claim.id, error="planner dead letter")

        dead_letter_events = get_run_outbox_events(
            run.id,
            statuses=[OutboxStatus.FAILED, OutboxStatus.DEAD_LETTER],
            run_store=store,
        )
        self.assertEqual(len(dead_letter_events), 1)
        self.assertEqual(dead_letter_events[0].status, OutboxStatus.DEAD_LETTER)
        self.assertEqual(dead_letter_events[0].attempts, 3)
        self.assertEqual(dead_letter_events[0].error, "planner dead letter")

    def test_dead_letter_outbox_endpoint_returns_only_dead_letters(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)
        claimed = store.claim_next_outbox_event()
        self.assertIsNotNone(claimed)

        store.fail_outbox_event(claimed.id, error="temporary planner failure")
        failed = store._outbox[claimed.id]  # noqa: SLF001
        store._outbox[claimed.id] = failed.model_copy(update={"available_at": datetime.now(UTC) - timedelta(seconds=1)})  # noqa: SLF001
        second_claim = store.claim_next_outbox_event()
        self.assertIsNotNone(second_claim)
        store.fail_outbox_event(second_claim.id, error="another planner failure")
        second_failed = store._outbox[claimed.id]  # noqa: SLF001
        store._outbox[claimed.id] = second_failed.model_copy(update={"available_at": datetime.now(UTC) - timedelta(seconds=1)})  # noqa: SLF001
        third_claim = store.claim_next_outbox_event()
        self.assertIsNotNone(third_claim)
        store.fail_outbox_event(third_claim.id, error="planner dead letter")

        dead_letters = get_run_dead_letter_outbox_events(run.id, run_store=store)

        self.assertEqual(len(dead_letters), 1)
        self.assertEqual(dead_letters[0].status, OutboxStatus.DEAD_LETTER)
        self.assertEqual(dead_letters[0].error, "planner dead letter")

    def test_manual_step_endpoint_creates_pending_classified_step(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)

        step = submit_manual_step(
            run.id,
            ManualStepCreate(
                command="systemctl --failed",
                entered_by="Ada Lovelace",
                purpose="Check failed units.",
            ),
            run_store=store,
        )

        self.assertEqual(step.status, StepStatus.PROPOSED)
        self.assertEqual(step.risk_class, "READ_ONLY")
        self.assertEqual(store.get_run(run.id).pending_step["id"], step.id)

    def test_approve_edit_reject_retry_and_abort_endpoints(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)
        step = submit_manual_step(
            run.id,
            ManualStepCreate(
                command="systemctl status nginx --no-pager",
                entered_by="Ada Lovelace",
                purpose="Check nginx.",
            ),
            run_store=store,
        )

        rejected = reject_step(
            run.id,
            step.id,
            StepRejection(rejected_by="Ada Lovelace", reason="Prefer failed unit list first."),
            run_store=store,
        )
        self.assertEqual(rejected.status, RunStatus.INVESTIGATING)

        retrying = retry_run(run.id, RunRetry(requested_by="Ada Lovelace"), run_store=store)
        self.assertEqual(retrying.status, RunStatus.INVESTIGATING)

        replacement = submit_manual_step(
            run.id,
            ManualStepCreate(
                command="systemctl status nginx --no-pager",
                entered_by="Ada Lovelace",
                purpose="Check nginx.",
            ),
            run_store=store,
        )
        edited = edit_and_approve_step(
            run.id,
            replacement.id,
            CommandEditApproval(
                command="journalctl -u nginx --no-pager -n 80",
                approved_by="Ada Lovelace",
            ),
            run_store=store,
        )
        self.assertEqual(edited.status, RunStatus.EXECUTING)

        aborted = abort_run(
            run.id,
            RunAbort(aborted_by="Ada Lovelace", reason="Stop execution."),
            run_store=store,
        )
        self.assertEqual(aborted.status, RunStatus.ABORTED)

    def test_approve_step_endpoint_queues_command_execution(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)
        step = submit_manual_step(
            run.id,
            ManualStepCreate(
                command="systemctl --failed",
                entered_by="Ada Lovelace",
                purpose="Check failed units.",
            ),
            run_store=store,
        )

        updated = approve_step(
            run.id,
            step.id,
            CommandApproval(approved_by="Ada Lovelace"),
            run_store=store,
        )

        self.assertEqual(updated.status, RunStatus.EXECUTING)
        self.assertEqual(store.claim_next_outbox_event().event_type, "command.execution_requested")

    def test_output_chunks_endpoint_returns_sanitized_terminal_chunks_in_order(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)
        step = submit_manual_step(
            run.id,
            ManualStepCreate(
                command="printf hello",
                entered_by="Ada Lovelace",
                purpose="Capture terminal output.",
            ),
            run_store=store,
        )
        approve_step(run.id, step.id, CommandApproval(approved_by="Ada Lovelace"), run_store=store)
        execution = store.start_command_execution(run.id, step.id)
        store.append_command_output_chunk(run.id, execution.id, stream="stdout", content="hello\n", redacted=False)
        store.append_command_output_chunk(run.id, execution.id, stream="stderr", content="TOKEN=secret-token\n", redacted=False)

        chunks = get_run_output_chunks(run.id, run_store=store)

        self.assertEqual([(chunk.sequence, chunk.stream) for chunk in chunks], [(1, "stdout"), (2, "stderr")])
        self.assertEqual(chunks[0].content, "hello\n")
        self.assertIn("[REDACTED_SECRET]", chunks[1].content)
        self.assertNotIn("secret-token", chunks[1].content)
        terminal_events = [event for event in get_run_events(run.id, run_store=store) if event.event_type == "terminal.output_chunk"]
        self.assertIn("[REDACTED_SECRET]", terminal_events[-1].payload["content"])
        self.assertNotIn("secret-token", terminal_events[-1].payload["content"])

    def test_integration_request_status_endpoint_returns_one_request(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)
        validation_step = submit_manual_step(
            run.id,
            ManualStepCreate(
                command="curl -I http://localhost",
                entered_by="Ada Lovelace",
                purpose="Validate customer-facing endpoint.",
                phase="validation",
            ),
            run_store=store,
        )
        approve_step(run.id, validation_step.id, CommandApproval(approved_by="Ada Lovelace"), run_store=store)
        execution = store.start_command_execution(run.id, validation_step.id)
        store.append_command_output_chunk(run.id, execution.id, stream="stdout", content="HTTP/1.1 200 OK\n", redacted=False)
        store.complete_command_execution(run.id, execution.id, exit_code=0, duration_ms=15, error=None, timed_out=False)
        store.append_event(
            run.id,
            actor="evidence_detector",
            event_type="evidence.source_detected",
            summary="Root cause evidence recorded.",
            payload={"inspected_source_id": 1},
        )
        draft = ActivityDraft(
            ticket_id=run.ticket_id,
            start_datetime=run.started_at.isoformat(),
            end_datetime=run.started_at.isoformat(),
            summary="API endpoint restored.",
            root_cause="Nginx was not serving the expected endpoint.",
            actions_taken="Validated endpoint response.",
            commands_summary="curl -I http://localhost",
            validation_result="HTTP 200 returned.",
        )
        save_run_activity_draft(run.id, draft, run_store=store)
        queued = submit_run_activity(run.id, run_store=store)

        status_response = get_run_integration_request(run.id, queued.id, run_store=store)

        self.assertEqual(status_response.id, queued.id)
        self.assertEqual(status_response.status, IntegrationRequestStatus.PENDING)
        self.assertEqual(status_response.activity_payload["ticket_id"], 7001)

    def test_restore_endpoint_proposes_restore_step_and_records_event_semantics(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)
        backup_step = submit_manual_step(
            run.id,
            ManualStepCreate(
                command="cp -a /etc/nginx/sites-enabled/default /var/backups/techbold-autopilot/7001/1/default.prechange",
                entered_by="Ada Lovelace",
                purpose="Create targeted config backup.",
            ),
            run_store=store,
        )
        approve_step(run.id, backup_step.id, CommandApproval(approved_by="Ada Lovelace"), run_store=store)
        backup_execution = store.start_command_execution(run.id, backup_step.id)
        store.complete_command_execution(run.id, backup_execution.id, exit_code=0, duration_ms=18, error=None, timed_out=False)
        backup_record = store.list_backup_records(run.id)[0]

        restore_step = propose_backup_restore(
            run.id,
            backup_record.id,
            BackupRestoreProposalCreate(
                proposed_by="Ada Lovelace",
                reason="Restore the exact nginx config file from the recorded rollback copy.",
            ),
            run_store=store,
        )
        approve_step(run.id, restore_step.id, CommandApproval(approved_by="Ada Lovelace"), run_store=store)
        restore_execution = store.start_command_execution(run.id, restore_step.id)
        store.complete_command_execution(run.id, restore_execution.id, exit_code=0, duration_ms=20, error=None, timed_out=False)

        event_types = [event.event_type for event in get_run_events(run.id, run_store=store)]

        self.assertEqual(restore_step.phase, "restore")
        self.assertEqual(restore_step.command, backup_record.restore_command)
        self.assertIn("backup.restore_proposed", event_types)
        self.assertIn("backup.restored", event_types)

    def test_run_stream_alias_matches_spec_path(self) -> None:
        route_paths = {route.path for route in app.routes}

        self.assertIn("/api/runs/{run_id}/stream", route_paths)
        self.assertIn("/api/runs/{run_id}/events/stream", route_paths)

    def test_spec_route_aliases_are_registered(self) -> None:
        route_paths = {route.path for route in app.routes}

        self.assertIn("/api/runs/{run_id}/manual-step", route_paths)
        self.assertIn("/api/runs/{run_id}/manual-steps", route_paths)
        self.assertIn("/api/runs/{run_id}/validation-results", route_paths)
        self.assertIn("/api/runs/{run_id}/validation-expectations", route_paths)
        self.assertIn("/api/runs/{run_id}/outbox-events", route_paths)
        self.assertIn("/api/runs/{run_id}/outbox-events/dead-letter", route_paths)
        self.assertIn("/api/runs/{run_id}/integration-requests", route_paths)
        self.assertIn("/api/runs/{run_id}/integration-requests/{integration_request_id}", route_paths)
        self.assertIn("/api/runs/{run_id}/output-chunks", route_paths)
        self.assertIn("/api/runs/{run_id}/backups/{backup_record_id}/restore", route_paths)
        self.assertIn("/api/runs/{run_id}/activity/draft", route_paths)
        self.assertIn("/api/runs/{run_id}/activity/save", route_paths)
        self.assertIn("/api/runs/{run_id}/activity/submit", route_paths)

    def test_run_activity_draft_schema_requires_non_empty_scored_fields(self) -> None:
        with self.assertRaises(ValueError):
            ActivityDraft(
                ticket_id=7001,
                start_datetime="2026-06-06T10:00:00Z",
                end_datetime="2026-06-06T10:15:00Z",
                summary="",
                root_cause="nginx bind conflict",
                actions_taken="checked logs and fixed config",
                commands_summary="journalctl; curl",
                validation_result="HTTP 200",
            )

    def test_activity_draft_endpoint_rejects_run_without_passed_validation(self) -> None:
        store = InMemoryRunStore()
        run = start_run(RunCreate(ticket_id=7001), client=FakePhoenixClient(), run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)

        with self.assertRaises(HTTPException) as raised:
            draft_run_activity(run.id, run_store=store)

        self.assertEqual(raised.exception.status_code, 409)

    def test_run_scoped_activity_draft_and_submit_uses_run_context(self) -> None:
        store = InMemoryRunStore()
        phoenix = FakePhoenixClient()
        run = start_run(RunCreate(ticket_id=7001), client=phoenix, run_store=store)
        approve_run_connection(run.id, ConnectionApproval(approved_by="Ada Lovelace"), run_store=store)
        diagnostic_step = submit_manual_step(
            run.id,
            ManualStepCreate(
                command="journalctl -u nginx --no-pager -n 80",
                entered_by="Ada Lovelace",
                purpose="Inspect nginx errors.",
            ),
            run_store=store,
        )
        approve_step(run.id, diagnostic_step.id, CommandApproval(approved_by="Ada Lovelace"), run_store=store)
        diagnostic_execution = store.start_command_execution(run.id, diagnostic_step.id)
        store.append_command_output_chunk(
            run.id,
            diagnostic_execution.id,
            stream="stderr",
            content="nginx: bind() to 0.0.0.0:80 failed (98: Address already in use)\n",
            redacted=False,
        )
        store.complete_command_execution(run.id, diagnostic_execution.id, exit_code=0, duration_ms=30, error=None, timed_out=False)

        validation_step = submit_manual_step(
            run.id,
            ManualStepCreate(
                command="curl -I http://localhost",
                entered_by="Ada Lovelace",
                purpose="Validate customer-facing endpoint.",
                phase="validation",
            ),
            run_store=store,
        )
        approve_step(run.id, validation_step.id, CommandApproval(approved_by="Ada Lovelace"), run_store=store)
        validation_execution = store.start_command_execution(run.id, validation_step.id)
        store.append_command_output_chunk(run.id, validation_execution.id, stream="stdout", content="HTTP/1.1 200 OK\n", redacted=False)
        store.complete_command_execution(run.id, validation_execution.id, exit_code=0, duration_ms=15, error=None, timed_out=False)

        validation_results = get_run_validation_results(run.id, run_store=store)
        draft = draft_run_activity(run.id, run_store=store)

        self.assertEqual(len(validation_results), 1)
        self.assertTrue(validation_results[0].passed)
        self.assertEqual(draft.ticket_id, 7001)
        self.assertIn("API down", draft.summary)
        self.assertIn("command execution #", draft.commands_summary)
        self.assertIn("validation result #", draft.validation_result)

        saved = save_run_activity_draft(run.id, draft, edited_by="Ada Lovelace", run_store=store)
        request = submit_run_activity(run.id, run_store=store)

        self.assertEqual(saved.ticket_id, 7001)
        self.assertEqual(request.status, IntegrationRequestStatus.PENDING)
        self.assertEqual(phoenix.activities, [])
        self.assertEqual(phoenix.status_updates, [])
        self.assertEqual(store.get_run(run.id).status, RunStatus.READY_FOR_ACTIVITY)
        self.assertEqual(get_run_integration_requests(run.id, run_store=store)[0].id, request.id)
        self.assertIn("activity.submission_requested", [event.event_type for event in get_run_events(run.id, run_store=store)])

def _sse_chunk_to_text(chunk: object) -> str:
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8")
    if hasattr(chunk, "encode"):
        encoded = chunk.encode()
        if isinstance(encoded, bytes):
            return encoded.decode("utf-8")
    return str(chunk)


if __name__ == "__main__":
    unittest.main()
