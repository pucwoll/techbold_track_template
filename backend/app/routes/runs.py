from __future__ import annotations

from fastapi import APIRouter, Depends, status

from ..api_dependencies import get_phoenix_client, get_run_store, raise_http_error, raise_run_store_error
from ..phoenix_client import PhoenixAPIError, PhoenixClient
from ..run_store import RunStore, RunStoreError
from ..schemas import ConnectionApproval, Run, RunAbort, RunCreate, RunRetry


router = APIRouter()


@router.post("/api/runs", response_model=Run, status_code=status.HTTP_201_CREATED)
def start_run(
    request: RunCreate,
    client: PhoenixClient = Depends(get_phoenix_client),
    run_store: RunStore = Depends(get_run_store),
) -> Run:
    try:
        ticket = client.get_ticket(request.ticket_id)
        customer_system = client.get_customer_system(request.ticket_id)
        return run_store.create_run(
            ticket_id=request.ticket_id,
            ticket_snapshot=ticket,
            customer_system_snapshot=customer_system,
        )
    except PhoenixAPIError as error:
        raise_http_error(error)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.get("/api/runs/{run_id}", response_model=Run)
def get_run(run_id: int, run_store: RunStore = Depends(get_run_store)) -> Run:
    try:
        return run_store.get_run(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.post("/api/runs/{run_id}/connect/approve", response_model=Run)
def approve_run_connection(
    run_id: int,
    approval: ConnectionApproval,
    run_store: RunStore = Depends(get_run_store),
) -> Run:
    try:
        return run_store.approve_connection(run_id, approved_by=approval.approved_by)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.post("/api/runs/{run_id}/retry", response_model=Run)
def retry_run(
    run_id: int,
    retry: RunRetry,
    run_store: RunStore = Depends(get_run_store),
) -> Run:
    try:
        return run_store.retry_run(run_id, requested_by=retry.requested_by, reason=retry.reason)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.post("/api/runs/{run_id}/abort", response_model=Run)
def abort_run(
    run_id: int,
    abort: RunAbort,
    run_store: RunStore = Depends(get_run_store),
) -> Run:
    try:
        return run_store.abort_run(run_id, aborted_by=abort.aborted_by, reason=abort.reason)
    except RunStoreError as error:
        raise_run_store_error(error)
