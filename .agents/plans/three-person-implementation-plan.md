# Three-Person Implementation Plan

This plan splits the implementation of the techbold AI Service Desk Autopilot across three parallel people. It is based on the canonical final spec in [techbold-final-spec.md](techbold-final-spec.md) and the supporting specs linked from there.

## Shared Goal

Deliver the smallest complete workflow that scores well:

`ticket -> customer system -> approve SSH -> command runs -> live output -> audit/evidence -> fix -> validate -> Phoenix activity`

The MVP should favor a fully demonstrable, audited troubleshooting loop over breadth. Every person should keep the scoring rubric in view, especially troubleshooting, safety/auditability, and reproducibility.

## Person 1: Backend API, Postgres, Phoenix

Own the control plane: database schema, API routes, Phoenix ERP integration, and durable run state.

### Primary Deliverables

1. Define and migrate the Postgres schema.
2. Implement Phoenix API client.
3. Implement backend API routes used by the frontend.
4. Implement run creation, approval state, command proposal state, evidence/activity read models.
5. Implement outbox writes for worker tasks.
6. Ensure everything runs in Docker Compose.

### Tables To Implement First

- `technician_cache`
- `tickets_cache`
- `customer_system_cache`
- `runs`
- `run_events`
- `proposed_steps`
- `command_executions`
- `command_output_chunks`
- `inspected_sources`
- `backup_records`
- `activity_drafts`
- `integration_requests`
- `outbox_events`

### API Surface

- `GET /api/health`
- `GET /api/me`
- `GET /api/tickets`
- `GET /api/tickets/{ticket_id}`
- `GET /api/tickets/{ticket_id}/customer-system`
- `POST /api/tickets/{ticket_id}/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`
- `GET /api/runs/{run_id}/terminal`
- `GET /api/runs/{run_id}/evidence`
- `POST /api/runs/{run_id}/connection-approval`
- `POST /api/runs/{run_id}/steps/{step_id}/approve`
- `POST /api/runs/{run_id}/steps/{step_id}/reject`
- `POST /api/runs/{run_id}/manual-command`
- `POST /api/runs/{run_id}/submit-activity`

### Milestone Order

1. Docker Compose boots `frontend`, `backend-api`, `worker`, and `postgres`.
2. Backend health route proves DB connectivity.
3. Phoenix `me`, ticket list, ticket detail, and customer system calls work.
4. Starting a run snapshots ticket/system data and writes `run.created`.
5. Approval endpoints write events and outbox rows.
6. Read endpoints expose run timeline, terminal chunks, evidence, backups, and activity draft.

### Acceptance Checks

- Phoenix errors are visible in the UI through API error responses.
- No secrets or private key contents are returned by the API.
- Every user action creates a durable `run_events` row.
- Outbox rows are idempotent and claimable by the worker with Postgres locking.

## Person 2: Worker, SSH, Safety, Agent

Own the execution plane: event-driven worker, SSH command runner, command safety, targeted backup flow, LLM planner, and validation.

### Primary Deliverables

1. Implement the Postgres outbox worker loop.
2. Implement SSH connection and command execution.
3. Implement command streaming into `command_output_chunks`.
4. Implement redaction before output is stored or streamed.
5. Implement safety classification and approval enforcement.
6. Implement evidence detection and `inspected_sources` creation.
7. Implement targeted backup records before risky file changes.
8. Implement the structured LLM planner.
9. Implement deterministic fallback diagnostics.
10. Implement validation and activity draft generation.

### Worker Events To Handle

- `agent.plan_requested`
- `command.execution_requested`
- `validation.requested`
- `activity.draft_requested`
- `activity.submit_requested`

### Safety Rules

- The LLM may propose commands only; it never executes them.
- Worker must recheck that the exact command was approved before SSH execution.
- Only one pending command should exist per active run.
- Read-only diagnostic commands can be low risk.
- Service restarts, package changes, writes, deletes, permission changes, firewall changes, and database mutations require high visibility and explicit approval.
- Dangerous/destructive commands should be blocked unless they are rewritten into a narrow, justified command.

### Backup Rules

- Do not attempt full-machine backup by default.
- Before modifying a file, copy that exact file to a timestamped backup path.
- Before changing service state, record service status and relevant config checks.
- Before changing app/data directories, record metadata and exact target paths.
- Store all backup metadata in `backup_records`.

### LLM Planner Contract

The planner returns structured output:

- `phase`
- `hypothesis`
- `proposed_command`
- `purpose`
- `risk_level`
- `expected_signal`
- `evidence_to_collect`
- `rollback_plan`
- `validation_plan`

Invalid output should be logged and retried once, then fall back to deterministic diagnostics.

### Milestone Order

