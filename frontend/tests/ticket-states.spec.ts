import fs from "node:fs";

import { expect, type Page, type Request, test } from "@playwright/test";

type MockResult =
  | {
      status?: number;
      json?: unknown;
      body?: string;
      contentType?: string;
      delayMs?: number;
    }
  | "abort";

const employee = {
  id: 101,
  firstname: "Ada",
  lastname: "Lovelace",
  username: "ada",
  teamname: "Service Desk",
};

const ticket = {
  id: 7001,
  title: "API down",
  description: "Customer cannot reach the status endpoint.",
  priority: "high",
  status: "OPEN",
  customer_id: 5001,
  customer_name: "Nordlicht Logistik GmbH",
  tags: ["api", "urgent"],
  created_at: "2026-06-06T09:00:00Z",
  sla_due_at: "2026-06-06T12:00:00Z",
};

const databaseTicket = {
  ...ticket,
  id: 7002,
  title: "Database latency",
  description: "Customer reports slow database responses on port 5432.",
  priority: "medium",
  customer_id: 5002,
  customer_name: "Sternwarte Analytics AG",
  tags: ["database"],
  created_at: "2026-06-06T10:00:00Z",
  sla_due_at: "2026-06-06T16:00:00Z",
};

const customerSystem = {
  ticket_id: 7001,
  customer_id: 5001,
  system: {
    ip: "10.0.0.5",
    port: 22,
    username: "azureuser",
    os: "Ubuntu 24.04",
    notes: "nginx reverse proxy",
  },
};

const databaseCustomerSystem = {
  ...customerSystem,
  ticket_id: 7002,
  customer_id: 5002,
  system: {
    ip: "10.0.0.8",
    port: 22,
    username: "postgres",
    os: "Ubuntu 24.04",
    notes: "postgresql primary",
  },
};

const activeRun = {
  id: 42,
  ticket_id: 7001,
  status: "investigating",
  started_at: "2026-06-06T10:00:00Z",
  ended_at: null,
  ticket_snapshot: ticket,
  customer_system_snapshot: customerSystem,
  current_hypotheses: [],
  pending_step: null,
  validation_result: null,
  activity_draft: null,
};

const databaseRun = {
  ...activeRun,
  id: 43,
  ticket_id: 7002,
  ticket_snapshot: databaseTicket,
  customer_system_snapshot: databaseCustomerSystem,
};

const activityDraft = {
  ticket_id: 7001,
  start_datetime: "2026-06-06T10:00:00Z",
  end_datetime: "2026-06-06T10:20:00Z",
  summary: "Restored the customer status endpoint.",
  root_cause: "Inspected source #31 showed nginx could not bind port 80.",
  actions_taken: "Reviewed source #31, corrected the nginx listener, and validated the endpoint.",
  commands_summary: "command execution #77 inspected nginx; validation result #201 confirmed HTTP success.",
  validation_result: "validation result #201 from command execution #77 returned HTTP/1.1 200 OK.",
};

const completedIntegrationRequest = {
  id: 301,
  run_id: 42,
  ticket_id: 7001,
  activity_draft_id: 77,
  request_type: "phoenix_activity_submission",
  status: "completed",
  activity_payload: activityDraft,
  phoenix_activity_id: 9001,
  ticket_status: "DONE",
  attempts: 1,
  error: null,
  created_at: "2026-06-06T10:21:00Z",
  updated_at: "2026-06-06T10:22:00Z",
  completed_at: "2026-06-06T10:22:00Z",
};

const partialIntegrationRequest = {
  ...completedIntegrationRequest,
  id: 302,
  status: "activity_created",
  ticket_status: null,
  attempts: 1,
  error: "Phoenix status patch unavailable",
  completed_at: null,
};

const blockedManualStep = {
  id: 55,
  run_id: 42,
  source: "manual",
  phase: "diagnostic",
  command: "cat /etc/shadow",
  purpose: "Inspect credential file.",
  expected_signal: null,
  risk_class: "BLOCKED",
  safety_verdict: "blocked",
  safety_summary: "Reading likely secret material is blocked.",
  safety_notes: ["Command was not queued for execution."],
  status: "blocked",
  timeout_s: 30,
};

