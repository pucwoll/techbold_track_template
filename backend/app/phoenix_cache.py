from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any, Protocol

from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .database import create_database_engine, run_database_migrations
from .persistence_models import CustomerSystemCacheRecord, TechnicianCacheRecord, TicketCacheRecord


JsonObject = dict[str, Any]

TECHNICIAN_CACHE = TechnicianCacheRecord.__table__
TICKETS_CACHE = TicketCacheRecord.__table__
CUSTOMER_SYSTEM_CACHE = CustomerSystemCacheRecord.__table__


class PhoenixCache(Protocol):
    def save_me(self, payload: JsonObject) -> None:
        ...

    def get_me(self) -> JsonObject | None:
        ...

    def save_tickets(self, payload: list[JsonObject]) -> None:
        ...

    def save_ticket(self, payload: JsonObject) -> None:
        ...

    def list_tickets(self, *, status: str | None, priority: str | None, sort: str | None) -> list[JsonObject] | None:
        ...

    def get_ticket(self, ticket_id: int) -> JsonObject | None:
        ...

    def save_customer_system(self, payload: JsonObject) -> None:
        ...

    def get_customer_system(self, ticket_id: int) -> JsonObject | None:
        ...


class NoopPhoenixCache:
    def save_me(self, payload: JsonObject) -> None:
        return None

    def get_me(self) -> JsonObject | None:
        return None

    def save_tickets(self, payload: list[JsonObject]) -> None:
        return None

    def save_ticket(self, payload: JsonObject) -> None:
        return None

    def list_tickets(self, *, status: str | None, priority: str | None, sort: str | None) -> list[JsonObject] | None:
        return None

    def get_ticket(self, ticket_id: int) -> JsonObject | None:
        return None

    def save_customer_system(self, payload: JsonObject) -> None:
        return None

    def get_customer_system(self, ticket_id: int) -> JsonObject | None:
        return None


class InMemoryPhoenixCache:
    def __init__(self) -> None:
        self._me: JsonObject | None = None
        self._tickets: dict[int, JsonObject] = {}
        self._ticket_list_loaded = False
        self._customer_systems: dict[int, JsonObject] = {}

    def save_me(self, payload: JsonObject) -> None:
        self._me = deepcopy(payload)

    def get_me(self) -> JsonObject | None:
        return deepcopy(self._me)

    def save_tickets(self, payload: list[JsonObject]) -> None:
        self._ticket_list_loaded = True
        for ticket in payload:
            self.save_ticket(ticket)

    def save_ticket(self, payload: JsonObject) -> None:
        ticket_id = payload.get("id")
        if isinstance(ticket_id, int):
            self._tickets[ticket_id] = deepcopy(payload)

    def list_tickets(self, *, status: str | None, priority: str | None, sort: str | None) -> list[JsonObject] | None:
        if not self._ticket_list_loaded and not self._tickets:
            return None
        return _filter_and_sort_tickets(list(self._tickets.values()), status=status, priority=priority, sort=sort)

    def get_ticket(self, ticket_id: int) -> JsonObject | None:
        ticket = self._tickets.get(ticket_id)
        return deepcopy(ticket) if ticket is not None else None

    def save_customer_system(self, payload: JsonObject) -> None:
        ticket_id = payload.get("ticket_id")
        if isinstance(ticket_id, int):
            self._customer_systems[ticket_id] = deepcopy(payload)

    def get_customer_system(self, ticket_id: int) -> JsonObject | None:
        customer_system = self._customer_systems.get(ticket_id)
        return deepcopy(customer_system) if customer_system is not None else None


