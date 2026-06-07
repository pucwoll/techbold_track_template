# Library Replacement Implementation Checklist

Goal: replace custom plumbing with popular, maintained libraries while keeping the product-specific safety, audit, approval, and troubleshooting rules explicit.

## Guiding Rules

- [x] Keep custom domain logic for technician approval, command safety policy, audit event vocabulary, evidence requirements, backup policy, validation gates, and Phoenix activity content.
- [x] Replace repeated infrastructure code first: fetching, forms, styling primitives, database mapping, migrations, HTTP clients, SSH execution, and agent workflow plumbing.
- [x] Keep changes incremental and test each phase before starting the next one.
- [x] Prefer project-local and `mise exec -- ...` tooling.
- [x] Update docs and tests in the same phase as each library migration.

## Phase 0 - Baseline Before Replacing Code

- [x] Run backend tests and record the current passing/failing state.
- [x] Run frontend build and Playwright checks and record the current passing/failing state.
- [x] Run the Compose smoke test and record the current passing/failing state.
- [x] Add a short architecture note explaining that third-party libraries replace plumbing, not safety policy.
- [x] Add dependency review criteria: active maintenance, broad adoption, clear docs, type support, and minimal extra infrastructure.

Architecture note: libraries replace infrastructure plumbing only. The safety policy, approval gates, append-only audit model, backup policy, validation requirements, and Phoenix activity rules remain product-owned code.

Dependency criteria: keep libraries only when they are actively maintained, broadly adopted, well documented, type-friendly, testable in this Compose stack, and do not add infrastructure that weakens the demo or audit story.

Current verification: backend unit tests pass, frontend build passes with generated API type freshness checks, Playwright passes, and the Docker Compose smoke stack passes.

## Frontend - Styling And Components

### Tailwind CSS

- [x] Install and configure `tailwindcss`, `@tailwindcss/vite` or the current Vite-compatible Tailwind setup, and `tailwind-merge`.
- [x] Replace global layout/color/spacing CSS variables with Tailwind theme tokens.
- [x] Convert app shell, topbar, workspace grid, ticket rail, detail pane, and run console styles from `frontend/src/index.css` to Tailwind classes.
- [x] Preserve responsive behavior for desktop, tablet, and mobile layouts.
- [x] Remove replaced CSS rules from `frontend/src/index.css`.
- [x] Verify desktop and mobile screenshots after conversion.

### shadcn/ui And Radix

- [x] Initialize shadcn/ui.
- [x] Add shadcn components for `Button`, `Badge`, `Card`, `Input`, `Textarea`, `Select`, `Label`, `Separator`, `ScrollArea`, `Skeleton`, `Alert`, `Tabs`, `Dialog`, and `Tooltip`.
- [x] Replace custom buttons and icon buttons with shadcn `Button` variants.
- [x] Replace status/risk/priority pills with shadcn `Badge`.
- [x] Replace manual form fields with shadcn `Input`, `Textarea`, `Select`, and `Label`.
- [x] Replace loading placeholders with shadcn `Skeleton`.
- [x] Replace error blocks with shadcn `Alert`.
- [x] Add `lucide-react` icons for refresh, approve, abort, retry, queue, terminal, ticket, validation, backup, and activity actions.
- [x] Keep the operational UI dense and scan-friendly; avoid landing-page styling.
- [x] Verify keyboard focus states and accessible labels.

## Frontend - Data, State, And API Contracts

### TanStack Query

