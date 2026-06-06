# Final Implementation Spec

## Purpose

Build the techbold AI Service Desk Autopilot as a technician-controlled web app that can:

1. Load assigned Phoenix ERP tickets.
2. Load the affected customer system and SSH details.
3. Let an LLM-assisted planner diagnose the incident.
4. Require technician approval before every SSH action.
5. Stream approved command output live in the browser.
6. Record every command, file/log checked, safety decision, backup, validation, and ERP action.
7. Apply minimal, evidence-backed fixes.
8. Validate customer benefit and persistence.
9. Submit a complete Phoenix activity.

The product must optimize for the scoring rubric: troubleshooting performance first, safety/auditability second, then technician experience and reproducibility.

## Source Specs

| Topic | Canonical detail |
| --- | --- |
| Case brief | [../../techbold-case.md](../../techbold-case.md) |
| Scoring rubric | [../../docs/scoring.md](../../docs/scoring.md) |
| Phoenix ERP API | [../../docs/phoenix-openapi.yaml](../../docs/phoenix-openapi.yaml) |
| Product workflow | [../../docs/application-spec.md](../../docs/application-spec.md) |
| Architecture | [../../docs/architecture-spec.md](../../docs/architecture-spec.md) |
| LLM agent behavior | [../../docs/agent-spec.md](../../docs/agent-spec.md) |
| Live browser terminal | [../../docs/live-terminal-spec.md](../../docs/live-terminal-spec.md) |
| Checked logs/evidence ledger | [../../docs/evidence-log-spec.md](../../docs/evidence-log-spec.md) |
| Backup and rollback policy | [../../docs/backup-policy-spec.md](../../docs/backup-policy-spec.md) |

## Core Decisions

- Use FastAPI backend, React/Vite frontend, Postgres, and one Docker Compose stack.
- Use a backend API plus a worker from the same backend codebase.
- Use Postgres as durable run state, audit log, outbox queue, command log, evidence ledger, backup ledger, and activity draft store.
- Use a custom event-driven worker state machine, not LangGraph, Pi harness, or autonomous LangChain SSH tools.
- The LLM proposes structured next steps only. It never executes SSH commands.
- Every SSH command requires safety classification and technician approval.
- Stream approved command output to the browser through SSE first, with polling fallback.
- Do not implement raw browser SSH or a persistent interactive shell.
- Do not create full-machine backups by default. Use targeted pre-change backups only.
- Generate Phoenix activity text from audit, command, evidence, validation, and backup records.

## Compose Services

The app should run with:

- `frontend`: React technician workspace.
- `backend-api`: FastAPI API consumed by the frontend.
- `worker`: Postgres outbox processor for planning, SSH execution, validation, and ERP submission.
- `postgres`: durable state.

External dependencies:

- Phoenix ERP API.
- Customer VMs reachable over SSH.
- Optional LLM API.

Required mounts/env:

- `./keys:/keys:ro`
- `PHOENIX_API_BASE_URL`
- `PHOENIX_API_TOKEN`
- `SSH_PRIVATE_KEY_PATH`
- `SSH_USERNAME`
- `DATABASE_URL`
- optional LLM settings such as `OPENAI_API_KEY` and `OPENAI_MODEL`
- command timeout/output limit settings

## User Workflow

### 1. Ticket Overview

Required UI:

- Show technician identity from Phoenix.
- Show tickets with title, customer, priority, status, created date, SLA date, and tags.
- Default sort by date.
- Support sorting/filtering by status, priority, and date.
- Handle loading, empty, 401, 404, and backend unavailable states.

Backend:

- `GET /api/me`
- `GET /api/tickets?status=&priority=&sort=`
- Phoenix calls: `GET /api/v1/me`, `GET /api/v1/me/tickets`

Acceptance:

- Covers scoring A ticket list points and D overview points.

### 2. Ticket Detail

Required UI:

- Show ticket title, report, customer, priority, status, tags.
- Show customer system info: IP/host, port, username, OS, notes.
- Do not show keys or secrets.
- Show `Start troubleshooting`.

Backend:

- `GET /api/tickets/{ticket_id}`
- `GET /api/tickets/{ticket_id}/customer-system`
- Phoenix calls: `GET /api/v1/tickets/{ticket_id}`, `GET /api/v1/tickets/{ticket_id}/customer-system`

Acceptance:

- Covers scoring A customer-system points and D detail-view points.

### 3. Run Creation and Connection Approval

When troubleshooting starts:

1. Create `runs` row.
2. Snapshot ticket and customer system into Postgres.
3. Append `run.created`.
4. Append `connection.approval_requested`.
5. UI asks technician to approve SSH connection before planning/execution.

