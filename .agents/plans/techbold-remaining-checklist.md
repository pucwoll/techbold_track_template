# TechBold Full-Spec Remaining Checklist

Audit date: 2026-06-06
Source spec: `.agents/plans/techbold-final-spec.md`

This checklist covers the whole final implementation spec, not just the last phases. It is based on static inspection of the current repository: Docker Compose, FastAPI backend, worker, run store, safety layer, Phoenix client, SSH runner, activity/evidence/backup modules, React frontend, README, `.env.example`, and existing tests.

## Baseline Already Present

- Docker Compose includes `postgres`, `backend-api`, `worker`, and `frontend`.
- `.env.example` includes Phoenix, SSH, database, timeout, output-limit, and optional LLM settings.
- FastAPI exposes the main ticket, customer-system, run, event stream, manual command, backup, validation, activity, and ticket-status endpoints.
- React/Vite frontend shows ticket overview/detail, run console, event/live terminal panels, evidence, backups, validation, and activity review.
- Postgres-backed store exists for `runs`, `run_events`, `outbox_events`, `proposed_steps`, `command_executions`, `command_output_chunks`, `inspected_sources`, `validation_results`, `backup_records`, and `activity_drafts`.
- The worker processes planner and command-execution outbox events.
- Safety classification, command approval, output redaction, inspected-source detection, backup records, validation results, and validation-gated activity submission have first-pass implementations.

## Milestone 0: Project Wiring

- [x] Add a repeatable Compose smoke test or script that runs `docker compose up --build` from a clean checkout and proves frontend, backend API, worker, and Postgres start.
- [x] Document the expected `/health` response, including database configured/reachable state and Phoenix configured state.
- [x] Add CI or local verification for backend image build, frontend image build, and worker startup.
- [x] Replace the current schema-bootstrap-only approach with explicit migrations or a documented idempotent migration runner for upgrades on an existing database.
- [x] Update README language that still describes the app as a starter skeleton so it reflects the implemented architecture and operational workflow.
- [x] Add README troubleshooting for common Compose failures: missing `.env`, missing SSH key mount, Phoenix unavailable, database unavailable, and port conflicts.

## Milestone 1: Phoenix ERP MVP

- [x] Add Postgres cache tables required by the spec: `technician_cache`, `tickets_cache`, and `customer_system_cache`.
- [x] Add cache read/write behavior for `/api/me`, `/api/tickets`, `/api/tickets/{ticket_id}`, and `/api/tickets/{ticket_id}/customer-system`.
- [x] Define what the UI/API should do when Phoenix is unavailable but cache exists versus unavailable with no cache.
- [x] Add response-shape validation for Phoenix responses against the fields the app consumes.
- [x] Add Phoenix tests for 401, 404, 422, timeout, malformed JSON, empty body, and backend-unavailable states.
- [x] Add frontend tests or e2e checks for loading, empty, 401, 404, and backend-unavailable states in ticket overview and ticket detail.
- [x] Verify ticket sorting/filtering against Phoenix contract and document supported `status`, `priority`, and `sort` values.
- [x] Confirm no Phoenix token or backend-only error detail can be exposed to frontend responses.

## Milestone 2: Run State, Events, And Audit

- [x] Add or document an `audit_store` boundary, since the spec calls it out separately and current audit behavior is consolidated inside `run_store.py`.
- [x] Add database-level constraints for allowed run status values, proposed step status values, command execution status values, and event relationships beyond the current application checks.
- [x] Enforce one active pending/running proposed step per run at the database or transaction level.
- [x] Add transaction checks so blocked, rejected, aborted, or stale steps cannot execute even if an old outbox row remains.
- [x] Add outbox recovery for stale `processing` rows after worker crashes.
- [x] Add retry/backoff scheduling for recoverable outbox failures.
- [x] Add API/UI visibility for failed or dead-letter outbox events.
- [x] Add tests for SSE replay with `after_id`, polling fallback behavior, and event ordering.
- [x] Add integration tests proving run state and event timeline survive backend and worker restarts.

## Milestone 3: Safety And Manual Approved SSH

- [x] Broaden safety parsing for shell composition, pipes, redirection, command substitution, unsafe globbing, and multi-command forms.
- [x] Add hard-block tests for all safety-gate examples in the final spec, including disk formatting, bootloader/kernel commands, clearing logs/history, remote script piping, unrestricted shells, and credential-store reads.
- [x] Add a `redaction_events` table and write records whenever stdout, stderr, evidence, or activity content is redacted.
- [x] Add audit events for every safety classification and block reason, not only approval/execution outcomes.
- [x] Improve SSH host-key handling with configurable known-hosts policy instead of a shared `/tmp` default.
- [x] Add SSH-runner tests for timeout, nonzero exit, stderr streaming, chunk order, output cap, cancellation/abort, and command text fidelity.
- [x] Add manual command phase selection in the UI so diagnostics, fixes, and validation manual steps are intentional.
- [x] Add manual timeout editing in the UI where supported by the backend.
- [x] Show blocked commands clearly in the UI with the exact reason and no execution action.

