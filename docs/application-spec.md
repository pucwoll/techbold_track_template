# AI Service Desk Autopilot Application Spec

## Purpose

Build a technician-controlled AI workspace that completes the full techbold case loop:

1. Load assigned Phoenix ERP tickets.
2. Load the affected customer system and SSH target for a selected ticket.
3. Diagnose the Linux incident with AI assistance.
4. Propose one explicit system action at a time.
5. Require technician approval before every SSH command.
6. Execute approved commands through a safety layer.
7. Validate that the customer-facing problem is fixed and persistent.
8. Submit a precise activity log back to Phoenix ERP.
9. Mark the ticket complete when the technician accepts the result.

The application is optimized for the scoring rubric. The highest priorities are real troubleshooting performance, safety, auditability, and a reliable end-to-end demo.

## Non-Goals

- No fully autonomous repair mode.
- No production multi-tenant hardening.
- No mobile/native app.
- No hard-coded fixes for known demo VMs.
- No direct browser access to Phoenix credentials, SSH keys, or LLM keys.
- No broad package installation or invasive host modification unless explicitly justified and approved.

## Assumptions

- Phoenix ERP base URL, bearer token, SSH private key, and VM credentials are provided externally.
- Customer systems are Ubuntu Linux and incidents are local service problems solvable through shell access.
- The private key is mounted read-only into the backend container.
- We will bring our own LLM credentials if using an LLM.
- A deterministic fallback workflow is still needed so the app can make progress when the LLM is slow or returns unsafe output.

## Product Workflow

### 1. Ticket Overview

The first screen is a technician workspace, not a landing page.

Required behavior:

- Fetch `GET /api/v1/me` to show the logged-in technician context.
- Fetch `GET /api/v1/me/tickets`.
- Show ticket title, customer, priority, status, creation date, SLA date when present, and tags.
- Default sort by date.
- Support sorting or filtering by status, priority, and date.
- Handle empty, loading, 401, 404, and backend unavailable states without breaking the flow.

Scoring coverage:

- Functional MVP ticket loading, usable list, sorting/filtering.
- Technician overview clarity.

### 2. Ticket Detail

When the technician opens a ticket:

- Fetch the full ticket details.
- Fetch `GET /api/v1/tickets/{ticket_id}/customer-system`.
- Display the customer name, ticket report, priority, status, SSH hostname/IP, SSH port, username, OS, and system notes.
- Do not display private keys or secrets.
- Show a clear `Start troubleshooting` action.

Scoring coverage:

- Customer system loading.
- Detail view with system information.

### 3. Start Troubleshooting Run

Starting a run creates a backend run record with:

- Ticket snapshot.
- Customer system snapshot.
- Start timestamp.
- Run status: `planning`, `awaiting_approval`, `running`, `validating`, `ready_for_activity`, `submitted`, `aborted`, or `failed`.
- Audit event stream.

The first AI/planner task is to produce:

- A concise problem interpretation.
- A ranked hypothesis list.
- A safe initial diagnostic plan.
- The first proposed command, if appropriate.

The app must ask for technician approval before connecting to or running commands on the VM.

Scoring coverage:

- Visible agent progress.
- Human control.
- Complete audit trail.

### 4. Human Approval Loop

The core loop is:

1. Agent proposes exactly one next SSH action.
2. Safety layer classifies the action.
3. UI shows command, purpose, expected evidence, risk level, target, timeout, and safety notes.
4. Technician can approve, edit, reject, retry, or abort.
5. Backend executes only the approved command text.
6. Sanitized result is appended to the audit log.
7. Agent uses the latest evidence to propose the next step.

Commands are never sent directly from the LLM to SSH. The backend always mediates through safety checks and the technician approval state.

Scoring coverage:

- Human confirmation for every action.
- Followable logs and actions.
- Review, retry, abort.
- Safety and auditability.

### 5. Diagnosis Strategy

The agent should generalize across hidden service incidents instead of using hard-coded scenarios.