- [x] Install `@tanstack/react-query` and `@tanstack/react-query-devtools`.
- [x] Add a `QueryClientProvider` at the React root.
- [x] Move the custom fetch helper into a typed API module.
- [x] Replace the ticket list `useEffect` with `useQuery`.
- [x] Replace ticket detail loading with dependent `useQuery` calls.
- [x] Replace run state loading with `useQuery`.
- [x] Replace run events polling with `useQuery` or query invalidation around the SSE stream.
- [x] Replace outbox, evidence, backup, validation, activity, and integration request polling with TanStack queries.
- [x] Replace start-run, approve-connection, manual-step, approve-step, edit-and-approve, reject, backup-not-applicable, retry, abort, draft-activity, and submit-activity handlers with `useMutation`.
- [x] Use mutation `onSuccess` to invalidate or update specific run/ticket queries.
- [x] Remove manual loading/error state that TanStack Query now owns.
- [x] Remove custom cache merge helpers where query cache updates can handle the behavior.
  - Decision: keep small query-cache helper functions for SSE event de-duplication and integration request upserts because those are cache update policies, not independent client-side state stores.

### OpenAPI-Generated Types And Client

- [x] Generate TypeScript types from FastAPI OpenAPI using `openapi-typescript`, `orval`, or `@hey-api/openapi-ts`.
- [x] Replace duplicated frontend types in `frontend/src/App.tsx` with generated API types.
- [x] Generate or hand-wrap a typed API client around the generated types.
- [x] Add a script to regenerate frontend API types.
- [x] Document when to regenerate API types after backend schema changes.
- [x] Add a CI/build step that fails if generated types are stale, if practical.

Regenerate frontend API types after backend route, request model, response model, or enum changes by running `mise exec -- pnpm run generate:api` from `frontend/`.

### TanStack Router

- [x] Decide whether to install `@tanstack/react-router`.
- [x] Preserve the current single-screen workflow for demo speed.
- [x] Keep selected ticket, filters, and active run in local UI state because this technician workspace is still one operational screen.

Decision: do not install TanStack Router in this increment. The current UI is intentionally a single workspace, so adding routes would add library surface without replacing meaningful custom code.

### TanStack Table

- [x] Decide whether to install `@tanstack/react-table`.
- [x] Keep ticket, evidence, backup, validation, and outbox rows as workflow cards because they are action-oriented records rather than dense tabular data.
- [x] Keep mobile rendering usable with stacked rows/cards on narrow screens.

Decision: do not install TanStack Table in this increment. The current repeated views do not need column sorting, resizing, pagination, or row models.

### Forms And Validation

- [x] Install `react-hook-form`, `zod`, and `@hookform/resolvers`.
- [x] Replace manual command form state with React Hook Form.
- [x] Replace manual timeout validation with a Zod schema.
- [x] Replace backup not-applicable form validation with a Zod schema.
- [x] Replace activity draft form validation with a Zod schema.
- [x] Surface field-level errors instead of silently ignoring invalid submits.

### SSE And Live Updates

- [x] Keep native `EventSource` if it remains simple and reliable.
- [x] Decide not to install `@microsoft/fetch-event-source`; native `EventSource` remains sufficient with polling fallback.
- [x] On SSE events, update TanStack Query cache for run events instead of app-local arrays.
- [x] Keep polling as a fallback for missed events.
- [x] Verify that terminal output still streams and persists correctly.

### Frontend Utilities

- [x] Decide not to add `date-fns`; date formatting has not expanded beyond the current helper.
- [x] Add `clsx` and `tailwind-merge` for class composition.
- [x] Decide not to add `sonner`; current inline audit/status panels are more useful than transient toasts for this workflow.
- [x] Decide not to add `cmdk`; no command palette/search workflow exists yet.

## Backend - Persistence

### SQLAlchemy 2.0

- [x] Install `sqlalchemy`.
- [x] Decide sync versus async SQLAlchemy. Recommended starting point: sync SQLAlchemy with FastAPI threadpool behavior, because current code is sync.
- [x] Define declarative models for `runs`, `run_events`, `proposed_steps`, `command_executions`, `command_output_chunks`, `inspected_sources`, `validation_results`, `validation_expectations`, `backup_records`, `activity_drafts`, `integration_requests`, `redaction_events`, `outbox_events`, and Phoenix cache tables.
- [x] Model JSONB fields using SQLAlchemy PostgreSQL JSONB types.
- [x] Model enum/check constraints explicitly.
- [x] Model foreign keys and indexes currently expressed in raw SQL.
- [x] Replace raw insert/update/select calls in `PostgresRunStore` with SQLAlchemy statements.
- [x] Replace manual row-to-Pydantic mapping with model-to-schema conversion helpers.
- [x] Keep Postgres-specific row locking for outbox claim behavior, including `FOR UPDATE SKIP LOCKED`.
- [x] Preserve append-only audit behavior and existing transition checks.
- [x] Keep an in-memory store for fast unit tests unless SQLAlchemy test setup is simple enough to replace it.

