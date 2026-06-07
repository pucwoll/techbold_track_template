from __future__ import annotations

import signal
import time
from typing import Protocol

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from .agent_orchestrator import (
    OpenAIChatPlannerAdapter,
    PlannerAdapter,
)
from .agent_graph import CommandExecutionWorkflow, PlannerWorkflow, draft_activity_with_graph
from .config import get_settings
from .logging_config import bind_log_context, clear_log_context, get_logger
from .phoenix_client import PhoenixAPIError, PhoenixClient
from .run_store import RunStore, RunStoreError, get_postgres_run_store
from .schemas import IntegrationRequestStatus, OutboxEvent, TicketStatus
from .ssh_runner import CommandRunner, SSHCommandRunner


logger = get_logger("techbold.worker")
shutdown_requested = False


class PhoenixActivityWriter(Protocol):
    def create_activity(self, payload: dict[str, object]) -> dict[str, object]:
        ...

    def set_ticket_status(self, ticket_id: int, status: str) -> dict[str, object]:
        ...


def _is_transient_phoenix_error(error: BaseException) -> bool:
    return isinstance(error, PhoenixAPIError) and error.status_code in {502, 503, 504}


def _phoenix_status_retryer() -> Retrying:
    return Retrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.01, max=0.05),
        retry=retry_if_exception(_is_transient_phoenix_error),
        reraise=True,
    )


class Worker:
    def __init__(
        self,
        *,
        store: RunStore,
        runner: CommandRunner,
        command_timeout_s: int,
        command_output_limit_bytes: int,
        planner_adapter: PlannerAdapter | None = None,
        phoenix_client: PhoenixActivityWriter | None = None,
    ) -> None:
        self.store = store
        self.runner = runner
        self.command_timeout_s = command_timeout_s
        self.command_output_limit_bytes = command_output_limit_bytes
        self.planner_adapter = planner_adapter
        self.phoenix_client = phoenix_client

    def process_next(self) -> bool:
        outbox_event = self.store.claim_next_outbox_event()
        if not outbox_event:
            return False

        clear_log_context()
        bind_log_context(
            outbox_event_id=outbox_event.id,
            run_id=outbox_event.run_id,
            outbox_event_type=outbox_event.event_type,
        )
        try:
            logger.info("worker processing outbox event")
            self._process(outbox_event)
        except Exception as error:
            logger.exception("worker failed to process outbox event")
            self.store.fail_outbox_event(outbox_event.id, error=str(error))
            return True
        finally:
            clear_log_context()

        self.store.complete_outbox_event(outbox_event.id)
        logger.info(
            "worker completed outbox event",
            outbox_event_id=outbox_event.id,
            run_id=outbox_event.run_id,
            outbox_event_type=outbox_event.event_type,
        )
        return True

    def _process(self, outbox_event: OutboxEvent) -> None:
        if outbox_event.event_type == "agent.plan_requested":
            self._process_agent_plan(outbox_event)
            return
        if outbox_event.event_type == "command.execution_requested":
            self._process_command_execution(outbox_event)
            return
        if outbox_event.event_type == "agent.activity_draft_requested":
            self._process_activity_draft(outbox_event)
            return
        if outbox_event.event_type == "integration.activity_submission_requested":
            self._process_activity_submission(outbox_event)
            return
        if outbox_event.run_id is not None:
            self.store.append_event(
                outbox_event.run_id,
                actor="worker",
                event_type="outbox.ignored",
                summary=f"No worker handler for {outbox_event.event_type}.",
                payload={"outbox_event_id": outbox_event.id, "event_type": outbox_event.event_type},
            )

    def _process_agent_plan(self, outbox_event: OutboxEvent) -> None:
        PlannerWorkflow(
            store=self.store,
            planner_adapter=self.planner_adapter,
            command_timeout_s=self.command_timeout_s,
        ).invoke(outbox_event)

    def _process_command_execution(self, outbox_event: OutboxEvent) -> None:
        CommandExecutionWorkflow(
            store=self.store,
            runner=self.runner,
            command_timeout_s=self.command_timeout_s,
        ).invoke(outbox_event)

    def _process_activity_draft(self, outbox_event: OutboxEvent) -> None:
        if outbox_event.run_id is None:
            return
        draft_activity_with_graph(store=self.store, run_id=outbox_event.run_id)

    def _process_activity_submission(self, outbox_event: OutboxEvent) -> None:
        if outbox_event.run_id is None:
            return
        if self.phoenix_client is None:
            raise RuntimeError("Phoenix client is required for activity submission worker events")
        integration_request_id = int(outbox_event.payload["integration_request_id"])
        request = self.store.get_integration_request(outbox_event.run_id, integration_request_id)
        if request.status == IntegrationRequestStatus.COMPLETED:
            return
        request = self.store.mark_integration_request_processing(outbox_event.run_id, integration_request_id)
        try:
            phoenix_activity_id = request.phoenix_activity_id
            if phoenix_activity_id is None:
                created = self.phoenix_client.create_activity(request.activity_payload)
                raw_activity_id = created.get("id")
                if not isinstance(raw_activity_id, int):
                    raise RuntimeError("Phoenix activity response did not include an integer id")
                phoenix_activity_id = raw_activity_id
                request = self.store.mark_integration_activity_created(
                    outbox_event.run_id,
                    integration_request_id,
                    phoenix_activity_id=phoenix_activity_id,
                )
            self._set_ticket_status_with_retry(request.ticket_id, TicketStatus.DONE.value)
            self.store.mark_integration_request_completed(
                outbox_event.run_id,
                integration_request_id,
                ticket_status=TicketStatus.DONE.value,
            )
        except Exception as error:
            self.store.fail_integration_request(
                outbox_event.run_id,
                integration_request_id,
                error=str(error),
            )
            raise

    def _set_ticket_status_with_retry(self, ticket_id: int, status: str) -> dict[str, object]:
        if self.phoenix_client is None:
            raise RuntimeError("Phoenix client is required for activity submission worker events")
        return _phoenix_status_retryer()(self.phoenix_client.set_ticket_status, ticket_id, status)


