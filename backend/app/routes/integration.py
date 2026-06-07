from __future__ import annotations

from fastapi import APIRouter, Depends

from ..api_dependencies import get_run_store, raise_run_store_error
from ..run_store import RunStore, RunStoreError
from ..schemas import IntegrationRequest


router = APIRouter()


@router.get("/api/runs/{run_id}/integration-requests", response_model=list[IntegrationRequest])
def get_run_integration_requests(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> list[IntegrationRequest]:
    try:
        return run_store.list_integration_requests(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.get("/api/runs/{run_id}/integration-requests/{integration_request_id}", response_model=IntegrationRequest)
def get_run_integration_request(
    run_id: int,
    integration_request_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> IntegrationRequest:
    try:
        return run_store.get_integration_request(run_id, integration_request_id)
    except RunStoreError as error:
        raise_run_store_error(error)
