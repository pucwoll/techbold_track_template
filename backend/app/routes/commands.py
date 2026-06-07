from __future__ import annotations

from fastapi import APIRouter, Depends, status

from ..api_dependencies import get_run_store, raise_run_store_error
from ..run_store import RunStore, RunStoreError
from ..schemas import (
    CommandApproval,
    CommandEditApproval,
    CommandExecution,
    CommandOutputChunk,
    ManualStepCreate,
    ProposedStep,
    Run,
    StepRejection,
)


router = APIRouter()


@router.post("/api/runs/{run_id}/manual-step", response_model=ProposedStep, status_code=status.HTTP_201_CREATED)
@router.post("/api/runs/{run_id}/manual-steps", response_model=ProposedStep, status_code=status.HTTP_201_CREATED)
def submit_manual_step(
    run_id: int,
    request: ManualStepCreate,
    run_store: RunStore = Depends(get_run_store),
) -> ProposedStep:
    try:
        return run_store.create_manual_step(
            run_id,
            command=request.command,
            entered_by=request.entered_by,
            purpose=request.purpose,
            expected_signal=request.expected_signal,
            phase=request.phase,
            timeout_s=request.timeout_s,
        )
    except RunStoreError as error:
        raise_run_store_error(error)


@router.post("/api/runs/{run_id}/steps/{step_id}/approve", response_model=Run)
def approve_step(
    run_id: int,
    step_id: int,
    approval: CommandApproval,
    run_store: RunStore = Depends(get_run_store),
) -> Run:
    try:
        return run_store.approve_step(run_id, step_id, approved_by=approval.approved_by)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.post("/api/runs/{run_id}/steps/{step_id}/edit-and-approve", response_model=Run)
def edit_and_approve_step(
    run_id: int,
    step_id: int,
    approval: CommandEditApproval,
    run_store: RunStore = Depends(get_run_store),
) -> Run:
    try:
        return run_store.edit_and_approve_step(
            run_id,
            step_id,
            command=approval.command,
            approved_by=approval.approved_by,
            purpose=approval.purpose,
            expected_signal=approval.expected_signal,
            timeout_s=approval.timeout_s,
        )
    except RunStoreError as error:
        raise_run_store_error(error)


@router.post("/api/runs/{run_id}/steps/{step_id}/reject", response_model=Run)
def reject_step(
    run_id: int,
    step_id: int,
    rejection: StepRejection,
    run_store: RunStore = Depends(get_run_store),
) -> Run:
    try:
        return run_store.reject_step(
            run_id,
            step_id,
            rejected_by=rejection.rejected_by,
            reason=rejection.reason,
        )
    except RunStoreError as error:
        raise_run_store_error(error)


@router.get("/api/runs/{run_id}/commands", response_model=list[CommandExecution])
def get_run_commands(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> list[CommandExecution]:
    try:
        return run_store.list_command_executions(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.get("/api/runs/{run_id}/output-chunks", response_model=list[CommandOutputChunk])
def get_run_output_chunks(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> list[CommandOutputChunk]:
    try:
        return run_store.list_command_output_chunks(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)