def _request_shutdown(signum: int, _frame: object) -> None:
    global shutdown_requested
    shutdown_requested = True
    logger.info("worker shutdown requested", signal=signum)


def build_worker_from_settings() -> Worker:
    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for the worker")
    store = get_postgres_run_store(
        settings.database_url,
        settings.command_timeout_s,
        settings.command_output_limit_bytes,
    )
    runner = SSHCommandRunner(
        private_key_path=settings.ssh_private_key_path,
        private_key_dir=settings.ssh_private_key_dir,
        known_hosts_path=settings.ssh_known_hosts_path,
        host_key_policy=settings.ssh_host_key_policy,
    )
    phoenix_client = None
    if settings.phoenix_configured and settings.phoenix_api_token:
        phoenix_client = PhoenixClient(
            base_url=settings.phoenix_api_base_url or "",
            token=settings.phoenix_api_token.get_secret_value(),
            timeout_s=settings.phoenix_timeout_s,
        )
    planner_adapter = None
    if settings.openai_api_key and settings.openai_model:
        planner_adapter = OpenAIChatPlannerAdapter(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            timeout_s=min(float(settings.command_timeout_s), 30.0),
        )
    return Worker(
        store=store,
        runner=runner,
        command_timeout_s=settings.command_timeout_s,
        command_output_limit_bytes=settings.command_output_limit_bytes,
        planner_adapter=planner_adapter,
        phoenix_client=phoenix_client,
    )


def main() -> None:
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    worker = build_worker_from_settings()
    logger.info("worker started")
    while not shutdown_requested:
        try:
            processed = worker.process_next()
        except RunStoreError:
            logger.exception("run store error while processing worker queue")
            processed = False
        if not processed:
            time.sleep(1)
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
