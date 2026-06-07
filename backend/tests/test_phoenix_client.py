from __future__ import annotations

import json
import unittest

import httpx

from app.phoenix_client import PhoenixAPIError, PhoenixClient


def json_response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, json=payload)


def raw_response(body: bytes, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=body)


class RecordingTransport:
    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class PhoenixClientTest(unittest.TestCase):
    def test_list_tickets_sends_auth_header_and_filters(self) -> None:
        transport = RecordingTransport(
            [
                json_response(
                    [
                        {
                            "id": 7001,
                            "title": "API down",
                            "description": "Customer cannot reach status endpoint",
                            "priority": "high",
                            "status": "OPEN",
                            "customer_id": 5001,
                            "customer_name": "Nordlicht Logistik GmbH",
                            "tags": ["api", "urgent"],
                        }
                    ]
                )
            ]
        )
        client = PhoenixClient(
            base_url="https://phoenix.example/root/",
            token="secret-token",
            transport=httpx.MockTransport(transport),
            timeout_s=2.5,
        )

        tickets = client.list_tickets(status="OPEN", priority="high", sort="date")

        self.assertEqual(tickets[0]["id"], 7001)
        request = transport.requests[0]
        self.assertEqual(
            str(request.url),
            "https://phoenix.example/root/api/v1/me/tickets?status=OPEN&priority=high&sort=date",
        )
        self.assertEqual(request.headers["Authorization"], "Bearer secret-token")

    def test_empty_ticket_filters_are_omitted(self) -> None:
        transport = RecordingTransport([json_response([])])
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        client.list_tickets(status="", priority=None, sort="priority")

        request = transport.requests[0]
        self.assertEqual(
            str(request.url),
            "https://phoenix.example/api/v1/me/tickets?sort=priority",
        )

    def test_http_errors_are_exposed_with_status_code_and_detail(self) -> None:
        transport = RecordingTransport([json_response({"detail": "Missing or invalid bearer token"}, status_code=401)])
        client = PhoenixClient("https://phoenix.example", "bad-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.get_me()

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Missing or invalid bearer token")

    def test_404_errors_are_exposed_with_detail(self) -> None:
        transport = RecordingTransport([json_response({"detail": "Ticket not found"}, status_code=404)])
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.get_ticket(9999)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.detail, "Ticket not found")

    def test_422_errors_are_exposed_with_detail_payload(self) -> None:
        transport = RecordingTransport([json_response({"detail": [{"loc": ["query", "sort"], "msg": "invalid sort"}]}, status_code=422)])
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.list_tickets(sort="bad")

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("invalid sort", raised.exception.detail)

    def test_timeout_errors_become_gateway_timeout(self) -> None:
        request = httpx.Request("GET", "https://phoenix.example/api/v1/me")
        transport = RecordingTransport([httpx.TimeoutException("timed out", request=request)] * 3)
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.get_me()

        self.assertEqual(raised.exception.status_code, 504)
        self.assertEqual(raised.exception.detail, "Phoenix ERP request timed out")

    def test_network_errors_become_service_unavailable(self) -> None:
        request = httpx.Request("GET", "https://phoenix.example/api/v1/tickets/7001")
        transport = RecordingTransport([httpx.ConnectError("connection refused", request=request)] * 3)
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.get_ticket(7001)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("Phoenix ERP unavailable", raised.exception.detail)

    def test_malformed_json_becomes_bad_gateway(self) -> None:
        transport = RecordingTransport([raw_response(b"{not-valid-json")])
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.get_me()

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("malformed JSON", raised.exception.detail)

    def test_empty_body_becomes_bad_gateway_for_required_payload(self) -> None:
        transport = RecordingTransport([raw_response(b"")])
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.get_me()

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("did not match expected employee schema", raised.exception.detail)

    def test_get_me_validates_required_response_fields(self) -> None:
        transport = RecordingTransport([json_response({"id": 101, "firstname": "Ada"})])
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.get_me()

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("did not match expected employee schema", raised.exception.detail)

    def test_list_tickets_validates_required_ticket_fields(self) -> None:
        transport = RecordingTransport([json_response([{"id": 7001, "status": "OPEN"}])])
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.list_tickets()

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("did not match expected ticket schema", raised.exception.detail)

    def test_customer_system_validates_required_nested_fields(self) -> None:
        transport = RecordingTransport([json_response({"ticket_id": 7001, "customer_id": 5001, "system": {"port": 22}})])
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.get_customer_system(7001)

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("did not match expected customer-system schema", raised.exception.detail)

    def test_create_activity_posts_complete_contract_payload(self) -> None:
        payload = {
            "ticket_id": 7001,
            "start_datetime": "2026-06-06T10:00:00Z",
            "end_datetime": "2026-06-06T10:20:00Z",
            "summary": "Restored the customer endpoint.",
            "root_cause": "nginx could not bind the expected port.",
            "actions_taken": "Checked service state, fixed config, validated HTTP response.",
            "commands_summary": "journalctl, nginx config check, curl validation.",
            "validation_result": "HTTP/1.1 200 OK returned from the local endpoint.",
        }
        transport = RecordingTransport(
            [
                json_response(
                    {
                        **payload,
                        "id": 9001,
                        "team_id": 42,
                        "team_name": "Service Desk",
                        "employee_id": 101,
                        "created_at": "2026-06-06T10:21:00Z",
                    },
                    status_code=201,
                )
            ]
        )
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        activity = client.create_activity(payload)

        request = transport.requests[0]
        self.assertEqual(str(request.url), "https://phoenix.example/api/v1/activities/create")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(request.content.decode("utf-8")), payload)
        self.assertEqual(activity["id"], 9001)
        self.assertEqual(activity["validation_result"], payload["validation_result"])

    def test_activity_submit_and_ticket_status_errors_are_mapped_for_worker_retries(self) -> None:
        create_transport = RecordingTransport([json_response({"detail": "Phoenix activity worker unavailable"}, status_code=503)])
        status_transport = RecordingTransport([json_response({"detail": [{"loc": ["body", "status"], "msg": "invalid status"}]}, status_code=422)])

        with self.assertRaises(PhoenixAPIError) as create_error:
            PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(create_transport)).create_activity(
                {
                    "ticket_id": 7001,
                    "start_datetime": "2026-06-06T10:00:00Z",
                    "end_datetime": "2026-06-06T10:20:00Z",
                }
            )
        with self.assertRaises(PhoenixAPIError) as status_error:
            PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(status_transport)).set_ticket_status(7001, "DONE")

        self.assertEqual(create_error.exception.status_code, 503)
        self.assertEqual(create_error.exception.detail, "Phoenix activity worker unavailable")
        self.assertEqual(status_error.exception.status_code, 422)
        self.assertIn("invalid status", status_error.exception.detail)

    def test_safe_get_retries_availability_failure_before_validating_success(self) -> None:
        request = httpx.Request("GET", "https://phoenix.example/api/v1/me")
        transport = RecordingTransport(
            [
                httpx.ConnectError("connection refused", request=request),
                httpx.ConnectError("connection refused", request=request),
                json_response(
                    {
                        "id": 101,
                        "firstname": "Ada",
                        "lastname": "Lovelace",
                        "username": "ada",
                        "teamname": "Service Desk",
                    }
                ),
            ]
        )
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        employee = client.get_me()

        self.assertEqual(employee["username"], "ada")
        self.assertEqual(len(transport.requests), 3)

    def test_activity_creation_is_not_retried_on_availability_failure(self) -> None:
        request = httpx.Request("POST", "https://phoenix.example/api/v1/activities/create")
        transport = RecordingTransport([httpx.ConnectError("connection refused", request=request)])
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertRaises(PhoenixAPIError) as raised:
            client.create_activity(
                {
                    "ticket_id": 7001,
                    "start_datetime": "2026-06-06T10:00:00Z",
                    "end_datetime": "2026-06-06T10:20:00Z",
                }
            )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(len(transport.requests), 1)

    def test_structured_logs_do_not_include_bearer_token(self) -> None:
        transport = RecordingTransport(
            [
                json_response(
                    {
                        "id": 101,
                        "firstname": "Ada",
                        "lastname": "Lovelace",
                        "username": "ada",
                        "teamname": "Service Desk",
                    }
                )
            ]
        )
        client = PhoenixClient("https://phoenix.example", "secret-token", transport=httpx.MockTransport(transport))

        with self.assertLogs("techbold.phoenix", level="INFO") as logs:
            client.get_me()

        joined = "\n".join(logs.output)
        self.assertIn("Phoenix request", joined)
        self.assertIn("Phoenix response", joined)
        self.assertNotIn("secret-token", joined)


if __name__ == "__main__":
    unittest.main()
