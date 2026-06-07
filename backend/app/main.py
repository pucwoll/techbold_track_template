from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .api_dependencies import (
    check_database,
    get_phoenix_cache,
    get_phoenix_client,
    get_run_store,
    raise_http_error,
    raise_run_store_error,
)
from .config import get_settings
from .database import run_database_migrations
from .logging_config import bind_log_context, clear_log_context, get_logger
from .routes.activities import create_activity, router as activities_router
from .routes.activity import (
    draft_run_activity,
    router as activity_router,
    save_run_activity_draft,
    submit_run_activity,
)
from .routes.backups import (
    get_run_backups,
    propose_backup_restore,
    record_backup_not_applicable,
    router as backups_router,
)
from .routes.commands import (
    approve_step,
    edit_and_approve_step,
    get_run_commands,
    get_run_output_chunks,
    reject_step,
    router as commands_router,
    submit_manual_step,
)
from .routes.evidence import get_run_evidence, router as evidence_router
from .routes.events import (
    get_run_dead_letter_outbox_events,
    get_run_events,
    get_run_outbox_events,
    router as events_router,
    stream_run_events,
)
from .routes.health import health, router as health_router
from .routes.integration import (
    get_run_integration_request,
    get_run_integration_requests,
    router as integration_router,
)
from .routes.runs import abort_run, approve_run_connection, get_run, retry_run, router as runs_router, start_run
from .routes.tickets import (
    get_customer_system,
    get_me,
    get_ticket,
    list_tickets,
    router as tickets_router,
    set_ticket_status,
)
from .routes.validation import (
    get_run_validation_expectations,
    get_run_validation_results,
    router as validation_router,
)


logger = get_logger("techbold.api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.database_url:
        run_database_migrations(settings.database_url)
    yield


app = FastAPI(
    title="techbold AI Service Desk Autopilot",
    version="0.1.0",
    description="Backend control plane for Phoenix ERP, technician approvals, SSH execution, and audit state.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_logging_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    clear_log_context()
    bind_log_context(request_id=request_id, method=request.method, path=request.url.path)
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("API request failed", duration_ms=round((perf_counter() - started) * 1000, 2))
        raise
    else:
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "API request",
            status_code=response.status_code,
            duration_ms=round((perf_counter() - started) * 1000, 2),
        )
        return response
    finally:
        clear_log_context()


for router in (
    health_router,
    tickets_router,
    activities_router,
    runs_router,
    commands_router,
    evidence_router,
    backups_router,
    validation_router,
    activity_router,
    integration_router,
    events_router,
):
    app.include_router(router)