const pendingFixStep = {
  id: 90,
  run_id: 42,
  source: "agent",
  phase: "fix",
  command: "sed -i 's/listen 8080/listen 80/' /etc/nginx/sites-enabled/default",
  purpose: "Apply a targeted nginx listen-port fix.",
  expected_signal: "nginx config should use the customer-facing port.",
  risk_class: "MEDIUM_RISK",
  safety_verdict: "allowed",
  safety_summary: "Targeted change requires technician review and approval.",
  safety_notes: ["Confirm the target path or service is exact before approving."],
  status: "proposed",
  timeout_s: 30,
};

const runAwaitingFixApproval = {
  ...activeRun,
  status: "awaiting_step_approval",
  pending_step: pendingFixStep,
};

const backupPlannedEvent = {
  id: 41,
  run_id: 42,
  created_at: "2026-06-06T10:06:00Z",
  actor: "backup_service",
  event_type: "backup.planned",
  summary: "Persistent file change requires rollback support for /etc/nginx/sites-enabled/default.",
  command: pendingFixStep.command,
  approval_status: null,
  payload: {
    step_id: pendingFixStep.id,
    source_path: "/etc/nginx/sites-enabled/default",
    backup_type: "file_copy",
    backup_record_id: 501,
  },
};

const plannedBackupRecord = {
  id: 501,
  run_id: 42,
  ticket_id: 7001,
  command_execution_id: null,
  source_path: "/etc/nginx/sites-enabled/default",
  backup_path: "/var/backups/techbold-autopilot/7001/42/default.prechange",
  backup_type: "file_copy",
  reason: "Persistent file change requires rollback support for /etc/nginx/sites-enabled/default.",
  restore_command: "cp -a /var/backups/techbold-autopilot/7001/42/default.prechange /etc/nginx/sites-enabled/default",
  stored_content: false,
  redacted: false,
  backup_required: true,
  backup_created: false,
  persistent_across_reboot: true,
  created_at: "2026-06-06T10:06:00Z",
};

const notApplicableBackupRecord = {
  ...plannedBackupRecord,
  id: 502,
  backup_path: null,
  backup_type: "not_applicable",
  reason: "Disposable demo config; rollback is not applicable.",
  restore_command: null,
  backup_required: false,
  persistent_across_reboot: false,
};

const deadLetterOutboxEvent = {
  id: 11,
  run_id: 42,
  event_type: "agent.plan_requested",
  payload: { reason: "connection_approved" },
  status: "dead_letter",
  attempts: 3,
  available_at: null,
  claimed_at: null,
  completed_at: null,
  error: "Planner worker failed after retry limit.",
  created_at: "2026-06-06T10:01:00Z",
};

const approvedEvent = {
  id: 8,
  run_id: 42,
  created_at: "2026-06-06T10:02:00Z",
  actor: "technician",
  event_type: "connection.approved",
  summary: "SSH connection approved by Ada Lovelace.",
  approval_status: "approved",
  payload: { approved_by: "Ada Lovelace" },
};

const planRequestedEvent = {
  id: 9,
  run_id: 42,
  created_at: "2026-06-06T10:03:00Z",
  actor: "worker",
  event_type: "agent.plan_requested",
  summary: "Planner queued after connection approval.",
  approval_status: null,
  payload: { reason: "connection_approved" },
};

const commandStartedEvent = {
  id: 10,
  run_id: 42,
  created_at: "2026-06-06T10:04:00Z",
  actor: "ssh_runner",
  event_type: "command.started",
  summary: "Approved SSH command started.",
  command: "journalctl -u nginx --no-pager -n 80",
  approval_status: null,
  payload: { command_execution_id: 77, step_id: 66 },
};

const terminalOutputEvent = {
  id: 11,
  run_id: 42,
  created_at: "2026-06-06T10:04:01Z",
  actor: "ssh_runner",
  event_type: "terminal.output_chunk",
  summary: "stdout output chunk received.",
  command: null,
  approval_status: null,
  payload: {
    command_execution_id: 77,
    sequence: 1,
    stream: "stdout",
    content: "HTTP/1.1 200 OK\n",
    redacted: false,
  },
};

const databaseCommandStartedEvent = {
  ...commandStartedEvent,
  id: 12,
  run_id: 43,
  command: "ss -H -ltn",
  payload: { command_execution_id: 88, step_id: 77 },
};

const databaseTerminalOutputEvent = {
  ...terminalOutputEvent,
  id: 13,
  run_id: 43,
  payload: {
    command_execution_id: 88,
    sequence: 1,
    stream: "stdout",
    content: "LISTEN 0 4096 127.0.0.1:5432 0.0.0.0:*\n",
    redacted: false,
  },
};