Technician can approve or abort.

Acceptance:

- No SSH work begins before explicit connection approval.

### 4. Agent Planning Loop

Agent loop:

1. Worker receives `agent.plan_requested`.
2. LLM/planner builds context from ticket, system info, sanitized audit history, latest evidence, and safety rules.
3. Planner proposes exactly one next step.
4. Pydantic validates structured output.
5. Safety layer classifies the command.
6. UI displays pending command card.
7. Technician approves, edits, rejects, retries, or aborts.

The planner phases are plain worker functions:

- `ticket_analyzer`
- `system_context_planner`
- `observation_interpreter`
- `fix_planner`
- `validation_planner`
- `activity_writer`

Do not use LangGraph for this build. See [../../docs/agent-spec.md](../../docs/agent-spec.md).

Acceptance:

- Only one proposed SSH command can be pending at once.
- Invalid LLM output is logged and retried or falls back to deterministic diagnostics.

### 5. Command Execution and Live Terminal

Approved command flow:

1. API records `step.approved` or `step.edited_and_approved`.
2. API creates outbox row `command.execution_requested`.
3. Worker claims row with Postgres locking.
4. Worker rechecks run state, approval, exact command text, and safety verdict.
5. Worker creates `command_executions` row in running state.
6. Worker appends `command.started`.
7. Worker runs exactly the approved command over SSH.
8. Worker redacts stdout/stderr chunks before storage or display.
9. Worker writes `terminal.output_chunk` events and `command_output_chunks` rows.
10. UI streams output live in the terminal.
11. Worker finalizes exit code, duration, status, and appends completion/failure/timeout event.

Live terminal requirements:

- Show pending command before approval.
- Show safety verdict.
- Show exact approved command.
- Stream sanitized stdout/stderr live.
- Show exit code and duration.
- Link output to inspected evidence where applicable.
- Support manual command entry, but manual commands enter the same safety/approval path.

Acceptance:

- No hidden commands.
- No raw browser SSH.
- No command output reaches UI or Postgres before redaction.

### 6. Evidence Ledger

Every inspected source should appear in "Logs & files checked".

Record source types:

- `file`
- `journal`
- `service_status`
- `config`
- `metadata`
- `endpoint`
- `other`

Each evidence row stores:

- source/path
- command that inspected it
- purpose
- sanitized excerpt/finding
- redaction marker
- support type: hypothesis, root cause, fix choice, validation, context, or none
- linked command transcript

Acceptance:

- The technician can see all related logs/files/journal sources opened during the run.
- Activity root cause and validation claims can cite concrete evidence.

### 7. Backup and Rollback

Do not do full-machine backups by default.

Before persistent changes, create targeted rollback support:

- config file edit -> copy exact file to a run-specific backup path
- permission/ownership change -> record original owner/group/mode
- systemd setting change -> record current unit/enablement state
- config value change -> record checksum/sanitized diff where safe

Backup UI:

- "Backups & rollback" panel
- backup required yes/no
- backup created yes/no
- original path
- backup path
- restore command
- persistent across reboot yes/no
- redaction/content-storage marker

Safety:

- Block broad archives of `/`, `/etc`, `/home`, `/var`, `/srv`, database directories, customer data, private keys, and `.env` files.
- Backup and restore commands require normal approval and audit.

Acceptance:

- Every medium-risk persistent change references `backup_records` or explicit `backup.not_applicable`.

### 8. Fix Policy

Fixes must be evidence-backed, minimal, and persistent.

Preferred fixes:

- correct a specific config value
- restore missing service enablement
- correct exact ownership/mode for a proven path
- reload/restart only affected service
- restore a specific symlink/file path
- clean only safe, specific temporary/cache files when disk pressure is proven

Avoid:

- blanket `chmod`/`chown`
- broad recursive operations
- database reinitialization
- deleting customer data
- disabling security controls
- unnecessary installs
- unrelated restarts

Acceptance:

- Fix restores customer benefit and addresses root cause.
- Fix remains green after relevant restart/reload or reboot when explicitly approved.

### 9. Validation

Every run needs concrete validation before activity submission.

Validation should include:

- service health, such as `systemctl is-active <service>`
- customer benefit, such as local HTTP/API/port/process check
- recent logs no longer show original error
- persistence check after affected service restart/reload
- reboot only with explicit approval and only when useful

Acceptance:

- Validation result is concrete enough for Phoenix activity and grader review.

### 10. Activity Submission

Generate activity draft from:

- audit events
- command executions
- terminal chunks/summaries
- inspected evidence
- backups/rollback records
- validation events

