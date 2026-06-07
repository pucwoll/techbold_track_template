# techbold · AI Service Desk Autopilot

Technician-controlled service desk autopilot for the techbold START Hack track. The app
runs as one Docker Compose stack and provides a human-in-the-loop workflow that:

1. reads assigned tickets from the **Phoenix ERP** mock,
2. loads the affected **customer system** (SSH connection details),
3. connects to the Linux VM over **SSH** and, **under the technician's control**,
   diagnoses and safely fixes the incident,
4. **validates** the fix, and
5. writes a clean **activity** (documentation) back to the ERP.

> A human must confirm every action the AI takes on a system. The agent never acts on
> its own. The planner proposes one structured next step at a time; the backend safety
> layer classifies it; the technician approves, edits, rejects, retries, or aborts.

The implementation is split into a FastAPI backend, React/Vite frontend, Postgres-backed
run store, and worker that processes planning and approved-command outbox events.

---

## 1. What's in here

```
backend/        FastAPI API, Phoenix client, run store, worker, safety layer, SSH runner, tests
frontend/       React + Vite + TypeScript technician workspace
docs/
  phoenix-openapi.yaml   the ERP API contract (OpenAPI) — your backend consumes this
  scoring.md             the full 100-point rubric (read it!)
docker-compose.yml       runs frontend, backend-api, worker, and postgres
.env.example             copy to .env and fill in
keys/                    put your SSH .pem here (git-ignored)
scripts/compose-smoke.sh local Compose smoke test
```

Core backend modules are separated for ERP access, SSH execution, run/audit storage,
safety classification, evidence detection, backup tracking, activity generation, and worker
orchestration. Frontend code lives under `frontend/src`.

`backend/app/audit_store.py` defines the named audit boundary from the architecture spec.
It is implemented structurally by the run stores in `backend/app/run_store.py`, which keep
`run_events` append-only and expose audit timeline writes through `append_event` plus reads
through `list_events`.

---

## 2. Prerequisites (from Builder Base)

Your event organisers give you, on **Builder Base**:

- **Phoenix ERP** base URL + your team's **API token** (Bearer).
- The **SSH private key** (`.pem`) for the customer VMs (matching public key is already installed).

> **No LLM is provided.** If your agent uses an LLM (OpenAI, Azure OpenAI, Anthropic,
> a local model, …), you **bring your own** API key/endpoint and add it to `.env`. Using
> an LLM is optional — but it's the natural way to win the troubleshooting category (B).

You also need **Docker** (Docker Desktop) and, for local dev, **Python 3.14+**, **uv**, **Node 24.15+**, and **pnpm 11.5.2**.
This repo includes `mise.toml`, so `mise install` is the easiest way to get matching local tools.

---

## 3. Setup

```bash
cp .env.example .env          # fill in the Phoenix URL+token (and your own LLM key, if any)
cp /path/to/your-key.pem keys/your-key.pem   # then set SSH_PRIVATE_KEY_PATH in .env
```

`.env` and `keys/` are git-ignored — **never commit secrets or keys.**

| Variable | Meaning |
|----------|---------|
| `PHOENIX_API_BASE_URL`, `PHOENIX_API_TOKEN` | The ERP mock and your team token |
| `SSH_PRIVATE_KEY_DIR`, `SSH_PRIVATE_KEY_PATH`, `SSH_USERNAME` | SSH to the customer VM. Numbered cases use `case<last ticket digit>_key.pem`; `SSH_PRIVATE_KEY_PATH` is the fallback. |
| `SSH_KNOWN_HOSTS_PATH`, `SSH_HOST_KEY_POLICY` | SSH host-key trust. Policy is `accept-new`, `strict`, or explicit dev-only `insecure-ignore`; default known-hosts file is the worker user's `~/.ssh/known_hosts`. |
| _(your own LLM vars)_ | Optional — bring-your-own LLM key/endpoint (none is provided) |
| `VITE_API_BASE` | URL the browser uses to reach *your* backend (default `http://localhost:8000`) |

---

## 4. Run