Initial read-only discovery should favor commands such as:

- `hostnamectl`, `uname -a`, `uptime`
- `systemctl --failed`
- `systemctl status <suspected-service>`
- `journalctl -u <service> --no-pager -n 80`
- `ss -ltnp` or targeted port checks
- `curl -i http://localhost:<port>` for local HTTP services
- `df -h`, `free -m`
- targeted config validation, such as `nginx -t` or equivalent when the service is known
- `ls -la` and `stat` for exact files/directories implicated by logs

The agent should infer service candidates from:

- Ticket title and description.
- Customer system notes.
- Open/listening ports.
- Failed systemd units.
- Recent service logs.
- Config validation errors.
- Process and file ownership evidence.

The agent should produce a ranked diagnosis with evidence before proposing a fix.

### 6. Fix Strategy

Fixes should be minimal and persistence-aware.

Preferred fix patterns:

- Correct a specific broken config value and validate syntax before restart.
- Restore a missing service enablement when service should start at boot.
- Fix exact file or directory ownership/permissions when logs prove a permission problem.
- Restart or reload only the affected service after a validated config or state change.
- Restore an expected symlink or file path only when evidence identifies it.
- Free space only from safe, specific, non-customer-data caches or temporary files when disk pressure is proven.

Avoid:

- Blanket permission changes.
- Recursive changes over broad system paths.
- Deleting customer data.
- Reinitializing databases.
- Disabling security controls.
- Installing packages without strong evidence and technician approval.
- Restarting unrelated services.
- Making changes before collecting enough evidence.

### 7. Validation Strategy

Every run must end with concrete validation before activity submission.

Validation should include:

- Service health: `systemctl is-active <service>` or equivalent.
- Customer benefit: local HTTP/API request, port response, process health, or service-specific command.
- Error absence: recent logs no longer show the triggering failure.
- Persistence: restart or reload the affected service and rerun the customer-benefit check. If appropriate and time allows, use reboot validation only with explicit technician approval.

The validation result must be specific enough to earn activity review credit.

Example validation language:

> `nginx -t` passed, `systemctl restart nginx` completed successfully, `systemctl is-active nginx` returned active, and `curl -I http://localhost` returned HTTP 200 after restart.

## Backend Architecture

The backend is the control plane. It owns secrets, external integrations, safety checks, audit storage, and the troubleshooting run state.

The detailed architecture is specified in [architecture-spec.md](architecture-spec.md). The short version: use Postgres as the durable run, audit, command, activity, and event-outbox store; run a backend API plus a worker from the same codebase; drive the troubleshooting loop through append-only events so every approval, command, result, validation, and ERP submission is reconstructable for the jury.

### Proposed Modules

`config`

- Load `.env` values with typed settings.
- Validate required Phoenix and SSH settings at startup or first use.
- Never expose secret values through API responses.

`phoenix_client`

- Wrap Phoenix REST API calls.
- Add bearer authentication.
- Implement timeouts, retries for transient failures, and clear error mapping.
- Methods: `get_me`, `list_tickets`, `get_ticket`, `get_customer_system`, `create_activity`, `set_ticket_status`, `reset`.

`ssh_runner`

- Open SSH connections using the configured private key.
- Execute one approved command per request.
- Enforce command timeout, output size limit, and non-interactive execution.
- Return exit code, stdout, stderr, duration, and connection errors.
- Never accept arbitrary private key material from the frontend.

`safety_layer`

- Classify commands before execution.
- Block hard-fail patterns.
- Redact secret-looking output.
- Attach a safety verdict and explanation to every proposed command.

`agent_orchestrator`

- Own the troubleshooting state machine.
- Converts ticket, system context, audit evidence, and safety feedback into the next proposed step.
- Can use an LLM, deterministic playbooks, or both.
- Emits structured proposed actions, not raw free-form instructions.

`audit_store`

