from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from .logging_config import get_logger
from .schemas import Activity, CustomerSystem, Employee, Ticket


JsonObject = dict[str, Any]
logger = get_logger("techbold.phoenix")


class PhoenixAPIError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class PhoenixAvailabilityError(PhoenixAPIError):
    pass


def _safe_read_retryer() -> Retrying:
    return Retrying(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.01, max=0.05),
        retry=retry_if_exception_type(PhoenixAvailabilityError),
        reraise=True,
    )


class PhoenixClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 8.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s
        self.client = client or httpx.Client(
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
            },
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
        )

    def get_me(self) -> JsonObject:
        return self._validate_model(Employee, self._request("GET", "/api/v1/me"), "employee", "GET /api/v1/me")

    def list_tickets(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
        sort: str | None = "date",
    ) -> list[JsonObject]:
        query = {"status": status, "priority": priority, "sort": sort}
        payload = self._request("GET", "/api/v1/me/tickets", query=query)
        if not isinstance(payload, list):
            raise PhoenixAPIError(502, "Phoenix ERP response for GET /api/v1/me/tickets did not match expected ticket list schema")
        return [
            self._validate_model(Ticket, ticket, "ticket", "GET /api/v1/me/tickets")
            for ticket in payload
        ]

    def get_ticket(self, ticket_id: int) -> JsonObject:
        path = f"/api/v1/tickets/{ticket_id}"
        return self._validate_model(Ticket, self._request("GET", path), "ticket", f"GET {path}")

    def get_customer_system(self, ticket_id: int) -> JsonObject:
        path = f"/api/v1/tickets/{ticket_id}/customer-system"
        return self._validate_model(
            CustomerSystem,
            self._request("GET", path),
            "customer-system",
            f"GET {path}",
        )

    def create_activity(self, payload: JsonObject) -> JsonObject:
        return self._validate_model(
            Activity,
            self._request("POST", "/api/v1/activities/create", payload=payload),
            "activity",
            "POST /api/v1/activities/create",
        )

    def set_ticket_status(self, ticket_id: int, status: str) -> JsonObject:
        path = f"/api/v1/tickets/{ticket_id}/status"
        return self._validate_model(Ticket, self._request("PATCH", path, payload={"status": status}), "ticket", f"PATCH {path}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: JsonObject | None = None,
        query: dict[str, str | None] | None = None,
    ) -> Any:
        if method.upper() == "GET":
            return _safe_read_retryer()(self._request_once, method, path, payload=payload, query=query)
        return self._request_once(method, path, payload=payload, query=query)

    def _request_once(
        self,
        method: str,
        path: str,
        *,
        payload: JsonObject | None = None,
        query: dict[str, str | None] | None = None,
    ) -> Any:
        filtered_query = {key: value for key, value in (query or {}).items() if value}
        self._log_request(method, path, filtered_query)
        try:
            response = self.client.request(
                method,
                self._url(path),
                json=payload,
                params=filtered_query,
            )
            self._log_response(method, path, response.status_code)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise self._http_error(error) from error
        except httpx.TimeoutException as error:
            raise PhoenixAvailabilityError(504, "Phoenix ERP request timed out") from error
        except httpx.TransportError as error:
            raise PhoenixAvailabilityError(503, f"Phoenix ERP unavailable: {error}") from error

        if not response.content:
            return {}
        try:
            return response.json()
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise PhoenixAPIError(502, "Phoenix ERP returned malformed JSON") from error

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _validate_model(
        self,
        model_type: type[BaseModel],
        payload: Any,
        schema_name: str,
        operation: str,
    ) -> JsonObject:
        try:
            return model_type.model_validate(payload).model_dump(mode="json")
        except ValidationError as error:
            raise PhoenixAPIError(
                502,
                f"Phoenix ERP response for {operation} did not match expected {schema_name} schema",
            ) from error

    def _http_error(self, error: httpx.HTTPStatusError) -> PhoenixAPIError:
        response = error.response
        detail = response.reason_phrase or f"Phoenix ERP returned HTTP {response.status_code}"
        if response.content:
            try:
                parsed = response.json()
            except ValueError:
                parsed = {}
            if isinstance(parsed, dict) and parsed.get("detail"):
                detail_value = parsed["detail"]
                detail = detail_value if isinstance(detail_value, str) else json.dumps(detail_value)
        error_type = PhoenixAvailabilityError if response.status_code in {502, 503, 504} else PhoenixAPIError
        return error_type(response.status_code, detail)

    def _log_request(self, method: str, path: str, query: dict[str, str]) -> None:
        logger.info("Phoenix request", method=method, path=path, query_keys=sorted(query), timeout_s=self.timeout_s)

    def _log_response(self, method: str, path: str, status_code: int) -> None:
        logger.info("Phoenix response", method=method, path=path, status_code=status_code)