const inspectedSource = {
  id: 31,
  run_id: 42,
  command_execution_id: 77,
  source_type: "journal",
  source_name: "nginx",
  path: null,
  command: "journalctl -u nginx --no-pager -n 80",
  actor: "agent",
  purpose: "Inspect recent nginx journal entries.",
  finding: "nginx failed to bind port 80.",
  supports: "root_cause",
  sanitized_excerpt: "bind() to 0.0.0.0:80 failed",
  redacted: false,
  line_range: null,
  created_at: "2026-06-06T10:05:00Z",
};

const validationExpectations = [
  {
    id: 101,
    run_id: 42,
    fix_command_execution_id: 88,
    check_type: "service_health",
    target: "nginx",
    expected_result: "nginx service reports active after the approved fix.",
    relation_to_customer_symptom: "The affected service must be healthy before the status endpoint can recover.",
    required: true,
    status: "passed",
    validation_result_id: 201,
    created_at: "2026-06-06T10:06:00Z",
    updated_at: "2026-06-06T10:07:00Z",
  },
  {
    id: 102,
    run_id: 42,
    fix_command_execution_id: 88,
    check_type: "customer_benefit",
    target: "http://localhost",
    expected_result: "The local customer-facing endpoint returns HTTP success.",
    relation_to_customer_symptom: "The ticket reports that the customer cannot reach the status endpoint.",
    required: true,
    status: "failed",
    validation_result_id: 202,
    created_at: "2026-06-06T10:06:00Z",
    updated_at: "2026-06-06T10:08:00Z",
  },
  {
    id: 103,
    run_id: 42,
    fix_command_execution_id: 88,
    check_type: "logs_clean",
    target: "nginx",
    expected_result: "Recent nginx logs no longer show the original bind error.",
    relation_to_customer_symptom: "The original service error should be absent or materially reduced.",
    required: true,
    status: "pending",
    validation_result_id: null,
    created_at: "2026-06-06T10:06:00Z",
    updated_at: null,
  },
];

async function mockApi(page: Page, handler: (path: string, request: Request) => MockResult) {
  await page.route("http://127.0.0.1:18080/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let result = handler(path, route.request());
    if (result === "abort" && /^\/api\/runs\/\d+\/integration-requests$/.test(path)) {
      result = { json: [] };
    }
    if (result === "abort") {
      await route.abort("failed");
      return;
    }
    if (!result) {
      await route.abort("failed");
      return;
    }
    if (result.delayMs) {
      await new Promise((resolve) => setTimeout(resolve, result.delayMs));
    }
    await route.fulfill({
      status: result.status ?? 200,
      contentType: result.contentType ?? "application/json",
      body: result.body ?? JSON.stringify(result.json ?? {}),
    });
  });
}

function healthyApi(path: string): MockResult {
  if (path === "/api/me") {
    return { json: employee };
  }
  if (path === "/api/tickets") {
    return { json: [ticket] };
  }
  if (path === "/api/tickets/7001") {
    return { json: ticket };
  }
  if (path === "/api/tickets/7001/customer-system") {
    return { json: customerSystem };
  }
  return "abort";
}

test.describe("ticket overview states", () => {
  test("shows loading and then populated tickets", async ({ page }) => {
    await mockApi(page, (path) => {
      const result = healthyApi(path);
      if (path === "/api/me" || path === "/api/tickets") {
        return { ...(result as Exclude<MockResult, "abort">), delayMs: 500 };
      }
      return result;
    });

    await page.goto("/");

    await expect(page.getByRole("heading", { name: "Loading" })).toBeVisible();
    await expect(page.getByLabel("Loading tickets")).toBeVisible();
    await expect(page.getByRole("heading", { name: "1 visible" })).toBeVisible();
    await expect(page.getByRole("button", { name: /API down/ })).toBeVisible();
  });

  test("shows empty ticket state", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/me") {
        return { json: employee };
      }
      if (path === "/api/tickets") {
        return { json: [] };
      }
      return "abort";
    });

    await page.goto("/");

    await expect(page.getByRole("heading", { name: "0 visible" })).toBeVisible();
    await expect(page.getByText("No assigned tickets match the filters.")).toBeVisible();
  });

  test("shows Phoenix 401 overview error", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/me") {
        return { status: 401, json: { detail: "Missing or invalid bearer token" } };
      }
      if (path === "/api/tickets") {
        return { json: [ticket] };
      }
      return "abort";
    });

    await page.goto("/");

    await expect(page.getByText("Phoenix authentication failed.")).toBeVisible();
    await expect(page.getByText("No technician")).toBeVisible();
  });

  test("shows backend-unavailable overview error", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/me" || path === "/api/tickets") {
        return "abort";
      }
      return healthyApi(path);
    });

    await page.goto("/");

    await expect(page.getByText("Backend unavailable")).toBeVisible();
  });
});