## Milestone 4: Agent Planner

- [x] Implement planner functions named as specified: `ticket_analyzer`, `system_context_planner`, `observation_interpreter`, `fix_planner`, `validation_planner`, and `activity_writer`.
- [x] Make planner phase transitions explicit instead of relying mostly on generic planning plus inferred step phase.
- [x] Ensure planner context always includes ticket snapshot, customer system snapshot, sanitized timeline, latest evidence, backup state, validation state, and safety rules.
- [x] Require structured LLM output to include evidence references or explain why more diagnosis is needed before fixes.
- [x] Add retry behavior and audit events for invalid LLM output before deterministic fallback.
- [x] Expand deterministic fallback diagnostics beyond the current narrow service/web-server patterns.
- [x] Add tests for rejected, edited, retried, blocked, invalid-output, and fallback planner paths.
- [x] Add tests proving the agent never directly executes SSH and can only create a proposed step.

## Milestone 5: Evidence And Backup Ledgers

- [x] Expand evidence detection coverage for all spec source types: `file`, `journal`, `service_status`, `config`, `metadata`, `endpoint`, and `other`.
- [x] Link every evidence row to the exact command transcript and expose that link in the UI.
- [x] Add tests for evidence detection from `journalctl`, `systemctl status`, config reads, metadata checks, endpoint checks, and unknown/other sources.
- [x] Make activity root-cause and validation claims cite concrete inspected-source IDs, not only generated prose.
- [x] Add first-class targeted backup planning before persistent edits, instead of only detecting backup records after backup-like commands.
- [x] Generate run-specific backup paths and technician-readable restore commands before approval of persistent fixes.
- [x] Record metadata snapshots for file ownership, group, mode, size, mtime, and checksum where relevant.
- [x] Record service state snapshots before enablement, restart, reload, or service-state changes.
- [x] Record sanitized config diffs where useful and safe.
- [x] Add restore command proposal, approval, execution, and audit workflow.
- [x] Add frontend action for `backup.not_applicable`.
- [x] Show backup state on pending fix approval cards: required, created, not applicable, missing, or blocked.
- [x] Block broad archive/backup attempts over `/`, `/etc`, `/home`, `/var`, `/srv`, database directories, customer data, private keys, and `.env` files.

## Milestone 6: Fix And Validation Loop

- [x] Make the run workflow an explicit state machine for `investigating -> fixing -> validating -> ready_for_activity`, including allowed transitions and rejected transitions.
- [x] Require evidence-backed minimal fixes: a fix proposal should reference root-cause or fix-choice evidence before approval.
- [x] Strengthen fix policy enforcement for blanket `chmod`/`chown`, broad recursive operations, database reinitialization, customer-data deletion, disabling security controls, unnecessary installs, and unrelated restarts.
- [x] Persist validation expectations per run or per fix, including check type, target, expected result, and relation to customer symptom.
- [x] Require a validation suite before `ready_for_activity`, not just the first passing validation command.
- [x] Include service health validation when a service is involved.
- [x] Include customer-benefit validation such as local HTTP/API/port/process checks when the ticket has a customer-facing symptom.
- [x] Include recent-log validation showing the original error is absent or materially reduced.
- [x] Include persistence validation after affected service restart/reload when config or service state changed.
- [x] Add technician-approved reboot validation only when useful and explicitly approved.
- [x] Keep failed validation in `validating` and require a new fix/validation loop before activity generation.
- [x] Add UI state that shows required validation checks and their pass/fail status.
- [x] Add one real or mocked incident fixture that goes ticket -> diagnosis -> backup decision -> approved fix -> validation.

## Milestone 7: Activity Generation And Submission

