from __future__ import annotations

from fastapi import APIRouter, Depends

from ..api_dependencies import check_database
from ..config import Settings, get_settings
from ..schemas import HealthIntegration, HealthResponse


router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        database=check_database(settings.database_url),
        phoenix=HealthIntegration(configured=settings.phoenix_configured),
    )