test.describe("ticket detail states", () => {
  test("shows detail loading state", async ({ page }) => {
    await mockApi(page, (path) => {
      const result = healthyApi(path);
      if (path === "/api/tickets/7001" || path === "/api/tickets/7001/customer-system") {
        return { ...(result as Exclude<MockResult, "abort">), delayMs: 500 };
      }
      return result;
    });

    await page.goto("/");
    await expect(page.getByRole("button", { name: /API down/ })).toBeVisible();

    await expect(page.getByText("Loading ticket")).toBeVisible();
  });

  test("shows Phoenix 401 detail error", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/tickets/7001") {
        return { status: 401, json: { detail: "Missing or invalid bearer token" } };
      }
      return healthyApi(path);
    });

    await page.goto("/");

    await expect(page.getByText("Phoenix authentication failed.")).toBeVisible();
  });

  test("shows Phoenix 404 detail error", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/tickets/7001") {
        return { status: 404, json: { detail: "Ticket not found" } };
      }
      return healthyApi(path);
    });

    await page.goto("/");

    await expect(page.getByText("Phoenix returned 404 for this resource.")).toBeVisible();
  });

  test("shows backend-unavailable detail error", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/tickets/7001") {
        return "abort";
      }
      return healthyApi(path);
    });

    await page.goto("/");

    await expect(page.getByText("Backend unavailable")).toBeVisible();
  });
});

