# Final Acceptance Matrix

Date: 2026-06-07

This matrix maps the final implementation spec acceptance items and scoring priorities to
tests, source files, or demo steps.

| Acceptance item | Evidence |
| --- | --- |
| Loads Phoenix tickets | `backend/tests/test_phoenix_client.py`, `backend/tests/test_run_api.py`, `frontend/tests/ticket-states.spec.ts`; demo steps 1-2 in `docs/demo-rehearsal.md` |
| Loads customer system info | `backend/tests/test_phoenix_client.py`, `backend/tests/test_run_api.py`, `frontend/tests/ticket-states.spec.ts`; demo steps 3-4 |
| Runs from one Docker Compose stack | `docker-compose.yml`, `scripts/compose-smoke.sh`, README "Run" and "Local verification" |
| No frontend secrets | `frontend/src/App.tsx` uses only `VITE_API_BASE`; secret scan in `docs/secret-check.md`; frontend screenshots from `docs/frontend-manual-verification.md` |
| SSH key stays backend/worker only | `docker-compose.yml` mounts `./keys:/keys:ro` for backend/worker; no frontend key path or key material in `frontend/src` |
| Every SSH command has proposal, safety verdict, approval event, execution log, and transcript | `backend/tests/test_run_store.py`, `backend/tests/test_worker.py`, `backend/tests/test_fake_ssh_worker_integration.py` |
| Blocked dangerous commands are logged and not executed | `backend/tests/test_safety_layer.py`, `backend/tests/test_run_store.py`, `backend/tests/test_worker.py` |
| Agent never executes commands directly | `backend/tests/test_worker.py::test_agent_plan_only_creates_proposed_step_without_ssh_execution`, `backend/app/worker.py` |
| Live terminal streams sanitized output | `backend/tests/test_worker.py`, `backend/tests/test_run_api.py`, `frontend/tests/ticket-states.spec.ts` SSE and polling tests |
| Every checked log/file/source appears in evidence ledger | `backend/tests/test_evidence_detector.py`, `backend/tests/test_run_store.py`, `frontend/tests/ticket-states.spec.ts` |
| Persistent fixes have targeted backup or explicit backup-not-applicable record | `backend/tests/test_milestone5_backup_ledgers.py`, `backend/tests/test_run_store.py`, frontend backup tests |
| Validation proves customer benefit | `backend/tests/test_milestone6_run_loop.py`, `backend/tests/test_run_store.py`, activity validation assertions in `backend/tests/test_worker.py` |
| Activity includes all required fields | `backend/tests/test_worker.py::test_worker_activity_payload_matches_phoenix_openapi_contract`, `backend/tests/test_run_api.py`, `backend/app/schemas.py` |
| README and `.env.example` complete | README setup/run/env/architecture/troubleshooting/demo sections, `.env.example` |
| Tests and mocks are runnable | `backend/tests`, `frontend/tests`, README "Local verification" |
| No secrets committed | `.gitignore`, `git ls-files` check in `docs/secret-check.md`, redaction tests |

Scoring emphasis:

| Rubric area | Evidence |
| --- | --- |
| A - ERP workflow | Phoenix client/API tests, frontend ticket state tests, activity contract tests |
| B - Troubleshooting performance | planner/fix/validation loop tests, mocked incident fixture, demo rehearsal |
| C - Safety/auditability | safety hard-block tests, append-only event tests, redaction events, outbox recovery, approval gates |
| D - Technician experience/control | frontend e2e controls for approve/edit/reject/retry/abort, visual artifacts |
| E - Engineering/reproducibility | Docker Compose smoke script, README, module-separated backend, runnable tests |