- Persist run events.
- Store sanitized command output.
- Keep original command text, actor, approval state, timestamps, risk class, exit code, and summaries.
- Use Postgres append-only event tables for durability and auditability.
- Never mutate or delete audit events during a run; only append corrective events when state changes.

`activity_generator`

- Generate a complete Phoenix activity draft from the audit trail.
- Ensure required fields are populated: `summary`, `root_cause`, `actions_taken`, `commands_summary`, `validation_result`.
- Strip secrets and irrelevant raw output.

### Backend API Surface

The frontend should call our backend, not Phoenix directly.

Recommended endpoints:

- `GET /health`
- `GET /api/me`
- `GET /api/tickets?status=&priority=&sort=`
- `GET /api/tickets/{ticket_id}`
- `GET /api/tickets/{ticket_id}/customer-system`
- `POST /api/runs`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/connect/approve`
- `POST /api/runs/{run_id}/steps/{step_id}/approve`
- `POST /api/runs/{run_id}/steps/{step_id}/edit-and-approve`
- `POST /api/runs/{run_id}/steps/{step_id}/reject`
- `POST /api/runs/{run_id}/retry`
- `POST /api/runs/{run_id}/abort`
- `POST /api/runs/{run_id}/activity/draft`
- `POST /api/runs/{run_id}/activity/submit`
- `PATCH /api/tickets/{ticket_id}/status`

Server-sent events should be used for run progress when feasible, with polling as a fallback. Both should read from the same Postgres-backed run event stream.

## Safety Policy

### Command Risk Classes

`READ_ONLY`

- Inspection commands.
- No filesystem writes, service restarts, package changes, permission changes, or network/security modifications.

`LOW_RISK`

- Targeted service restart/reload.
- Targeted config test.
- Targeted file read of non-secret config or logs.

`MEDIUM_RISK`

- Targeted config edit.
- Targeted ownership or permission correction.
- Targeted cleanup of confirmed temporary/cache files.
- Enabling a known service for persistence.

`BLOCKED`

- Commands matching hard-fail or secret-exposure patterns.
- Commands that are too broad to assess.
- Interactive shells or privilege escalation sessions.
- Obfuscated command strings.

### Hard Blocks

The safety layer must block commands that:

- Delete or reinitialize databases.
- Run broad `rm -rf` against `/`, `/etc`, `/home`, `/var`, `/srv`, database directories, or unknown broad paths.
- Run broad `chmod -R 777` or similar permission opening on system/customer data paths.
- Run broad recursive `chown` or `chmod` without an exact narrow target and reason.
- Disable firewall, audit, or security controls without an explicit validated need.
- Clear logs, shell history, or audit trails.
- Read private keys, `.env` files, `/etc/shadow`, credential stores, or token files.
- Pipe remote scripts directly into a shell.
- Run disk formatting, partitioning, kernel, bootloader, or destructive storage commands.
- Start an unrestricted root shell or long-running interactive session.

### Output Redaction

Before storing or displaying output, redact:

- API tokens and bearer tokens.
- Password assignments.
- Private key blocks.
- SSH key material.
- Environment lines containing `SECRET`, `TOKEN`, `PASSWORD`, `KEY`, or `CREDENTIAL`.
- Connection strings containing embedded passwords.

The activity generator should summarize command classes and relevant findings, not paste full command output.

## Agent Design

Use a deterministic orchestrator with optional LLM reasoning. The detailed LLM design is specified in [agent-spec.md](agent-spec.md).

### Why This Shape

- The LLM is useful for diagnosis and summarization.
- The deterministic orchestrator is useful for safety, state, retries, and reproducible behavior.
- The LLM never receives authority to execute a command.

### Agent Inputs

- Ticket title, description, priority, tags.
- Customer system OS and notes.
- Sanitized audit history.
- Latest command result.
- Safety policy summary.
- Required activity schema.

### Agent Output Contract

Each proposed step should contain:

- `type`: `diagnostic`, `fix`, or `validation`.
- `command`: exact shell command.
- `purpose`: why this command is needed.
- `expected_signal`: what output would confirm or reject the hypothesis.
- `risk`: proposed risk level.
- `requires_restart`: boolean.
- `rollback_or_recovery`: how to undo or recover if applicable.
- `stop_if`: conditions where the run should pause instead of continuing.

The backend validates this structure and passes the command to the safety layer before the technician sees it.

### Stop Conditions

The run should pause or fail safely when:

- The safety layer blocks the proposed command.
- The agent proposes repeated low-value commands.
- Three consecutive commands fail without new evidence.
- SSH connection fails repeatedly.
- The action would require destructive data changes.
- The technician aborts.
- Required Phoenix or SSH configuration is missing.

## Frontend Experience

### Layout

Use a dense, operational technician UI:

- Left pane: ticket list with filters and sorting.
- Main pane: selected ticket details and customer system.
- Right or lower pane: troubleshooting run console.
- Activity review modal or panel at the end.

### Run Console

The run console should show:

- Current run status.
- Agent reasoning summary.
- Ranked hypotheses.
- Pending proposed command card.
- Safety verdict.
- Approve, edit and approve, reject, retry, abort controls.
- Live browser terminal showing approved commands and streamed sanitized stdout/stderr.
- "Logs & files checked" panel listing every inspected log source, file, journal stream, service status, config, metadata check, and endpoint validation used as evidence.
- "Backups & rollback" panel showing targeted pre-change backups, backup paths, restore commands, and backup applicability.
- Timeline of audit events.
- Completed command transcripts with expand/collapse.
- Validation status.

The live terminal is specified in [live-terminal-spec.md](live-terminal-spec.md). It is a terminal-style transcript and streaming viewer, not a raw interactive shell. Manual commands entered by the technician still go through safety classification and approval before execution.

The checked-logs/evidence panel is specified in [evidence-log-spec.md](evidence-log-spec.md). It should show the path/source, command, purpose, sanitized finding, redaction marker, timestamp, and linked terminal transcript for every related log or file the run inspected.

The backup policy is specified in [backup-policy-spec.md](backup-policy-spec.md). Full machine backups are not part of the default flow; before persistent changes, the app creates targeted backups or rollback metadata for exactly the file, config, service setting, or permission it will change.

### Activity Review

Before submission, show editable fields:

- Summary.
- Root cause.
- Actions taken.
- Commands summary.
- Validation result.

The UI should warn if any required field is empty or too generic.

## Data Model

Minimal run record:

- `id`
- `ticket_id`
- `status`
- `started_at`
- `ended_at`
- `ticket_snapshot`
- `customer_system_snapshot`
- `current_hypotheses`
- `pending_step`
- `validation_result`
- `activity_draft`

Minimal audit event:

- `id`
- `run_id`
- `timestamp`
- `actor`: `technician`, `agent`, `safety_layer`, `ssh_runner`, `phoenix`
- `event_type`
- `summary`
- `command`
- `sanitized_stdout`
- `sanitized_stderr`
- `exit_code`
- `duration_ms`
- `risk_class`
- `approval_status`
- `error`

Minimal inspected source record:

- `id`
- `run_id`
- `command_execution_id`
- `source_type`: `file`, `journal`, `service_status`, `config`, `metadata`, `endpoint`, or `other`
- `source_name`
- `path`
- `command`
- `purpose`
- `finding`
- `supports`: `hypothesis`, `root_cause`, `fix_choice`, `validation`, `context`, or `none`
- `sanitized_excerpt`
- `redacted`
- `created_at`

## Testing Strategy

Tests should focus on scoring-critical behavior.

Backend unit tests:

- Phoenix client handles auth, 404, 422, empty tickets, and timeouts.
- Safety layer blocks hard-fail commands.
- Safety layer allows targeted low-risk commands.
- Redactor removes secret-looking values.
- Activity generator always emits all required fields.
- SSH runner handles timeout, non-zero exit, output limits, and connection failure.

Integration tests:

- Ticket list flow using mocked Phoenix responses.
- Full run with fake SSH runner and deterministic agent steps.
- Approve/edit/reject/abort transitions.
- Activity submission payload matches Phoenix schema.

Frontend tests or manual verification checklist:

- Empty ticket list.
- Auth/backend error.
- Ticket detail with customer system.
- Pending command approval.
- Live terminal shows command output while a command is running.
- Manual terminal command enters the approval flow instead of executing directly.
- Logs/files checked panel shows every inspected source and links it to the command transcript.
- Rejected command.
- Aborted run.
- Activity review and submit.

## README and Reproducibility Requirements

The README should document:

- Docker setup.
- Local backend and frontend setup.
- `.env` variables.
- SSH key placement.
- How to run tests.
- How to reset Phoenix/VMs.
- Architecture diagram or module overview.
- Known assumptions.
- Troubleshooting common failures.

The repo should include:

- `.env.example` with no secrets.
- No committed `.env`.
- No private key files.
- No sensitive screenshots.
- MIT license at root.

## Build Plan

### Milestone 1: ERP MVP

- Backend Phoenix client.
- Ticket list and ticket detail API.
- Frontend ticket overview and detail.
- Error and empty states.

This secures most category A points early.

### Milestone 2: SSH, Safety, and Audit

- SSH runner with timeouts.
- Safety classifier and hard blocks.
- Audit event store.
- Manual command approval UI.

This creates the required control plane before any AI complexity.

### Milestone 3: Agent Loop

- Structured planner that proposes one command at a time.
- Initial read-only diagnostics.
- Evidence-based fix proposal.
- Validation proposal.
- Retry and abort handling.

This targets category B while preserving category C.

### Milestone 4: Activity Submission

- Activity draft generation from audit.
- Technician edit screen.
- Phoenix `POST /api/v1/activities/create`.
- Optional ticket status update to `DONE`.

This completes the judged loop.

### Milestone 5: Hardening and Demo

- Add tests and mocks.
- Improve error messages.
- Add README details.
- Run reset and full demo flow repeatedly.
- Verify no secrets in repo, logs, UI, or screenshots.

## Scoring Checklist

### A: Functional MVP and ERP Workflow

- Tickets load from Phoenix.
- Ticket list has title, customer, priority, status.
- Sort/filter by date, priority, status.
- Customer system loads for selected ticket.
- Complete activity schema can be submitted.
- Auth, 404, empty, and backend errors are handled.

### B: Troubleshooting Performance

- Agent identifies technical root cause, not just symptoms.
- Fix restores customer-facing behavior.
- Fix persists after restart/reload or reboot when appropriate.
- No customer data loss or unrelated regressions.
- Activity is technically useful.

### C: Safety, Auditability, Responsible AI

- Every command and key action is logged.
- Dangerous blanket commands are blocked.
- Secrets are redacted from UI, logs, activity, and screenshots.
- Changes are minimal and proportional.
- Technician can approve, edit, reject, retry, and abort.

### D: Technician Experience

- Ticket overview is clear.
- Detail view includes system info.
- Agent progress is visible.
- Logs and actions are followable.
- Human controls are always available.

### E: Engineering Quality

- Backend and frontend remain separated.
- Modules are understandable and testable.
- README is complete.
- Tests/mocks are runnable.
- Timeouts and retries exist for Phoenix, SSH, and AI.
- `.env` and secrets are handled safely.

## Open Questions for Mentors

These are useful but not blocking:

- Should activity submission also require setting ticket status to `DONE`, or is activity creation enough for grading?
- Are hidden incidents limited to systemd-managed services, or can they include cron jobs and one-shot scripts?
- Is reboot validation expected for every solved incident, or only when persistence is uncertain?
- Are package installs allowed when a missing package is the root cause, or should fixes avoid package management unless clearly required?
- Is there a maximum command runtime or total evaluation time the grader expects?