test.describe("run console queue visibility", () => {
  test("shows failed worker queue items for the active run", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/runs") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42/events") {
        return { json: [] };
      }
      if (path === "/api/runs/42/outbox-events") {
        return { json: [deadLetterOutboxEvent] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();

    const queuePanel = page.getByLabel("Worker queue failures");
    await expect(queuePanel).toBeVisible();
    await expect(queuePanel).toContainText("agent.plan_requested");
    await expect(queuePanel).toContainText("dead letter");
    await expect(queuePanel).toContainText("Planner worker failed after retry limit.");
  });

  test("links evidence rows to the command transcript anchor", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/runs") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42/events") {
        return { json: [commandStartedEvent] };
      }
      if (path === "/api/runs/42/evidence") {
        return { json: [inspectedSource] };
      }
      if (
        path === "/api/runs/42/outbox-events" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();

    const evidencePanel = page.getByLabel("Logs & files checked");
    const transcriptLink = evidencePanel.getByRole("link", { name: "Transcript #77" });
    await expect(transcriptLink).toHaveAttribute("href", /#transcript-command-77$/);
    await expect(page.locator("#transcript-command-77")).toContainText("journalctl -u nginx --no-pager -n 80");
  });

  test("shows required validation check statuses for the active run", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/runs") {
        return { json: { ...activeRun, status: "validating" } };
      }
      if (path === "/api/runs/42") {
        return { json: { ...activeRun, status: "validating" } };
      }
      if (path === "/api/runs/42/validation-expectations") {
        return { json: validationExpectations };
      }
      if (path === "/api/runs/42/events" || path === "/api/runs/42/outbox-events") {
        return { json: [] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();

    const suitePanel = page.getByLabel("Required validation checks");
    await expect(suitePanel).toBeVisible();
    await expect(suitePanel).toContainText("service_health");
    await expect(suitePanel).toContainText("passed");
    await expect(suitePanel).toContainText("customer_benefit");
    await expect(suitePanel).toContainText("failed");
    await expect(suitePanel).toContainText("logs_clean");
    await expect(suitePanel).toContainText("pending");
  });

  test("saves the draft before submit and refreshes tickets after completed integration", async ({ page }) => {
    const postOrder: string[] = [];
    let savedPayload: Record<string, unknown> | null = null;
    let ticketListRequests = 0;
    let ticketDetailRequests = 0;
    let submitQueued = false;
    const readyRun = { ...activeRun, status: "ready_for_activity", activity_draft: activityDraft };
    const submittedRun = { ...activeRun, status: "submitted", activity_draft: activityDraft };

    await mockApi(page, (path, request) => {
      if (path === "/api/tickets") {
        ticketListRequests += 1;
        return { json: [ticket] };
      }
      if (path === "/api/tickets/7001") {
        ticketDetailRequests += 1;
        return { json: ticket };
      }
      if (path === "/api/runs") {
        return { json: readyRun };
      }
      if (path === "/api/runs/42") {
        return { json: submitQueued ? submittedRun : readyRun };
      }
      if (path === "/api/runs/42/activity/save") {
        postOrder.push("save");
        savedPayload = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
        return { json: savedPayload };
      }
      if (path === "/api/runs/42/activity/submit") {
        postOrder.push("submit");
        submitQueued = true;
        return { json: completedIntegrationRequest };
      }
      if (path === "/api/runs/42/integration-requests") {
        return { json: submitQueued ? [completedIntegrationRequest] : [] };
      }
      if (path === "/api/runs/42/events" || path === "/api/runs/42/outbox-events") {
        return { json: [] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();
    await page.getByRole("textbox", { name: "Summary", exact: true }).fill(
      "Restored the endpoint after nginx bind conflict.",
    );
    await page.getByRole("button", { name: "Submit activity" }).click();

    await expect.poll(() => postOrder).toEqual(["save", "submit"]);
    expect(savedPayload?.summary).toBe("Restored the endpoint after nginx bind conflict.");
    await expect(page.getByLabel("Phoenix integration status")).toContainText("Activity submitted and ticket closed");
    await expect.poll(() => ticketListRequests).toBeGreaterThan(1);
    await expect.poll(() => ticketDetailRequests).toBeGreaterThan(1);
  });

  test("shows partial Phoenix success while the ticket status patch retries", async ({ page }) => {
    let submitQueued = false;

    await mockApi(page, (path) => {
      if (path === "/api/runs") {
        return { json: { ...activeRun, status: "ready_for_activity", activity_draft: activityDraft } };
      }
      if (path === "/api/runs/42") {
        return { json: { ...activeRun, status: "ready_for_activity", activity_draft: activityDraft } };
      }
      if (path === "/api/runs/42/activity/save") {
        return { json: activityDraft };
      }
      if (path === "/api/runs/42/activity/submit") {
        submitQueued = true;
        return { json: partialIntegrationRequest };
      }
      if (path === "/api/runs/42/integration-requests") {
        return { json: submitQueued ? [partialIntegrationRequest] : [] };
      }
      if (path === "/api/runs/42/events" || path === "/api/runs/42/outbox-events") {
        return { json: [] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();
    await page.getByRole("button", { name: "Submit activity" }).click();

    const integrationStatus = page.getByLabel("Phoenix integration status");
    await expect(integrationStatus).toContainText("Activity created; ticket status retrying");
    await expect(integrationStatus).toContainText("Phoenix activity #9001");
    await expect(integrationStatus).toContainText("Phoenix status patch unavailable");
    await expect(page.getByRole("button", { name: "Queued" })).toBeDisabled();
  });
});

test.describe("run event streaming and polling", () => {
  test("live terminal streaming appends sanitized output from SSE", async ({ page }) => {
    const sseBody = `event: terminal.output_chunk\ndata: ${JSON.stringify(terminalOutputEvent)}\n\n`;

    await mockApi(page, (path) => {
      if (path === "/api/runs") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42/events" || path === "/api/runs/42/outbox-events") {
        return { json: [] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { contentType: "text/event-stream", body: sseBody };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();

    const transcript = page.locator(".terminal-transcript");
    await expect(transcript).toContainText("stdout> HTTP/1.1 200 OK");
  });

  test("polling fallback updates the transcript in event id order when the stream fails", async ({ page }) => {
    let eventRequests = 0;
    let streamRequests = 0;

    await mockApi(page, (path) => {
      if (path === "/api/runs") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42/events") {
        eventRequests += 1;
        if (eventRequests < 3) {
          return { json: [] };
        }
        return { json: [planRequestedEvent, approvedEvent] };
      }
      if (path === "/api/runs/42/outbox-events") {
        return { json: [] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        streamRequests += 1;
        return "abort";
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();

    const transcript = page.locator(".terminal-transcript");
    await expect(transcript).toContainText("agent.plan_requested", { timeout: 7000 });
    const transcriptText = await transcript.innerText();

    expect(streamRequests).toBeGreaterThanOrEqual(1);
    expect(eventRequests).toBeGreaterThanOrEqual(3);
    expect(transcriptText.indexOf("[8]")).toBeLessThan(transcriptText.indexOf("[9]"));
    expect(transcriptText).toContain("connection.approved");
    expect(transcriptText).toContain("agent.plan_requested");
  });

  test("keeps run transcripts scoped to the ticket that created them", async ({ page }) => {
    await mockApi(page, (path, request) => {
      if (path === "/api/tickets") {
        return { json: [ticket, databaseTicket] };
      }
      if (path === "/api/tickets/7001") {
        return { json: ticket };
      }
      if (path === "/api/tickets/7001/customer-system") {
        return { json: customerSystem };
      }
      if (path === "/api/tickets/7002") {
        return { json: databaseTicket };
      }
      if (path === "/api/tickets/7002/customer-system") {
        return { json: databaseCustomerSystem };
      }
      if (path === "/api/runs") {
        const payload = JSON.parse(request.postData() ?? "{}") as { ticket_id?: number };
        return { json: payload.ticket_id === 7002 ? databaseRun : activeRun };
      }
      if (path === "/api/runs/42") {
        return { json: activeRun };
      }
      if (path === "/api/runs/43") {
        return { json: databaseRun };
      }
      if (path === "/api/runs/42/events") {
        return { json: [commandStartedEvent, terminalOutputEvent] };
      }
      if (path === "/api/runs/43/events") {
        return { json: [databaseCommandStartedEvent, databaseTerminalOutputEvent] };
      }
      if (
        path === "/api/runs/42/outbox-events" ||
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations" ||
        path === "/api/runs/42/integration-requests" ||
        path === "/api/runs/43/outbox-events" ||
        path === "/api/runs/43/evidence" ||
        path === "/api/runs/43/backups" ||
        path === "/api/runs/43/validation-results" ||
        path === "/api/runs/43/validation-expectations" ||
        path === "/api/runs/43/integration-requests"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream" || path === "/api/runs/43/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();

    const transcript = page.locator(".terminal-transcript");
    await expect(transcript).toContainText("HTTP/1.1 200 OK");

    await page.getByRole("button", { name: /Database latency/ }).click();
    await expect(transcript).not.toContainText("HTTP/1.1 200 OK");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();
    await expect(transcript).toContainText("127.0.0.1:5432");
    await expect(transcript).not.toContainText("HTTP/1.1 200 OK");

    await page.getByRole("button", { name: /API down/ }).click();
    await expect(transcript).toContainText("HTTP/1.1 200 OK");
    await expect(transcript).not.toContainText("127.0.0.1:5432");
  });
});

test.describe("manual command controls", () => {
  test("submits the selected manual command phase and timeout", async ({ page }) => {
    let manualPayload: Record<string, unknown> | null = null;

    await mockApi(page, (path, request) => {
      if (path === "/api/runs") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42/manual-step") {
        manualPayload = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
        return { json: {} };
      }
      if (path === "/api/runs/42/events" || path === "/api/runs/42/outbox-events") {
        return { json: [] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();
    await page.getByLabel("Phase").click();
    await page.getByRole("option", { name: "Fix" }).click();
    await page.getByLabel("Timeout").fill("120");
    await page.getByLabel("Command").fill("systemctl restart nginx");
    await page.getByRole("button", { name: "Queue" }).click();

    await expect.poll(() => manualPayload?.phase).toBe("fix");
    expect(manualPayload?.command).toBe("systemctl restart nginx");
    expect(manualPayload?.timeout_s).toBe(120);
  });

  test("shows blocked manual commands without execution actions", async ({ page }) => {
    await mockApi(page, (path) => {
      if (path === "/api/runs") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42") {
        return { json: activeRun };
      }
      if (path === "/api/runs/42/manual-step") {
        return { json: blockedManualStep };
      }
      if (path === "/api/runs/42/events" || path === "/api/runs/42/outbox-events") {
        return { json: [] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/backups" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();
    await page.getByLabel("Command").fill("cat /etc/shadow");
    await page.getByRole("button", { name: "Queue" }).click();

    const blockedPanel = page.getByLabel("Blocked command");
    await expect(blockedPanel).toBeVisible();
    await expect(blockedPanel).toContainText("cat /etc/shadow");
    await expect(blockedPanel).toContainText("Reading likely secret material is blocked.");
    await expect(blockedPanel.getByRole("button", { name: "Approve" })).toHaveCount(0);
    await expect(blockedPanel.getByRole("button", { name: "Edit & approve" })).toHaveCount(0);
  });
});

test.describe("pending command human controls", () => {
  test("covers approve edit reject retry and abort actions", async ({ page }) => {
    let runState = runAwaitingFixApproval;
    const calls: Record<string, number> = {
      approve: 0,
      edit: 0,
      reject: 0,
      retry: 0,
      abort: 0,
    };
    let editedPayload: Record<string, unknown> | null = null;
    let rejectedPayload: Record<string, unknown> | null = null;

    await mockApi(page, (path, request) => {
      if (path === "/api/runs") {
        return { json: runState };
      }
      if (path === "/api/runs/42") {
        return { json: runState };
      }
      if (path === "/api/runs/42/steps/90/approve") {
        calls.approve += 1;
        runState = { ...activeRun, status: "executing" };
        return { json: runState };
      }
      if (path === "/api/runs/42/steps/90/edit-and-approve") {
        calls.edit += 1;
        editedPayload = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
        runState = { ...activeRun, status: "executing" };
        return { json: runState };
      }
      if (path === "/api/runs/42/steps/90/reject") {
        calls.reject += 1;
        rejectedPayload = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
        runState = activeRun;
        return { json: runState };
      }
      if (path === "/api/runs/42/retry") {
        calls.retry += 1;
        runState = activeRun;
        return { json: runState };
      }
      if (path === "/api/runs/42/abort") {
        calls.abort += 1;
        runState = { ...activeRun, status: "aborted" };
        return { json: runState };
      }
      if (path === "/api/runs/42/events") {
        return { json: [backupPlannedEvent] };
      }
      if (path === "/api/runs/42/backups") {
        return { json: [plannedBackupRecord] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/outbox-events" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations" ||
        path === "/api/runs/42/integration-requests"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    async function openPendingRun() {
      runState = runAwaitingFixApproval;
      await page.goto("/");
      await page.getByRole("button", { name: "Start troubleshooting" }).click();
      await expect(page.getByLabel("Pending command approval")).toBeVisible();
    }

    await openPendingRun();
    await page.getByLabel("Pending command approval").getByRole("button", { name: "Approve", exact: true }).click();
    await expect.poll(() => calls.approve).toBe(1);

    await openPendingRun();
    await page.getByLabel("Approved command").fill("systemctl restart nginx");
    await page.getByLabel("Pending command approval").getByRole("button", { name: "Edit & approve" }).click();
    await expect.poll(() => calls.edit).toBe(1);
    expect(editedPayload?.command).toBe("systemctl restart nginx");

    await openPendingRun();
    await page.getByLabel("Reject reason").fill("Need a safer diagnostic first.");
    await page.getByLabel("Pending command approval").getByRole("button", { name: "Reject" }).click();
    await expect.poll(() => calls.reject).toBe(1);
    expect(rejectedPayload?.reason).toBe("Need a safer diagnostic first.");

    await openPendingRun();
    await page.getByRole("button", { name: "Retry" }).click();
    await expect.poll(() => calls.retry).toBe(1);

    await openPendingRun();
    await page.getByRole("button", { name: "Abort" }).first().click();
    await expect.poll(() => calls.abort).toBe(1);
  });
});

test.describe("backup approval controls", () => {
  test("records backup not applicable and immediately approves the pending fix", async ({ page }) => {
    let notApplicablePayload: Record<string, unknown> | null = null;
    let notApplicableRecorded = false;
    let approved = false;

    await mockApi(page, (path, request) => {
      if (path === "/api/runs") {
        return { json: runAwaitingFixApproval };
      }
      if (path === "/api/runs/42") {
        return {
          json: approved
            ? { ...runAwaitingFixApproval, status: "fixing", pending_step: null }
            : runAwaitingFixApproval,
        };
      }
      if (path === "/api/runs/42/events") {
        return { json: [backupPlannedEvent] };
      }
      if (path === "/api/runs/42/backups") {
        return { json: notApplicableRecorded ? [plannedBackupRecord, notApplicableBackupRecord] : [plannedBackupRecord] };
      }
      if (path === "/api/runs/42/backups/not-applicable") {
        notApplicablePayload = JSON.parse(request.postData() ?? "{}") as Record<string, unknown>;
        notApplicableRecorded = true;
        return { status: 201, json: notApplicableBackupRecord };
      }
      if (path === "/api/runs/42/steps/90/approve") {
        approved = true;
        return {
          json: { ...runAwaitingFixApproval, status: "fixing", pending_step: null },
        };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/outbox-events" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();

    const approvalCard = page.getByLabel("Pending command approval");
    await expect(approvalCard).toContainText("Backup state: missing");
    await approvalCard.getByLabel("Not applicable reason").fill("Disposable demo config; rollback is not applicable.");
    await approvalCard.getByRole("button", { name: "Mark not applicable" }).click();

    await expect.poll(() => notApplicablePayload?.source_path).toBe("/etc/nginx/sites-enabled/default");
    expect(notApplicablePayload?.reason).toBe("Disposable demo config; rollback is not applicable.");
    await expect.poll(() => approved).toBe(true);
    await expect(approvalCard).toHaveCount(0);
    const artifactDir = process.env.TECHBOLD_VISUAL_ARTIFACT_DIR ?? "/tmp/techbold-frontend-verification";
    fs.mkdirSync(artifactDir, { recursive: true });
    await page.screenshot({
      path: `${artifactDir}/backup-not-applicable-auto-approved.png`,
      fullPage: true,
    });
  });

  test("shows created backup state on pending fix approval cards", async ({ page }) => {
    const createdBackupRecord = {
      ...plannedBackupRecord,
      backup_created: true,
      command_execution_id: 77,
      created_at: "2026-06-06T10:07:00Z",
    };

    await mockApi(page, (path) => {
      if (path === "/api/runs") {
        return { json: runAwaitingFixApproval };
      }
      if (path === "/api/runs/42") {
        return { json: runAwaitingFixApproval };
      }
      if (path === "/api/runs/42/events") {
        return { json: [backupPlannedEvent] };
      }
      if (path === "/api/runs/42/backups") {
        return { json: [createdBackupRecord] };
      }
      if (
        path === "/api/runs/42/evidence" ||
        path === "/api/runs/42/outbox-events" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/validation-expectations"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();

    const approvalCard = page.getByLabel("Pending command approval");
    await expect(approvalCard).toContainText("Backup state: created");
    await expect(approvalCard.getByRole("button", { name: "Mark not applicable" })).toHaveCount(0);
  });
});

test.describe("frontend visual verification artifacts", () => {
  test("captures desktop and mobile ticket list detail and run console", async ({ page }) => {
    const artifactDir = process.env.TECHBOLD_VISUAL_ARTIFACT_DIR ?? "/tmp/techbold-frontend-verification";
    fs.mkdirSync(artifactDir, { recursive: true });

    await mockApi(page, (path) => {
      if (path === "/api/runs") {
        return { json: runAwaitingFixApproval };
      }
      if (path === "/api/runs/42") {
        return { json: runAwaitingFixApproval };
      }
      if (path === "/api/runs/42/events") {
        return { json: [approvedEvent, planRequestedEvent, commandStartedEvent, terminalOutputEvent, backupPlannedEvent] };
      }
      if (path === "/api/runs/42/evidence") {
        return { json: [inspectedSource] };
      }
      if (path === "/api/runs/42/backups") {
        return { json: [plannedBackupRecord] };
      }
      if (path === "/api/runs/42/validation-expectations") {
        return { json: validationExpectations };
      }
      if (
        path === "/api/runs/42/outbox-events" ||
        path === "/api/runs/42/validation-results" ||
        path === "/api/runs/42/integration-requests"
      ) {
        return { json: [] };
      }
      if (path === "/api/runs/42/stream") {
        return { status: 204, json: {} };
      }
      return healthyApi(path);
    });

    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page.getByRole("button", { name: "Start troubleshooting" }).click();
    await expect(page.getByLabel("Ticket overview")).toBeVisible();
    await expect(page.getByLabel("Ticket detail")).toBeVisible();
    await expect(page.getByLabel("Run console")).toBeVisible();
    await page.screenshot({
      path: `${artifactDir}/desktop-ticket-detail-run-console.png`,
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 1100 });
    await expect(page.getByLabel("Run console")).toBeVisible();
    await page.screenshot({
      path: `${artifactDir}/mobile-ticket-detail-run-console.png`,
      fullPage: true,
    });
  });
});