1. Worker can claim and complete a fake outbox task.
2. SSH command runner executes one approved read-only command.
3. Output chunks are redacted and stored live.
4. Safety classifier blocks obvious dangerous commands.
5. Evidence rows are created for log/file/service inspections.
6. Manual command path works through the same approval and execution path.
7. LLM planner proposes one command at a time.
8. Backup records are created before approved mutations.
9. Validation commands prove service restoration or customer benefit.
10. Activity draft summarizes root cause, fix, validation, and evidence.

### Acceptance Checks

- No command can execute without a matching approval row.
- Output appears live in the browser through stored chunks/events.
- Every checked log or file appears in the evidence ledger.
- Fixes are minimal and evidence-backed.
- Validation is explicit and recorded.

## Person 3: Frontend, Demo UX, QA

Own the technician experience: React UI, live terminal, approval flow, evidence panel, activity review, and demo polish.

### Primary Deliverables

1. Build ticket overview.
2. Build ticket detail and customer system panel.
3. Build run workspace.
4. Build approval cards for connection, proposed commands, edited commands, and activity submission.
5. Build live terminal transcript.
6. Build "Logs & files checked" evidence panel.
7. Build backup/rollback panel.
8. Build activity draft review and submit UI.
9. Build clear run timeline/status indicators.
10. Add visual and E2E checks for the core demo flow.

### Main Screens

- Ticket list
- Ticket detail
- Run workspace
- Live terminal
- Evidence/logs checked
- Backup/rollback
- Activity draft/review

### Run Workspace Layout

Suggested layout:

- Left: ticket, customer system, current hypothesis, run status.
- Center: pending approval card and live terminal.
- Right: evidence ledger, backups, timeline, activity draft.

The UI should make technician control obvious:

- show exact command before approval
- show safety verdict
- show expected signal
- support approve, edit, reject, retry, abort
- never hide command output or checked files

### Milestone Order

1. Ticket list renders from backend API.
2. Ticket detail renders ticket and customer system data.
3. Start run creates a run and opens workspace.
4. Connection approval UI works.
5. Pending command approval UI works.
6. Terminal streams output chunks live.
7. Evidence panel lists every checked source.
8. Backup panel lists targeted backups.
9. Activity draft can be reviewed and submitted.
10. Demo path is visually verified on desktop and mobile-sized viewport.

### Acceptance Checks

- Technician can understand what the agent wants to do before approving.
- Live output is readable and clearly tied to a command.
- Evidence panel exposes every related log/file/source inspected.
- Activity draft cites concrete root cause, fix, and validation evidence.
- UI handles loading, empty, error, rejected, failed command, timeout, and completed states.

## Integration Order

1. Person 1 and Person 3 agree on API response shapes before deep implementation.
2. Person 1 ships Phoenix ticket/detail/customer-system APIs.
3. Person 3 builds ticket list/detail against mocked or real backend data.
4. Person 1 ships run creation and approval endpoints.
5. Person 2 ships worker outbox claim loop.
6. Person 2 ships approved read-only SSH command execution.
7. Person 3 wires live terminal to real command output.
8. Person 2 adds safety classification, redaction, evidence detection, and backup records.
9. Person 1 exposes evidence, backup, activity, and timeline read APIs.
10. Person 3 builds evidence, backup, timeline, and activity panels.
11. Person 2 adds LLM planner and deterministic fallback.
12. Person 2 and Person 3 validate manual command and edited-command paths.
13. Person 1 implements Phoenix activity submission.
14. All three run end-to-end demo rehearsals against the provided cases.

## Shared API Contract Priorities

Agree on these early to avoid blocking:

- `Run`
- `RunEvent`
- `ProposedStep`
- `CommandExecution`
- `CommandOutputChunk`
- `InspectedSource`
- `BackupRecord`
- `ActivityDraft`
- error response shape

Every object should include stable IDs, timestamps, status, and enough labels for the UI to render without guessing.

## Demo Readiness Checklist

- Docker Compose starts from a clean checkout.
- Phoenix credentials are loaded from env, not committed files.
- SSH keys are mounted read-only.
- Ticket list and details load.
- Run starts only after user action.
- SSH connection needs explicit approval.
- Every command needs explicit approval.
- Live terminal shows command output as it arrives.
- Evidence panel shows all checked logs/files/sources.
- Backup panel shows targeted backups before changes.
- Fix is minimal and tied to evidence.
- Validation proves the customer issue is resolved.
- Phoenix activity submission works and includes root cause, action, validation, and next steps.
- Secrets are redacted from logs, UI, and database output.

## Daily Coordination

Run short syncs around concrete contracts:

- Morning: schema/API shape changes and blockers.
- Midday: first end-to-end path status.
- Evening: demo script, scoring coverage, and remaining risk.

Do not let each person optimize their slice in isolation. The winning path is the integrated audited troubleshooting loop.
