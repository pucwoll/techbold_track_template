from __future__ import annotations

from fastapi import APIRouter, Depends

from ..agent_graph import draft_activity_with_graph
from ..api_dependencies import get_run_store, raise_run_store_error
from ..run_store import RunStore, RunStoreError
from ..schemas import ActivityDraft, IntegrationRequest


router = APIRouter()


@router.post("/api/runs/{run_id}/activity/draft", response_model=ActivityDraft)
def draft_run_activity(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> ActivityDraft:
    try:
        return draft_activity_with_graph(store=run_store, run_id=run_id)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.put("/api/runs/{run_id}/activity/draft", response_model=ActivityDraft)
@router.post("/api/runs/{run_id}/activity/save", response_model=ActivityDraft)
def save_run_activity_draft(
    run_id: int,
    activity: ActivityDraft,
    edited_by: str = "technician",
    run_store: RunStore = Depends(get_run_store),
) -> ActivityDraft:
    try:
        return run_store.save_activity_draft(run_id, activity, edited_by=edited_by)
    except RunStoreError as error:
        raise_run_store_error(error)


@router.post("/api/runs/{run_id}/activity/submit", response_model=IntegrationRequest)
def submit_run_activity(
    run_id: int,
    run_store: RunStore = Depends(get_run_store),
) -> IntegrationRequest:
    try:
        return run_store.queue_activity_submission(run_id)
    except RunStoreError as error:
        raise_run_store_error(error)
