from __future__ import annotations

import socket
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Response, status

from .config import Settings, get_settings
from .phoenix_cache import NoopPhoenixCache, PhoenixCache, get_postgres_phoenix_cache
from .phoenix_client import PhoenixAPIError, PhoenixClient
from .run_store import (
    RunNotFoundError,
    RunStore,
    RunStoreError,
    RunTransitionError,
    get_postgres_run_store,
)
from .safety_layer import redact_output
from .schemas import HealthIntegration


def get_phoenix_client(settings: Settings = Depends(get_settings)) -> PhoenixClient:
    if not settings.phoenix_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Phoenix ERP is not configured. Set PHOENIX_API_BASE_URL and PHOENIX_API_TOKEN.",
        )
    token = settings.phoenix_api_token.get_secret_value() if settings.phoenix_api_token else ""
    return PhoenixClient(
        base_url=settings.phoenix_api_base_url or "",
        token=token,
        timeout_s=settings.phoenix_timeout_s,
    )


def get_run_store(settings: Settings = Depends(get_settings)) -> RunStore:
    if not settings.database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured. Set DATABASE_URL to enable troubleshooting runs.",
        )
    try:
        return get_postgres_run_store(
            settings.database_url,
            settings.command_timeout_s,
            settings.command_output_limit_bytes,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error


def get_phoenix_cache(settings: Settings = Depends(get_settings)) -> PhoenixCache:
    if not settings.database_url:
        return NoopPhoenixCache()
    try:
        return get_postgres_phoenix_cache(settings.database_url)
    except RuntimeError:
        return NoopPhoenixCache()


def raise_http_error(error: PhoenixAPIError) -> None:
    detail, _redacted = redact_output(error.detail)
    raise HTTPException(status_code=error.status_code, detail=detail)


def can_use_phoenix_cache(error: PhoenixAPIError) -> bool:
    return error.status_code in {status.HTTP_503_SERVICE_UNAVAILABLE, status.HTTP_504_GATEWAY_TIMEOUT}


def mark_data_source(response: Response, source: str) -> None:
    response.headers["X-Techbold-Data-Source"] = source


def raise_run_store_error(error: RunStoreError) -> None:
    if isinstance(error, RunNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error.args[0] or "Run not found")
    if isinstance(error, RunTransitionError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.args[0] or "Invalid run transition")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Run store failed")


def check_database(database_url: str | None) -> HealthIntegration:
    if not database_url:
        return HealthIntegration(configured=False, reachable=None)

    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
        return HealthIntegration(configured=True, reachable=False, error="DATABASE_URL is not a PostgreSQL URL")

    try:
        with socket.create_connection((parsed.hostname, parsed.port or 5432), timeout=0.4):
            return HealthIntegration(configured=True, reachable=True)
    except OSError as error:
        return HealthIntegration(configured=True, reachable=False, error=str(error))
