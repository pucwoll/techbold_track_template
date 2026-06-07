from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
FINAL_SPEC = ROOT / ".agents" / "plans" / "techbold-final-spec.md"
CHECKLIST = ROOT / ".agents" / "plans" / "techbold-remaining-checklist.md"

FRONTEND_API_PATHS = [
    "/health",
    "/api/me",
    "/api/tickets",
    "/api/tickets/{ticket_id}",
    "/api/tickets/{ticket_id}/customer-system",
    "/api/tickets/{ticket_id}/status",
    "/api/runs",
    "/api/runs/{run_id}",
    "/api/runs/{run_id}/connect/approve",
    "/api/runs/{run_id}/manual-step",
    "/api/runs/{run_id}/events",
    "/api/runs/{run_id}/stream",
    "/api/runs/{run_id}/commands",
    "/api/runs/{run_id}/output-chunks",
    "/api/runs/{run_id}/evidence",
    "/api/runs/{run_id}/backups",
    "/api/runs/{run_id}/backups/not-applicable",
    "/api/runs/{run_id}/backups/{backup_record_id}/restore",
    "/api/runs/{run_id}/validation-results",
    "/api/runs/{run_id}/validation-expectations",
    "/api/runs/{run_id}/outbox-events",
    "/api/runs/{run_id}/outbox-events/dead-letter",
    "/api/runs/{run_id}/integration-requests",
    "/api/runs/{run_id}/integration-requests/{integration_request_id}",
    "/api/runs/{run_id}/steps/{step_id}/approve",
    "/api/runs/{run_id}/steps/{step_id}/edit-and-approve",
    "/api/runs/{run_id}/steps/{step_id}/reject",
    "/api/runs/{run_id}/retry",
    "/api/runs/{run_id}/abort",
    "/api/runs/{run_id}/activity/draft",
    "/api/runs/{run_id}/activity/save",
    "/api/runs/{run_id}/activity/submit",
]


class BackendApiSurfaceDocsTest(unittest.TestCase):
    def test_readme_documents_all_frontend_facing_api_paths(self) -> None:
        readme = README.read_text()

        missing = [path for path in FRONTEND_API_PATHS if path not in readme]

        self.assertEqual(missing, [])
        self.assertIn("Polling fallback", readme)
        self.assertIn("GET /api/runs/{run_id}/events?after_id=", readme)
        self.assertIn("dead-letter", readme)
        self.assertIn("backup.restore_proposed", readme)
        self.assertIn("backup.restored", readme)

    def test_final_spec_documents_extended_backend_api_surface(self) -> None:
        final_spec = FINAL_SPEC.read_text()

        missing = [path for path in FRONTEND_API_PATHS if path not in final_spec]

        self.assertEqual(missing, [])
        self.assertIn("command_output_chunks", final_spec)
        self.assertIn("integration request status", final_spec)
        self.assertIn("dead-letter", final_spec)
        self.assertIn("backup.restore_proposed", final_spec)
        self.assertIn("backup.restored", final_spec)

    def test_backend_api_surface_gap_checkboxes_are_validated(self) -> None:
        checklist = CHECKLIST.read_text()

        self.assertIn("- [x] Document all frontend-facing API endpoints in README or generated OpenAPI notes.", checklist)
        self.assertIn("- [x] Confirm `/api/runs/{run_id}/stream` has polling fallback behavior documented and tested.", checklist)
        self.assertIn("- [x] Add a command output chunks endpoint to the spec or remove it if it is not meant to be frontend-facing.", checklist)
        self.assertIn("- [x] Add API for integration request status if Phoenix submission moves to the worker.", checklist)
        self.assertIn("- [x] Add API for dead-letter outbox visibility.", checklist)
        self.assertIn("- [x] Add API or event semantics for restore proposals and restore execution.", checklist)


if __name__ == "__main__":
    unittest.main()
