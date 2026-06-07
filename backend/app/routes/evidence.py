from __future__ import annotations

from fastapi import APIRouter, Depends

from ..api_dependencies import get_run_store, raise_run_store_error
from ..run_store import RunStore, RunStoreError
from ..schemas import InspectedSource


router = APIRouter()


@router.get("/api/runs/{run_id}/evidence", response_model=list[InspectedSource])
def get_run_evidence(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> list[InspectedSource]:
    try:
        return run_store.list_inspected_sources(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)
