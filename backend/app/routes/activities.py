from __future__ import annotations

from fastapi import APIRouter, Depends, status

from ..api_dependencies import get_phoenix_client, raise_http_error
from ..phoenix_client import PhoenixAPIError, PhoenixClient
from ..schemas import Activity, ActivityCreate


router = APIRouter()


@router.post("/api/activities", response_model=Activity, status_code=status.HTTP_201_CREATED)
def create_activity(activity: ActivityCreate, client: PhoenixClient = Depends(get_phoenix_client)) -> object:
    try:
        return client.create_activity(activity.model_dump(exclude_none=True))
    except PhoenixAPIError as error:
        raise_http_error(error)
