from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schemas import JsonObject, RunEvent


@runtime_checkable
class AuditStore(Protocol):
    """Named boundary for append-only run audit timeline access."""

    def list_events(self, run_id: int, *, after_id: int = 0) -> list[RunEvent]:
        ...

    def append_event(
        self,
        run_id: int,
        *,
        actor: str,
        event_type: str,
        summary: str,
        payload: JsonObject,
        command: str | None = None,
        error: str | None = None,
    ) -> None:
        ...
