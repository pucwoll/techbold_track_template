from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette import EventSourceResponse, JSONServerSentEvent

from ..api_dependencies import get_run_store, raise_run_store_error
from ..run_store import RunStore, RunStoreError
from ..schemas import OutboxEvent, OutboxStatus, RunEvent


router = APIRouter()


@router.get("/api/runs/{run_id}/events", response_model=list[RunEvent])
def get_run_events(
    run_id: int,
    after_id: int = 0,
    run_store: RunStore = Depends(get_run_store),
) -> list[RunEvent]:
    try:
        return run_store.list_events(run_id, after_id=after_id)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.get("/api/runs/{run_id}/outbox-events", response_model=list[OutboxEvent])
def get_run_outbox_events(
    run_id: int,
    statuses: list[OutboxStatus] | None = Query(default=None, alias="status"),
    run_store: RunStore = Depends(get_run_store),
) -> list[OutboxEvent]:
    selected_statuses = set(statuses) if statuses else {OutboxStatus.FAILED, OutboxStatus.DEAD_LETTER}
    try:
        return run_store.list_outbox_events(run_id, statuses=selected_statuses)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.get("/api/runs/{run_id}/outbox-events/dead-letter", response_model=list[OutboxEvent])
def get_run_dead_letter_outbox_events(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> list[OutboxEvent]:
    try:
        return run_store.list_outbox_events(run_id, statuses={OutboxStatus.DEAD_LETTER})
    except RunStoreError as error:
        raise_run_store_error(error)


@router.get("/api/runs/{run_id}/stream")
@router.get("/api/runs/{run_id}/events/stream")
async def stream_run_events(
    run_id: int,
    request: Request,
    after_id: int = 0,
    run_store: RunStore = Depends(get_run_store),
) -> EventSourceResponse:
    try:
        run_store.get_run(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)

    async def event_generator():  # type: ignore[no-untyped-def]
        last_event_id = after_id
        try:
            while not await request.is_disconnected():
                events = run_store.list_events(run_id, after_id=last_event_id)
                for event in events:
                    last_event_id = event.id
                    yield JSONServerSentEvent(
                        data=event.model_dump(mode="json"),
                        event=event.event_type,
                        id=str(event.id),
                    )
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise

    return EventSourceResponse(
        event_generator(),
        headers={"Cache-Control": "no-cache"},
        ping=15,
        send_timeout=30,
    )