### Alembic

- [x] Install `alembic`.
- [x] Initialize Alembic under `backend/`.
- [x] Move schema creation and migration versions out of `backend/app/run_store.py`.
- [x] Generate an initial migration matching the current schema.
- [x] Preserve custom constraints, triggers, indexes, and Postgres-specific guards.
- [x] Add a backend startup path that runs migrations or document the migration command clearly.
- [x] Update Docker/Compose startup if migrations need a separate command.
- [x] Add tests that verify a fresh database migrates successfully.

### Optional SQLModel

- [x] Evaluate `sqlmodel` only if shared FastAPI/Pydantic/ORM models reduce real duplication.
- [x] Reject `sqlmodel` because this code needs explicit SQLAlchemy/Postgres control for JSONB, triggers, constraints, locking, and outbox behavior.

## Backend - HTTP Clients

### Phoenix Client

- [x] Install `httpx`.
- [x] Replace `urllib.request` in the Phoenix client with `httpx.Client`.
- [x] Preserve bearer auth, timeout behavior, error mapping, and response validation.
- [x] Add structured request/response logging without secrets.
- [x] Use dependency injection so tests can pass a mock transport.

### Retries And Backoff

- [x] Install `tenacity`.
- [x] Add retries for Phoenix availability failures where safe.
- [x] Do not retry non-idempotent Phoenix writes unless idempotency is proven or guarded.
- [x] Use explicit retry policies for worker outbox processing.
- [x] Keep dead-letter behavior visible in audit events and UI.

### OpenAI Client

- [x] Install the official `openai` Python SDK.
- [x] Replace raw `urllib.request` OpenAI chat completions adapter.
- [x] Use structured output support where available for planner output.
- [x] Keep Pydantic validation for planner output.
- [x] Keep deterministic fallback when the LLM is unavailable or invalid.
- [x] Redact or avoid sensitive data before sending planner context to any LLM provider.

## Backend - SSH Execution

### AsyncSSH Or Paramiko

- [x] Evaluate `asyncssh` for native async SSH execution and streaming.
- [x] Evaluate `paramiko` if blocking SSH is preferred and async is not needed.
- [x] Pick one SSH library and document the reason.
  - Decision: use Paramiko because the worker and `CommandRunner` contract are synchronous today; Paramiko preserves that boundary while replacing OpenSSH subprocess plumbing. AsyncSSH remains a better fit only if the worker is later made async.
- [x] Replace subprocess-based SSH command construction in `SSHCommandRunner`.
- [x] Preserve host key policy modes: `accept-new`, `strict`, and explicit dev-only insecure behavior if still required.
- [x] Preserve private key path handling and validation.
- [x] Preserve streaming stdout/stderr chunks into the run store.
- [x] Preserve timeout enforcement.
- [x] Preserve non-interactive command execution.
- [x] Add integration-style tests using a fake or containerized SSH server if time allows.

## Backend - Agent Framework

### LangGraph

- [x] Install `langgraph` and required LangChain/OpenAI integration packages.
- [x] Define graph state for run ID, ticket snapshot, system snapshot, events, commands, evidence, backups, validation, pending step, and activity draft.
- [x] Convert planner context building into a graph node.
- [x] Convert LLM planning into a graph node with structured output.
- [x] Convert deterministic fallback into a graph node.
- [x] Convert safety classification into a deterministic graph node.
- [x] Convert technician approval into a human-in-the-loop interrupt/checkpoint.
- [x] Convert command execution request into a graph node or durable outbox action.
- [x] Convert validation and activity drafting into graph nodes.
- [x] Preserve one-command-at-a-time behavior.
- [x] Preserve audit event appends at every side-effect boundary.
- [x] Preserve explicit technician edit/reject/retry/abort controls.
- [x] Decide whether LangGraph persistence replaces some run-store workflow state or only orchestrates around the existing database.
- [x] Keep tests for invalid planner output, fallback behavior, blocked commands, and approval gates.

