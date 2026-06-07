from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response

from ..api_dependencies import (
    can_use_phoenix_cache,
    get_phoenix_cache,
    get_phoenix_client,
    mark_data_source,
    raise_http_error,
)
from ..phoenix_cache import PhoenixCache
from ..phoenix_client import PhoenixAPIError, PhoenixClient
from ..schemas import CustomerSystem, Employee, StatusUpdate, Ticket, TicketStatus


router = APIRouter()


@router.get("/api/me", response_model=Employee)
def get_me(
    response: Response,
    client: PhoenixClient = Depends(get_phoenix_client),
    cache: PhoenixCache = Depends(get_phoenix_cache),
) -> object:
    try:
        payload = client.get_me()
        cache.save_me(payload)
        mark_data_source(response, "phoenix")
        return payload
    except PhoenixAPIError as error:
        if can_use_phoenix_cache(error):
            cached = cache.get_me()
            if cached is not None:
                mark_data_source(response, "cache")
                return cached
        raise_http_error(error)


@router.get("/api/tickets", response_model=list[Ticket])
def list_tickets(
    response: Response,
    status_filter: TicketStatus | None = Query(default=None, alias="status"),
    priority: str | None = Query(default=None),
    sort: str = Query(default="date", pattern="^(date|priority|status)$"),
    client: PhoenixClient = Depends(get_phoenix_client),
    cache: PhoenixCache = Depends(get_phoenix_cache),
) -> object:
    try:
        payload = client.list_tickets(
            status=status_filter.value if status_filter else None,
            priority=priority,
            sort=sort,
        )
        cache.save_tickets(payload)
        mark_data_source(response, "phoenix")
        return payload
    except PhoenixAPIError as error:
        if can_use_phoenix_cache(error):
            cached = cache.list_tickets(
                status=status_filter.value if status_filter else None,
                priority=priority,
                sort=sort,
            )
            if cached is not None:
                mark_data_source(response, "cache")
                return cached
        raise_http_error(error)


@router.get("/api/tickets/{ticket_id}", response_model=Ticket)
def get_ticket(
    ticket_id: int,
    response: Response,
    client: PhoenixClient = Depends(get_phoenix_client),
    cache: PhoenixCache = Depends(get_phoenix_cache),
) -> object:
    try:
        payload = client.get_ticket(ticket_id)
        cache.save_ticket(payload)
        mark_data_source(response, "phoenix")
        return payload
    except PhoenixAPIError as error:
        if can_use_phoenix_cache(error):
            cached = cache.get_ticket(ticket_id)
            if cached is not None:
                mark_data_source(response, "cache")
                return cached
        raise_http_error(error)


@router.get("/api/tickets/{ticket_id}/customer-system", response_model=CustomerSystem)
def get_customer_system(
    ticket_id: int,
    response: Response,
    client: PhoenixClient = Depends(get_phoenix_client),
    cache: PhoenixCache = Depends(get_phoenix_cache),
) -> object:
    try:
        payload = client.get_customer_system(ticket_id)
        cache.save_customer_system(payload)
        mark_data_source(response, "phoenix")
        return payload
    except PhoenixAPIError as error:
        if can_use_phoenix_cache(error):
            cached = cache.get_customer_system(ticket_id)
            if cached is not None:
                mark_data_source(response, "cache")
                return cached
        raise_http_error(error)


@router.patch("/api/tickets/{ticket_id}/status", response_model=Ticket)
def set_ticket_status(
    ticket_id: int,
    update: StatusUpdate,
    client: PhoenixClient = Depends(get_phoenix_client),
) -> object:
    try:
        return client.set_ticket_status(ticket_id, update.status.value)
    except PhoenixAPIError as error:
        raise_http_error(error)