class PostgresPhoenixCache:
    _engine: Any = None

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._engine = create_database_engine(database_url)
        self.ensure_schema()

    def save_me(self, payload: JsonObject) -> None:
        statement = pg_insert(TECHNICIAN_CACHE).values(cache_key="me", technician_snapshot=payload)
        with self._engine.begin() as conn:
            conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[TECHNICIAN_CACHE.c.cache_key],
                    set_={
                        "technician_snapshot": statement.excluded.technician_snapshot,
                        "fetched_at": func.now(),
                        "updated_at": func.now(),
                    },
                )
            )

    def get_me(self) -> JsonObject | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(TECHNICIAN_CACHE.c.technician_snapshot).where(TECHNICIAN_CACHE.c.cache_key == "me")
            ).mappings().fetchone()
        return dict(row["technician_snapshot"]) if row else None

    def save_tickets(self, payload: list[JsonObject]) -> None:
        with self._engine.begin() as conn:
            for ticket in payload:
                self._upsert_ticket(conn, ticket)

    def save_ticket(self, payload: JsonObject) -> None:
        with self._engine.begin() as conn:
            self._upsert_ticket(conn, payload)

    def list_tickets(self, *, status: str | None, priority: str | None, sort: str | None) -> list[JsonObject] | None:
        with self._engine.connect() as conn:
            rows = conn.execute(select(TICKETS_CACHE.c.ticket_snapshot)).mappings().fetchall()
        if not rows:
            return None
        tickets = [dict(row["ticket_snapshot"]) for row in rows]
        return _filter_and_sort_tickets(tickets, status=status, priority=priority, sort=sort)

    def get_ticket(self, ticket_id: int) -> JsonObject | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(TICKETS_CACHE.c.ticket_snapshot).where(TICKETS_CACHE.c.ticket_id == ticket_id)
            ).mappings().fetchone()
        return dict(row["ticket_snapshot"]) if row else None

    def save_customer_system(self, payload: JsonObject) -> None:
        ticket_id = payload.get("ticket_id")
        if not isinstance(ticket_id, int):
            return
        statement = pg_insert(CUSTOMER_SYSTEM_CACHE).values(
            ticket_id=ticket_id,
            customer_id=_optional_int(payload.get("customer_id")),
            customer_system_snapshot=payload,
        )
        with self._engine.begin() as conn:
            conn.execute(
                statement.on_conflict_do_update(
                    index_elements=[CUSTOMER_SYSTEM_CACHE.c.ticket_id],
                    set_={
                        "customer_id": statement.excluded.customer_id,
                        "customer_system_snapshot": statement.excluded.customer_system_snapshot,
                        "fetched_at": func.now(),
                        "updated_at": func.now(),
                    },
                )
            )

    def get_customer_system(self, ticket_id: int) -> JsonObject | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(CUSTOMER_SYSTEM_CACHE.c.customer_system_snapshot).where(CUSTOMER_SYSTEM_CACHE.c.ticket_id == ticket_id)
            ).mappings().fetchone()
        return dict(row["customer_system_snapshot"]) if row else None

    def ensure_schema(self) -> None:
        run_database_migrations(self.database_url)

    def _upsert_ticket(self, conn, ticket: JsonObject) -> None:  # type: ignore[no-untyped-def]
        ticket_id = ticket.get("id")
        if not isinstance(ticket_id, int):
            return
        statement = pg_insert(TICKETS_CACHE).values(
            ticket_id=ticket_id,
            ticket_snapshot=ticket,
            status=_optional_str(ticket.get("status")),
            priority=_optional_str(ticket.get("priority")),
            customer_id=_optional_int(ticket.get("customer_id")),
            customer_name=_optional_str(ticket.get("customer_name")),
            created_at_text=_optional_str(ticket.get("created_at")),
            sla_due_at_text=_optional_str(ticket.get("sla_due_at")),
        )
        conn.execute(
            statement.on_conflict_do_update(
                index_elements=[TICKETS_CACHE.c.ticket_id],
                set_={
                    "ticket_snapshot": statement.excluded.ticket_snapshot,
                    "status": statement.excluded.status,
                    "priority": statement.excluded.priority,
                    "customer_id": statement.excluded.customer_id,
                    "customer_name": statement.excluded.customer_name,
                    "created_at_text": statement.excluded.created_at_text,
                    "sla_due_at_text": statement.excluded.sla_due_at_text,
                    "fetched_at": func.now(),
                    "updated_at": func.now(),
                },
            )
        )


@lru_cache
def get_postgres_phoenix_cache(database_url: str) -> PostgresPhoenixCache:
    return PostgresPhoenixCache(database_url)


def _filter_and_sort_tickets(
    tickets: list[JsonObject],
    *,
    status: str | None,
    priority: str | None,
    sort: str | None,
) -> list[JsonObject]:
    filtered = [
        deepcopy(ticket)
        for ticket in tickets
        if (status is None or ticket.get("status") == status)
        and (priority is None or ticket.get("priority") == priority)
    ]
    if sort == "priority":
        return sorted(filtered, key=lambda ticket: (str(ticket.get("priority") or ""), int(ticket.get("id") or 0)))
    if sort == "status":
        return sorted(filtered, key=lambda ticket: (str(ticket.get("status") or ""), int(ticket.get("id") or 0)))
    return sorted(
        filtered,
        key=lambda ticket: (str(ticket.get("created_at") or ""), int(ticket.get("id") or 0)),
        reverse=True,
    )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None