Decision: graph orchestration wraps the existing run store, outbox, and audit/event tables. LangGraph persistence does not replace durable run-store workflow state in this increment.

### Agent Tooling

- [x] Represent SSH command proposal as a tool-like action but do not let the LLM execute it directly.
- [x] Represent Phoenix activity submission as a guarded tool or workflow action.
- [x] Represent evidence lookup and validation summaries as deterministic tools/nodes.
- [x] Keep model-facing tool schemas narrow and explicit.

## Backend - Background Jobs And Outbox

### Keep Or Replace Postgres Outbox

- [x] Keep the custom Postgres outbox because minimizing infrastructure is more important than replacing every line of custom code.
- [x] Evaluate `procrastinate` for Postgres-backed jobs.
- [x] Evaluate `dramatiq`, `celery`, and `temporalio`.
- [x] Preserve durable retries, dead letters, and audit visibility.
- [x] Preserve single side-effect processing semantics.
- [x] Preserve compatibility with Docker Compose.

Decision: keep the existing Postgres outbox. Procrastinate is the closest replacement, but the current outbox is already tightly coupled to run audit events and keeps the Compose stack simpler than adding a separate worker framework.

## Backend - API And Streaming

### Server-Sent Events

- [x] Install `sse-starlette`.
- [x] Replace manual `StreamingResponse` SSE formatting with `EventSourceResponse`.
- [x] Preserve current event names and payload shape.
- [x] Preserve polling fallback endpoint.
- [x] Add tests for stream event formatting if practical.

### API Structure

- [x] Split large `backend/app/main.py` route groups into routers: tickets, runs, events, commands, evidence, backups, validation, activity, integration, health.
- [x] Keep dependency functions small and testable.
- [x] Keep OpenAPI response models accurate for generated frontend types.

Implementation: route handlers now live under `backend/app/routes/`, shared dependency helpers live in `backend/app/api_dependencies.py`, and `backend/app/main.py` only owns app setup, middleware, router registration, and compatibility re-exports.

## Backend - Safety, Parsing, And Redaction

### Command Parsing

- [x] Evaluate `bashlex` or another shell parser for command syntax inspection.
- [x] Replace regex-only shell composition detection where a parser is more reliable.
- [x] Keep custom allow/block decisions in the safety layer.
- [x] Add regression tests for every blocked dangerous pattern currently covered.

Decision: do not install `bashlex` in this phase. The safety layer intentionally blocks shell composition, redirection, substitution, unsafe globbing, and dangerous commands before execution instead of parsing and allowing composed shell programs. Existing regression tests cover the current blocked patterns; keep the custom policy code.

### Secret Redaction

- [x] Evaluate `detect-secrets` or similar libraries as supplemental scanning.
- [x] Keep existing domain-specific redaction rules for bearer tokens, private keys, password URIs, and secret env lines.
- [x] Add tests for redaction in command output, evidence excerpts, and activity drafts.
- [x] Ensure redaction events remain auditable.

Decision: do not install `detect-secrets` in this phase. The existing redaction layer is tied to runtime command output, evidence excerpts, Phoenix-facing error text, planner context, and activity drafts; a repository secret scanner would be supplemental, not a replacement.

## Backend - Validation And Serialization

- [x] Keep Pydantic models for request/response validation.
- [x] Add `from_attributes` conversion where ORM models are converted to response schemas.
- [x] Consider shared enum definitions between ORM and Pydantic schemas.
- [x] Consider stricter `Literal`/enum types for event types, phases, source types, and validation check types.
- [x] Add schema tests that ensure OpenAPI still exposes expected endpoints and response models.