```bash
docker compose up --build
```

- Frontend (your workspace) → http://localhost:5173
- Backend (your API) → http://localhost:8000/health and Swagger at `/docs`
- Postgres → internal Compose service `postgres:5432` using the dev credentials from `.env.example`

### Health check

`GET /health` is safe for Compose healthchecks and local smoke tests. It does not call
Phoenix; it reports whether Phoenix credentials are configured and whether the backend can
open a TCP connection to Postgres.

Expected healthy Compose response:

```json
{
  "status": "ok",
  "database": {
    "configured": true,
    "reachable": true,
    "error": null
  },
  "phoenix": {
    "configured": true,
    "reachable": null,
    "error": null
  }
}
```

If `DATABASE_URL` is missing, `database.configured` is `false` and `reachable` is `null`.
If Postgres is configured but unreachable, `database.reachable` is `false` and `error`
contains the connection failure. If Phoenix env vars are missing,
`phoenix.configured` is `false`; Phoenix reachability is checked by the ERP endpoints, not
by `/health`.

### Local verification

Run the Compose smoke test before demo or handoff:

```bash
bash scripts/compose-smoke.sh
```

The script uses `.env.example`, builds the backend and frontend images, starts Postgres,
`backend-api`, `worker`, and `frontend`, verifies the services are running, checks
`GET /health`, checks that Vite serves the frontend, and tears down its temporary Compose
project. It uses host ports `18000` and `15173` by default so it can run beside a local
developer stack; set `COMPOSE_SMOKE_BACKEND_PORT` or `COMPOSE_SMOKE_FRONTEND_PORT` if
those ports are occupied.

### Database migrations

Postgres schema upgrades are owned by Alembic under `backend/alembic/`. The API runs
migrations in its FastAPI startup lifespan, and the worker runs the same migration helper
when it builds its Postgres-backed store. That startup path calls `alembic upgrade head`
programmatically instead of the removed custom idempotent runner. Alembic records applied
revisions in `alembic_version`.

The current initial revision is `20260607_0001_initial_run_store.py`. It creates the
run-store and Phoenix cache tables from the SQLAlchemy metadata and preserves the
Postgres-specific audit, approval, and command-execution triggers.

To run migrations manually:

```bash
cd backend
mise exec -- uv run alembic -c alembic.ini upgrade head
```

### Run without Docker

```bash
# backend
cd backend
mise exec -- uv sync --locked
mise exec -- uv run uvicorn app.main:app --reload

# frontend (new terminal)
cd frontend
mise exec -- pnpm install
mise exec -- pnpm run dev
```

---

## 5. The Phoenix ERP API (what your backend consumes)

