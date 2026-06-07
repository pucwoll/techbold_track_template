# Demo Rehearsal

Date: 2026-06-07

Purpose: rehearse the final judging flow after a Phoenix reset and verify it aligns with
the final spec and scoring rubric.

## Reset

Phoenix reset endpoint from the case brief:

```bash
POST /api/v1/me/reset
Authorization: Bearer <PHOENIX_API_TOKEN>
```

Use this before the live demo to clear prior activities and restore the customer VMs.

## Rehearsal Flow

1. Open the frontend at `http://localhost:5173`.
2. Confirm the ticket list loads from Phoenix, sorted by date, with title, customer, priority, status, and tags visible.
3. Select one ticket.
4. Confirm ticket detail shows the report and customer system host, port, username, OS, and notes without secrets.
5. Click `Start troubleshooting`.
6. Approve SSH connection.
7. Confirm the agent proposes exactly one diagnostic command and the safety verdict is visible.
8. Approve the diagnostic command.
9. Confirm live terminal output appears in the transcript and redaction markers replace secret-like values.
10. Confirm the inspected log/file/source appears in `Logs & files checked` and links to the transcript.
11. For a persistent fix, confirm `Backups & rollback` shows a targeted backup plan or record `backup.not_applicable` with a reason.
12. Approve the minimal evidence-backed fix.
13. Confirm validation includes service health, customer benefit, recent-log, and persistence checks where applicable.
14. Generate and review the activity draft.
15. Confirm `summary`, `root_cause`, `actions_taken`, `commands_summary`, and `validation_result` are non-empty and cite concrete evidence.
16. Submit the activity and confirm Phoenix integration status reaches `Activity submitted and ticket closed`.

## Evidence To Capture

- Compose stack health: `bash scripts/compose-smoke.sh`
- Backend tests: `cd backend && mise exec -- uv run python -m unittest discover -s tests`
- Frontend e2e and screenshots: `cd frontend && mise exec -- pnpm run test:e2e`
- Secret check: see `docs/secret-check.md`

## Rehearsal Result

Live Phoenix reset/read check:

| Check | Result |
| --- | --- |
| Phoenix reset accepted | `POST /api/v1/me/reset` returned `200` with response keys `detail`, `message` |
| Tickets loaded after reset | Local backend `GET /api/tickets?sort=date` returned `200` and `5` tickets |
| Customer system loaded after reset | Local backend `GET /api/tickets/7001/customer-system` returned `200` with `ip`, `notes`, `os`, `port`, `username` |

The full troubleshooting loop rehearsal is covered by automated fake/mocked tests:

- `backend/tests/test_fake_ssh_worker_integration.py`
- `backend/tests/test_worker.py`
- `backend/tests/test_run_store.py::RunStoreTest.test_mocked_incident_fixture_covers_diagnosis_backup_fix_and_validation`
- `frontend/tests/ticket-states.spec.ts`

The automated rehearsal validates ticket list/detail/run console, approval/edit/reject/retry/abort,
live terminal streaming, evidence, backup/rollback, validation, activity review, durable
Phoenix submission state, and completed ticket status handling without mutating a live
customer VM during this final hardening pass.