Decision: keep existing `StrEnum` classes for the public status enums. Do not convert all event type strings to `Literal` yet because the event vocabulary is still evolving and is also constrained at the database/audit layer.

## Backend - Observability

- [x] Install `structlog`.
- [x] Replace ad hoc worker logging with structured logs.
- [x] Add request IDs or run IDs to API/worker logs.
- [x] Install OpenTelemetry FastAPI instrumentation only if tracing is useful for the demo or future ops.
- [x] Do not log secrets, SSH key paths with sensitive values, Phoenix tokens, or raw unredacted command output.

Decision: do not install OpenTelemetry instrumentation yet. Structured JSON logs now include API request IDs and worker outbox/run identifiers without adding tracing infrastructure.

## Testing Libraries And Verification

- [x] Add HTTP mock coverage for Phoenix and LLM clients.
- [x] Add optional `testcontainers` support for Postgres integration tests if Docker-based tests are acceptable.
- [x] Keep fast unit tests for safety, evidence detection, backup logic, and deterministic planner behavior.
- [x] Add migration tests for a fresh Postgres database.
- [x] Add frontend component tests only if the split into reusable components makes them valuable.
- [x] Keep Playwright for end-to-end technician workflow coverage.
- [x] After every frontend-facing phase, capture desktop and mobile screenshots.

Decision: do not add frontend component tests in this phase. The current risk is covered by Playwright workflow tests, API state tests, and visual verification artifacts; add component tests later only for extracted reusable components with meaningful isolated behavior.

## Suggested Implementation Order

- [x] 1. Add OpenAPI-generated frontend types so later frontend changes are safer.
- [x] 2. Add TanStack Query and migrate server-state handling.
- [x] 3. Add React Hook Form and Zod for manual command/activity forms.
- [x] 4. Add Tailwind and shadcn/ui, then migrate the visual layer.
- [x] 5. Split the frontend into smaller modules/components after state and styling are stable.
- [x] 6. Replace Phoenix and OpenAI raw HTTP clients with `httpx` and official SDKs.
- [x] 7. Add SQLAlchemy models while keeping current behavior tests passing.
- [x] 8. Add Alembic and migrate schema management out of `run_store.py`.
- [x] 9. Replace Postgres run-store operations incrementally with SQLAlchemy.
- [x] 10. Replace SSH subprocess execution with the chosen SSH library.
- [x] 11. Introduce LangGraph for the agent planning/approval workflow.
- [x] 12. Evaluate whether the custom Postgres outbox should stay or move to a job library.
- [x] 13. Replace manual SSE with `sse-starlette`.
- [x] 14. Add observability and stricter integration tests.

## Do Not Replace Blindly

- [x] Do not replace safety rules with generic agent guardrails.
- [x] Do not let the LLM execute SSH commands directly.
- [x] Do not remove append-only audit events.
- [x] Do not remove technician approval/edit/reject/abort gates.
- [x] Do not remove deterministic planner fallback.
- [x] Do not remove validation requirements before Phoenix activity submission.
- [x] Do not add Redis or a new queue unless the Compose/demo tradeoff is acceptable.
- [x] Do not migrate all backend persistence in one untested commit.

## Success Criteria

- [x] Frontend has fewer custom state/loading/error/polling paths.
- [x] Frontend visual primitives come from Tailwind and shadcn/ui.
- [x] Frontend API types are generated from backend OpenAPI.
- [x] Backend persistence is expressed through SQLAlchemy models and Alembic migrations.
- [x] Raw HTTP client code is replaced by maintained clients.
- [x] SSH command execution uses a maintained SSH library or a documented decision explains why OpenSSH subprocess remains.
- [x] Agent orchestration uses LangGraph or a documented decision explains why the custom workflow remains.
- [x] Existing safety, audit, approval, backup, validation, and Phoenix activity behavior remains intact.
- [x] Backend tests, frontend build, Playwright checks, and Compose smoke test pass after each major phase.
