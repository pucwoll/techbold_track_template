from __future__ import annotations

from fastapi import APIRouter, Depends

from ..api_dependencies import get_run_store, raise_run_store_error
from ..run_store import RunStore, RunStoreError
from ..schemas import ValidationExpectation, ValidationResult


router = APIRouter()


@router.get("/api/runs/{run_id}/validation-results", response_model=list[ValidationResult])
def get_run_validation_results(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> list[ValidationResult]:
    try:
        return run_store.list_validation_results(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.get("/api/runs/{run_id}/validation-expectations", response_model=list[ValidationExpectation])
def get_run_validation_expectations(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> list[ValidationExpectation]:
    try:
        return run_store.list_validation_expectations(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)
