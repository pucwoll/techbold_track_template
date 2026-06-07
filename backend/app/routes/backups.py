from __future__ import annotations

from fastapi import APIRouter, Depends, status

from ..api_dependencies import get_run_store, raise_run_store_error
from ..run_store import RunStore, RunStoreError
from ..schemas import BackupNotApplicableCreate, BackupRecord, BackupRestoreProposalCreate, ProposedStep


router = APIRouter()


@router.get("/api/runs/{run_id}/backups", response_model=list[BackupRecord])
def get_run_backups(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> list[BackupRecord]:
    try:
        return run_store.list_backup_records(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.post("/api/runs/{run_id}/backups/not-applicable", response_model=BackupRecord, status_code=status.HTTP_201_CREATED)
def record_backup_not_applicable(
    run_id: int,
    request: BackupNotApplicableCreate,
    run_store: RunStore = Depends(get_run_store),
) -> BackupRecord:
    try:
        return run_store.record_backup_not_applicable(
            run_id,
            source_path=request.source_path,
            reason=request.reason,
            recorded_by=request.recorded_by,
        )
    except RunStoreError as error:
        raise_run_store_error(error)


@router.post("/api/runs/{run_id}/backups/{backup_record_id}/restore", response_model=ProposedStep, status_code=status.HTTP_201_CREATED)
def propose_backup_restore(
    run_id: int,
    backup_record_id: int,
    request: BackupRestoreProposalCreate,
    run_store: RunStore = Depends(get_run_store),
) -> ProposedStep:
    try:
        return run_store.propose_restore_command(
            run_id,
            backup_record_id,
            proposed_by=request.proposed_by,
            reason=request.reason,
        )
    except RunStoreError as error:
        raise_run_store_error(error)