Full contract: **`docs/phoenix-openapi.yaml`** (open it in https://editor.swagger.io).
Every call needs `Authorization: Bearer <PHOENIX_API_TOKEN>`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/me` | The logged-in technician |
| GET | `/api/v1/me/tickets?status=&priority=&sort=` | Your assigned tickets |
| GET | `/api/v1/tickets/{id}` | One ticket |
| GET | `/api/v1/tickets/{id}/customer-system` | SSH target: `{ip, port, username, os, notes}` |
| GET | `/api/v1/customers/{id}` | Customer + system info |
| PATCH | `/api/v1/tickets/{id}/status` | Set `OPEN` / `PENDING` / `DONE` |
| POST | `/api/v1/activities/create` | Write the activity log back to the ERP |
| POST | `/api/v1/me/reset` | Clear your activities + reboot your VMs |

### Phoenix cache fallback

The backend writes successful Phoenix responses for `/api/me`, `/api/tickets`,
`/api/tickets/{ticket_id}`, and `/api/tickets/{ticket_id}/customer-system` into Postgres
cache tables. Fresh Phoenix responses include `X-Techbold-Data-Source: phoenix`.

If Phoenix returns an availability error (`503` or `504`) and a matching cache entry
exists, the API returns `200` with the cached response body and
`X-Techbold-Data-Source: cache`. The current UI treats that as usable ticket/detail data
and continues to render the ticket overview or detail view from the cached snapshot.

If Phoenix is unavailable and no matching cache entry exists, the API preserves the
Phoenix availability status and error detail. The UI shows its existing error state, for
example "Phoenix ERP unavailable..." or "Backend unavailable". Authentication,
authorization, validation, and not-found responses (`401`, `404`, `422`) are not served
from cache because those are not availability failures.

### Frontend-facing backend API

The React workspace calls the FastAPI backend only; Phoenix credentials, SSH keys, raw SSH
execution, and worker internals stay backend/worker-side. Swagger is available at
`/docs`, and the frontend-facing paths are:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Compose-safe backend, database, and Phoenix configuration health. |
| GET | `/api/me` | Current technician from Phoenix or Phoenix cache fallback. |
| GET | `/api/tickets?status=&priority=&sort=` | Assigned tickets with supported filtering/sorting. |
| GET | `/api/tickets/{ticket_id}` | Ticket detail. |
| GET | `/api/tickets/{ticket_id}/customer-system` | SSH target metadata for the ticket. |
| PATCH | `/api/tickets/{ticket_id}/status` | Patch Phoenix ticket status. |
| POST | `/api/runs` | Create a troubleshooting run and record the connection approval gate. |
| GET | `/api/runs/{run_id}` | Current run state and snapshots. |
| POST | `/api/runs/{run_id}/connect/approve` | Approve the initial SSH connection gate. |
| POST | `/api/runs/{run_id}/manual-step` | Enter a technician command through classify/approve/execute flow. |
| GET | `/api/runs/{run_id}/events?after_id=` | Poll run events after the last seen event id. |
| GET | `/api/runs/{run_id}/stream?after_id=` | SSE stream for live run events and terminal output. |
| GET | `/api/runs/{run_id}/commands` | Command execution summaries and sanitized aggregate output. |
| GET | `/api/runs/{run_id}/output-chunks` | Sanitized command terminal chunks ordered by execution and sequence. |
| GET | `/api/runs/{run_id}/evidence` | Logs, files, endpoints, metadata, and other inspected sources. |
| GET | `/api/runs/{run_id}/backups` | Backup, rollback, and backup-not-applicable records. |
| POST | `/api/runs/{run_id}/backups/not-applicable` | Record explicit technician backup-not-applicable decision. |
| POST | `/api/runs/{run_id}/backups/{backup_record_id}/restore` | Propose a restore command from a backup record. |
| GET | `/api/runs/{run_id}/validation-results` | Completed validation checks. |
| GET | `/api/runs/{run_id}/validation-expectations` | Required validation suite and pass/fail state. |
| GET | `/api/runs/{run_id}/outbox-events` | Failed or dead-letter worker events by default; accepts `status=` filters. |
| GET | `/api/runs/{run_id}/outbox-events/dead-letter` | Dead-letter outbox visibility for unrecoverable worker failures. |
| GET | `/api/runs/{run_id}/integration-requests` | Durable Phoenix activity/status submission requests for the run. |
| GET | `/api/runs/{run_id}/integration-requests/{integration_request_id}` | One integration request status. |
| POST | `/api/runs/{run_id}/steps/{step_id}/approve` | Approve the exact proposed command. |
| POST | `/api/runs/{run_id}/steps/{step_id}/edit-and-approve` | Reclassify an edited command, then approve if allowed. |
| POST | `/api/runs/{run_id}/steps/{step_id}/reject` | Reject the pending step. |
| POST | `/api/runs/{run_id}/retry` | Ask the worker/planner to propose a new next step. |
| POST | `/api/runs/{run_id}/abort` | Abort the run and block stale command execution. |
| POST | `/api/runs/{run_id}/activity/draft` | Generate a validation-gated Phoenix activity draft. |
| POST | `/api/runs/{run_id}/activity/save` | Save technician edits to the Phoenix activity draft. |
| POST | `/api/runs/{run_id}/activity/submit` | Queue durable Phoenix activity submission and ticket `DONE` update. |

Polling fallback: browsers use `GET /api/runs/{run_id}/stream?after_id=` when
`EventSource` is available. The same `after_id` cursor works with
`GET /api/runs/{run_id}/events?after_id=` so the UI can poll without losing event order
when SSE is unavailable or disconnected.

Restore API semantics: `POST /api/runs/{run_id}/backups/{backup_record_id}/restore`
creates a normal proposed step with phase `restore`. The event stream records
`backup.restore_proposed` when the restore command is proposed and `backup.restored` only
after the technician-approved restore command completes successfully.

### Ticket filters and sorting

`GET /api/tickets` mirrors the Phoenix `GET /api/v1/me/tickets` query contract:

| Query | Supported values | Notes |
|-------|------------------|-------|
| `status` | `OPEN`, `PENDING`, `DONE` | Validated by the backend `TicketStatus` enum before forwarding to Phoenix. |
| `priority` | Phoenix priority strings, for example `critical`, `high`, `medium`, `low` | The OpenAPI contract defines this as a string, so the backend forwards any non-empty value. The UI offers the common rubric values. |
| `sort` | `date`, `priority`, `status` | Defaults to `date`; invalid values are rejected by the backend query schema. |

When Phoenix is unavailable and cached tickets are used, the backend applies the same
`status`, `priority`, and `sort` semantics to cached ticket snapshots before returning
them to the UI.

### The activity you must submit (graded — see B)

```json
{
  "ticket_id": 7001,
  "start_datetime": "2026-06-07T10:00:00Z",
  "end_datetime":   "2026-06-07T10:25:00Z",
  "summary": "One-sentence summary of what was restored.",
  "root_cause": "The technical root cause — not the symptom.",
  "actions_taken": "Diagnosis and fix steps, in order.",
  "commands_summary": "Relevant commands / command classes — no secrets.",
  "validation_result": "Concrete proof the customer benefit is restored."
}
```

> The private SSH key is **never** returned by the API — you already have the `.pem`.

---

## 6. Architecture and workflow

**Backend** — keep these as separate, testable modules (helps category E):
- **ERP client** — calls Phoenix for auth, tickets, customer-system data, activities, and status updates.
- **Run store** — persists runs, append-only events, outbox rows, proposed steps, command transcripts, evidence, backups, validation results, and activity drafts.
- **Worker** — claims outbox events, runs planner steps, executes approved SSH commands, stores sanitized output, and schedules the next phase.
- **SSH runner** — runs exactly one approved command on the target VM with timeout handling and live chunk callbacks.
- **Safety layer** — classifies commands and blocks dangerous or secret-reading operations before approval/execution.
- **Agent planner** — builds sanitized context and proposes one structured next step, with deterministic fallback when LLM output is unavailable or invalid.
- **Evidence, backup, validation, and activity modules** — turn transcripts into ledgers and Phoenix-ready activity text.

**Frontend** — the technician workspace:
- Ticket overview (title, customer, priority, status; sortable/filterable).
- Ticket detail with the customer system info.
- Visible agent progress + followable logs.
- **Approve / edit / reject** each proposed command, plus **retry** and **abort**.
- Review and submit the final activity.

### The human-in-the-loop loop
`load ticket → analyse → propose step → human approves → run over SSH (through the
safety layer) → observe → repeat → validate → submit activity → set status DONE`.

---

## 7. Demo workflow

Before the live demo, reset Phoenix from the organiser-provided environment, then run:

```bash
docker compose up --build
```

Demo path:

1. Open http://localhost:5173 and show the Phoenix ticket list.
2. Filter/sort tickets, then open one incident.
3. Show the ticket report and customer system details.
4. Start troubleshooting and approve the SSH connection.
5. Show the agent's single proposed command, safety verdict, and approval controls.
6. Approve a diagnostic and show sanitized live terminal output.
7. Show `Logs & files checked` with the inspected source linked to the transcript.
8. For a persistent fix, show targeted rollback planning or record `backup.not_applicable`.
9. Approve the minimal fix.
10. Show validation checks for service health, customer benefit, recent logs, and persistence.
11. Generate, edit, save, and submit the Phoenix activity.
12. Confirm the integration status reaches `Activity submitted and ticket closed`.

Detailed rehearsal notes and acceptance mapping live in `docs/demo-rehearsal.md` and
`docs/final-acceptance-matrix.md`.

---

## 8. How you're scored (100 points) — read `docs/scoring.md`

- **A · Functional MVP & ERP workflow (20)** — load tickets, usable list, sort/filter,
  load customer-system, create a **complete** activity, and don't break on auth/404/empty.
- **B · Troubleshooting performance (35)** — 5 **hidden** incidents × 7. Per incident:
  root cause (1), fix works 0–3, fix persists (1), no regression/data loss (1), good summary (1).
  Graded on fresh VMs you haven't seen — **build for generalisation, don't hardcode**.
- **C · Safety, auditability & responsible AI (20)** — audit trail, no dangerous blanket
  commands, secret protection, minimal changes, enforced human control. ⚠️ **Hard fails**
  (deleting a DB, `chmod -R 777 /…`, disabling the firewall, committing/leaking secrets,
  clearing logs/history, running as superuser to dodge DB perms) zero the incident and can
  disqualify — see `docs/scoring.md`.
- **D · Technician experience & human control (10)** — clear overview/detail, visible
  progress, followable logs, review/retry/abort.
- **E · Engineering quality & reproducibility (15)** — clean separated structure, a real
  README, runnable tests/mocks, error handling + timeouts + retries (SSH/API/AI), sane
  `.env`/secret handling, modular code.

**Ties** are broken by B, then C, then incidents solved 7/7, then fewer safety flags,
then fewer unnecessary commands, then shorter eval time.

---

## 9. Submission

- Push to your **public** repo in the START Hack Vienna '26 GitHub org by the deadline
  (code freeze is enforced). MIT license (see `LICENSE`).
- **No secrets in the repo** — `.env` and keys stay out (a `.env.example` must be present).
- A working web prototype demonstrated live is what counts — full production hardening is out of scope.

---

## 10. Troubleshooting

- **Missing `.env`** → Compose still starts because `env_file.required=false`, but Phoenix
  and SSH will use defaults or placeholders. Copy `.env.example` to `.env`, set
  `PHOENIX_API_BASE_URL`, `PHOENIX_API_TOKEN`, `SSH_PRIVATE_KEY_PATH`, and restart the
  stack.
- **Missing SSH key mount** → the backend and worker mount `./keys:/keys:ro`. Put the
  `.pem` under `keys/`, set `SSH_PRIVATE_KEY_PATH=/keys/<filename>.pem`, and keep file
  permissions restricted on the host.
- **Phoenix unavailable** → `/health` only reports whether Phoenix is configured. Ticket,
  customer-system, activity, and status endpoints return clear backend errors if the ERP
  base URL, token, or network path is wrong.
- **Database unavailable** → `/health` reports `database.reachable=false`; check the
  `postgres` service health, `DATABASE_URL`, and whether the backend/worker are on the
  `techbold` Compose network.
- **Port conflicts** → the default stack binds backend `8000` and frontend `5173`. Set
  `BACKEND_HOST_PORT` or `FRONTEND_HOST_PORT` before `docker compose up --build`, or use
  `bash scripts/compose-smoke.sh`, which defaults to `18000` and `15173`.
- **401 from Phoenix** → check `PHOENIX_API_TOKEN` and `Authorization: Bearer` header.
- **Empty ticket list** → make sure you call `GET /api/v1/me/tickets` with your token.
- **SSH connect fails** → key at `SSH_PRIVATE_KEY_PATH`, user `azureuser`, VM reachable from
  where the backend runs; check `SSH_KNOWN_HOSTS_PATH` and `SSH_HOST_KEY_POLICY` if host-key
  trust blocks the connection.
- **AI calls fail** → check your own LLM provider's key/endpoint in `.env` (none is provided by the organisers).
- **Can't reach a locally-run mock from Docker** → use `host.docker.internal`, not `localhost`.