Required Phoenix fields:

- `ticket_id`
- `start_datetime`
- `end_datetime`
- `summary`
- `root_cause`
- `actions_taken`
- `commands_summary`
- `validation_result`

The UI must let the technician edit all required text fields before submission.

Backend:

- `POST /api/runs/{run_id}/activity/draft`
- `POST /api/runs/{run_id}/activity/submit`
- Phoenix call: `POST /api/v1/activities/create`
- optional Phoenix call: `PATCH /api/v1/tickets/{ticket_id}/status` to `DONE`

Acceptance:

- Activity is not generic.
- Activity excludes secrets and noisy raw output.
- Activity describes diagnosis, fix, commands/classes, root cause, and validation.

## Backend Modules

Required modules:

- `config`: typed settings and secret-safe config handling.
- `phoenix_client`: Phoenix REST wrapper with auth, retries, timeouts, error mapping.
- `ssh_runner`: one approved non-interactive command per execution, timeout/output cap.
- `safety_layer`: command classification, hard blocks, output redaction.
- `agent_orchestrator`: plain worker planner functions and structured LLM calls.
- `audit_store`: append-only event writes and run timeline reads.
- `activity_generator`: Phoenix activity draft from logs/evidence.
- `backup_service`: targeted backup/rollback planning and records.
- `evidence_detector`: deterministic extraction of inspected sources from command patterns/output.
- `worker`: Postgres outbox processor.

## Backend API Surface

Frontend-facing API:

- `GET /health`
- `GET /api/me`
- `GET /api/tickets?status=&priority=&sort=`
- `GET /api/tickets/{ticket_id}`
- `GET /api/tickets/{ticket_id}/customer-system`
- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/events`
- `GET /api/runs/{run_id}/stream`
- `POST /api/runs/{run_id}/connect/approve`
- `POST /api/runs/{run_id}/steps/{step_id}/approve`
- `POST /api/runs/{run_id}/steps/{step_id}/edit-and-approve`
- `POST /api/runs/{run_id}/steps/{step_id}/reject`
- `POST /api/runs/{run_id}/manual-step`
- `POST /api/runs/{run_id}/retry`
- `POST /api/runs/{run_id}/abort`
- `POST /api/runs/{run_id}/activity/draft`
- `POST /api/runs/{run_id}/activity/submit`
- `PATCH /api/tickets/{ticket_id}/status`

## Postgres Tables

Core tables:

- `technician_cache`
- `tickets_cache`
- `customer_system_cache`
- `runs`
- `run_events`
- `outbox_events`
- `proposed_steps`
- `command_executions`
- `command_output_chunks`
- `inspected_sources`
- `backup_records`
- `activity_drafts`
- `integration_requests`
- `redaction_events`

Important constraints:

- command execution requires approved proposed step
- edited command must be reclassified
- blocked step cannot execute
- one active pending step per run
- output chunks reference existing command execution
- inspected source references command execution
- medium-risk persistent change references backup record or `backup.not_applicable`
- activity submission requires validation or explicit technician override event

## Safety Gates

Hard blocks:

- database deletion/reinitialization
- broad `rm -rf`
- broad `chmod -R 777`
- broad recursive `chmod`/`chown`
- disabling firewall/audit/security controls without evidence
- clearing logs/history
- reading private keys, `.env`, `/etc/shadow`, credential stores, token files
- piping remote scripts directly into shell
- disk formatting/partitioning/kernel/bootloader commands
- unrestricted root shells or long-running interactive sessions

Always enforce:

- command timeout
- output cap
- redaction before persistence/display
- exact approved command text
- approval after safety classification
- abort path from active states

## Implementation Steps

### Milestone 0: Project Wiring

- Add Postgres service to Compose.
- Add backend API and worker service definitions.
- Add `.env.example` entries for database, SSH, Phoenix, LLM, timeouts.
- Add migrations or schema bootstrap.

Done when:

- `docker compose up --build` starts frontend, backend, worker, and Postgres.
- `/health` reports backend and database status.

### Milestone 1: Phoenix ERP MVP

- Build `phoenix_client`.
- Backend ticket/me/customer-system endpoints.
- Frontend ticket overview/detail.
- Error/empty/auth states.

Done when:

- Tickets load and sort/filter.
- Customer system loads for selected ticket.

### Milestone 2: Run State, Events, and Audit

- Create `runs`, `run_events`, `outbox_events`.
- Start run and connection approval.
- Event timeline API.
- SSE or polling stream.

Done when:

- UI shows run status and approval state from Postgres events.

### Milestone 3: Safety and Manual Approved SSH

- Implement safety layer.
- Implement SSH runner.
- Implement command execution log.
- Implement manual command entry through propose/classify/approve/execute.
- Implement live terminal chunks.

Done when:

- Technician can manually run a read-only command over SSH after approval.
- Output streams live and is stored.
- Blocked command is visible but not executed.

### Milestone 4: Agent Planner

- Implement structured LLM adapter.
- Implement planner functions.
- Add deterministic fallback diagnostics.
- Validate Pydantic output.
- Propose one step at a time.

Done when:

- Agent proposes a safe diagnostic command from ticket/system context.
- Rejected/edited/retried commands behave correctly.

### Milestone 5: Evidence and Backup Ledgers

- Implement evidence source detection.
- Add "Logs & files checked" UI.
- Implement backup planning and records.
- Add "Backups & rollback" UI.

Done when:

- `journalctl`, `systemctl status`, config reads, metadata checks, and endpoints produce evidence rows.
- Medium-risk fix proposal includes backup/rollback status.

### Milestone 6: Fix and Validation Loop

- Let planner interpret observations.
- Let planner propose minimal fix.
- Require backup when applicable.
- Validate service and customer benefit.
- Validate persistence after affected service restart/reload.

Done when:

- One real or mocked incident can go from ticket -> diagnosis -> approved fix -> validation.

### Milestone 7: Activity Generation and Submission

- Draft required activity fields from logs/evidence.
- UI review/edit form.
- Phoenix activity submit.
- Optional ticket status update.

Done when:

- Phoenix receives complete activity schema with useful content and no secrets.

### Milestone 8: Hardening, Tests, Demo

- Tests for safety hard blocks, redaction, activity schema, outbox resume, fake SSH run, Phoenix errors.
- README setup/run/env/architecture/troubleshooting.
- Secret scan/manual check.
- Full demo rehearsal after Phoenix reset.

Done when:

- Full loop works from fresh reset.
- Demo shows ticket list, detail, agent progress, approval, live terminal, evidence ledger, backup panel, validation, activity submit.

## Testing Plan

Backend tests:

- Phoenix 401/404/422/timeout/empty cases.
- Safety hard blocks and allowed targeted commands.
- Redaction of token/password/private-key-like output.
- Unapproved commands cannot execute.
- Edited commands are reclassified.
- Aborted runs do not execute pending outbox rows.
- Output chunk persistence and truncation.
- Evidence detection from command patterns.
- Backup record required for medium-risk persistent changes.
- Activity draft has all required Phoenix fields.

Integration tests:

- Ticket list/detail with mocked Phoenix.
- Fake SSH run with command output streaming.
- Agent proposes diagnostic -> approval -> fake output -> next step.
- Activity submit payload matches OpenAPI schema.

Frontend/manual verification:

- Desktop and mobile visual checks for ticket list/detail/run console.
- Live terminal streaming.
- Logs & files checked panel.
- Backups & rollback panel.
- Approve/edit/reject/retry/abort.
- Activity review and submit.

## Demo Script

1. Open app.
2. Show ticket list sorted by date.
3. Open a ticket.
4. Show customer system info.
5. Start troubleshooting.
6. Approve SSH connection.
7. Show AI hypothesis and proposed diagnostic.
8. Approve command.
9. Show live terminal output.
10. Show "Logs & files checked" row from the command.
11. Approve targeted backup if fix changes persistent state.
12. Approve fix command.
13. Show validation command and output.
14. Show activity draft generated from run logs.
15. Submit activity to Phoenix.
16. Optionally mark ticket `DONE`.

## Final Acceptance Checklist

- Loads Phoenix tickets.
- Loads customer system info.
- Runs from one Docker Compose stack.
- No frontend secrets.
- SSH key stays backend/worker only.
- Every SSH command has proposed step, safety verdict, approval event, execution log, and terminal transcript.
- Blocked dangerous commands are logged and not executed.
- Agent never executes commands directly.
- Live terminal streams sanitized output.
- Every checked log/file/source appears in evidence ledger.
- Persistent fixes have targeted backup or explicit backup-not-applicable record.
- Validation proves customer benefit.
- Activity includes all required fields.
- README and `.env.example` are complete.
- Tests/mocks are runnable.
- No secrets committed.

## Mentor Questions

Ask only if mentors are available:

- Is there any VM snapshot API, or only SSH/reset endpoint?
- Should status be patched to `DONE` after activity submission for grading?
- Is reboot validation expected for every incident or only when persistence is uncertain?
- Are package installs ever expected, or should we avoid package management unless evidence is decisive?