- [x] Add an `integration_requests` table for durable Phoenix activity and ticket-status writes.
- [x] Submit Phoenix activity through a run-scoped durable worker/outbox flow, not synchronously inside the API request only.
- [x] Patch ticket status to `DONE` through the same durable integration flow after activity creation succeeds.
- [x] Handle partial Phoenix success, such as activity created but ticket status patch failed, with retryable state and visible UI status.
- [x] Keep final run status distinct for integration failure versus fully submitted, or add clear integration status fields.
- [x] Make activity generation cite concrete event IDs, command execution IDs, inspected-source IDs, backup record IDs, and validation result IDs.
- [x] Avoid generic fallback activity text when evidence is insufficient; require technician review or more evidence instead.
- [x] Validate required Phoenix activity fields at the API schema boundary with non-empty strings.
- [x] Add explicit draft update/save semantics before submit.
- [x] Record audit events when a technician edits an activity draft.
- [x] Ensure activity content excludes secrets and noisy raw output even after technician edits.
- [x] Refresh ticket list/detail in the frontend after successful activity submission and status update.
- [x] Add contract tests proving submitted activity payload matches `docs/phoenix-openapi.yaml`.

## Milestone 8: Hardening, Tests, Demo

- [x] Add backend tests for every hard-block command family listed in the safety gates.
- [x] Add backend tests for redaction events and no-secret persistence/display guarantees.
- [x] Add backend tests for unapproved commands, edited-command reclassification, blocked-command non-execution, and aborted-run non-execution.
- [x] Add backend tests for output chunk persistence, truncation, stderr handling, and ordering.
- [x] Add Postgres integration tests for append-only run events, outbox `SKIP LOCKED`, stale processing recovery, and activity gating.
- [x] Add fake SSH integration tests for command output streaming and worker lifecycle.
- [x] Add planner integration tests for diagnostic -> approval -> fake output -> next step.
- [x] Add Phoenix integration/contract tests for activity submit payload and error handling.
- [x] Add frontend/manual verification artifacts for desktop and mobile ticket list/detail/run console.
- [x] Add frontend e2e coverage for live terminal streaming, logs/files checked, backups/rollback, approve/edit/reject/retry/abort, activity review, and submit.
- [x] Run and document a full demo rehearsal after Phoenix reset.
- [x] Add README setup, run, environment, architecture, troubleshooting, and demo workflow sections for the implemented app.
- [x] Run and document a secret scan/manual secret check before final handoff.
- [x] Add a final acceptance matrix mapping each spec acceptance item to a test, demo step, or source file.

## Backend API Surface Gaps

- [x] Document all frontend-facing API endpoints in README or generated OpenAPI notes.
- [x] Confirm `/api/runs/{run_id}/stream` has polling fallback behavior documented and tested.
- [x] Add a command output chunks endpoint to the spec or remove it if it is not meant to be frontend-facing.
- [x] Add API for integration request status if Phoenix submission moves to the worker.
- [x] Add API for dead-letter outbox visibility.
- [x] Add API or event semantics for restore proposals and restore execution.

## Postgres Table And Constraint Gaps

- [x] Add `technician_cache`.
- [x] Add `tickets_cache`.
- [x] Add `customer_system_cache`.
- [x] Add `integration_requests`.
- [x] Add `redaction_events`.
- [x] Decide whether `validation_results` should be added to the final spec table list or folded into `run_events`, then update the spec.
- [x] Add constraints ensuring command execution requires an approved proposed step.
- [x] Add constraints ensuring edited commands are reclassified before approval.
- [x] Add constraints ensuring blocked steps cannot execute.
- [x] Add constraints ensuring output chunks reference an existing command execution.
- [x] Add constraints ensuring inspected sources reference command executions.
- [x] Add durable representation for backup-not-applicable decisions if the backup requirement remains enforced outside event history.
- [x] Resolve explicit validation override handling: final spec now requires a completed validation suite instead of allowing an override bypass.

## Final Acceptance Checklist Gaps

- [ ] Prove Phoenix tickets load from a fresh environment.
- [ ] Prove customer system info loads from a fresh environment.
- [ ] Prove the app runs from one Docker Compose stack.
- [ ] Prove no frontend secrets are bundled or returned by API responses.
- [ ] Prove the SSH key stays backend/worker only.
- [ ] Prove every SSH command has proposed step, safety verdict, approval event, execution log, and terminal transcript.
- [ ] Prove blocked dangerous commands are logged and not executed.
- [ ] Prove the agent never executes commands directly.
- [ ] Prove live terminal output is sanitized before display.
- [ ] Prove every checked log/file/source appears in the evidence ledger.
- [ ] Prove persistent fixes have targeted backup or explicit backup-not-applicable records.
- [ ] Prove validation demonstrates customer benefit, not only service process health.
- [ ] Prove activities include all required fields.
- [ ] Prove README and `.env.example` are complete for the implemented app.
- [ ] Prove tests and mocks are runnable from a clean checkout.
- [ ] Prove no secrets are committed.
