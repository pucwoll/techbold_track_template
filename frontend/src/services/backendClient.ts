import createClient from "openapi-fetch";

import type { components, paths } from "../generated/openapi";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

const client = createClient<paths>({ baseUrl: API_BASE });

export type ActivityDraft = components["schemas"]["ActivityDraft"];
export type BackupRecord = components["schemas"]["BackupRecord"];
export type CustomerSystem = components["schemas"]["CustomerSystem"];
export type Employee = components["schemas"]["Employee"];
export type InspectedSource = components["schemas"]["InspectedSource"];
export type IntegrationRequest = components["schemas"]["IntegrationRequest"];
export type IntegrationRequestStatus = components["schemas"]["IntegrationRequestStatus"];
export type OutboxEvent = components["schemas"]["OutboxEvent"];
export type ProposedStep = components["schemas"]["ProposedStep"];
export type Run = components["schemas"]["Run"];
export type RunEvent = components["schemas"]["RunEvent"];
export type RunStatus = components["schemas"]["RunStatus"];
export type Ticket = components["schemas"]["Ticket"];
export type TicketStatus = components["schemas"]["TicketStatus"];
export type ValidationExpectation = components["schemas"]["ValidationExpectation"];
export type ValidationResult = components["schemas"]["ValidationResult"];

export class ApiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type ApiResult<T> = Promise<{
  data?: T;
  error?: unknown;
  response: Response;
}>;

async function readApiResult<T>(request: ApiResult<T>): Promise<T> {
  try {
    const { data, error, response } = await request;
    if (error) {
      throw new ApiError(errorMessage(error, response), response.status);
    }
    if (data === undefined) {
      throw new ApiError(`${response.status} ${response.statusText}`, response.status);
    }
    return data;
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }
    console.debug("Backend request failed", error);
    throw new ApiError("Backend unavailable");
  }
}

function errorMessage(error: unknown, response: Response): string {
  if (error && typeof error === "object" && "detail" in error) {
    const detail = (error as { detail?: unknown }).detail;
    if (typeof detail === "string") {
      return detail;
    }
  }
  return `${response.status} ${response.statusText}`;
}

export function userFacingError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return "Phoenix authentication failed.";
    }
    if (error.status === 404) {
      return "Phoenix returned 404 for this resource.";
    }
    if (error.status === 503) {
      return error.message;
    }
    return error.message || "Request failed.";
  }
  return error instanceof Error ? error.message : "Request failed.";
}

export function getMe() {
  return readApiResult(client.GET("/api/me"));
}

export function listTickets(filters: { status?: TicketStatus | ""; priority?: string; sort: string }) {
  return readApiResult(
    client.GET("/api/tickets", {
      params: {
        query: {
          status: filters.status || undefined,
          priority: filters.priority || undefined,
          sort: filters.sort,
        },
      },
    }),
  );
}

export function getTicket(ticketId: number) {
  return readApiResult(client.GET("/api/tickets/{ticket_id}", { params: { path: { ticket_id: ticketId } } }));
}

export function getCustomerSystem(ticketId: number) {
  return readApiResult(
    client.GET("/api/tickets/{ticket_id}/customer-system", {
      params: { path: { ticket_id: ticketId } },
    }),
  );
}

export function startRun(ticketId: number) {
  return readApiResult(client.POST("/api/runs", { body: { ticket_id: ticketId } }));
}

export function getRun(runId: number) {
  return readApiResult(client.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } }));
}

export function approveConnection(runId: number, approvedBy: string) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/connect/approve", {
      params: { path: { run_id: runId } },
      body: { approved_by: approvedBy },
    }),
  );
}

export function submitManualStep(
  runId: number,
  payload: {
    command: string;
    entered_by: string;
    purpose: string;
    phase: string;
    timeout_s: number;
  },
) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/manual-step", {
      params: { path: { run_id: runId } },
      body: payload,
    }),
  );
}

export function getRunEvents(runId: number, afterId = 0) {
  return readApiResult(
    client.GET("/api/runs/{run_id}/events", {
      params: { path: { run_id: runId }, query: { after_id: afterId } },
    }),
  );
}

export function getOutboxEvents(runId: number) {
  return readApiResult(client.GET("/api/runs/{run_id}/outbox-events", { params: { path: { run_id: runId } } }));
}

export function getEvidence(runId: number) {
  return readApiResult(client.GET("/api/runs/{run_id}/evidence", { params: { path: { run_id: runId } } }));
}

export function getBackups(runId: number) {
  return readApiResult(client.GET("/api/runs/{run_id}/backups", { params: { path: { run_id: runId } } }));
}

export function getValidationResults(runId: number) {
  return readApiResult(client.GET("/api/runs/{run_id}/validation-results", { params: { path: { run_id: runId } } }));
}

export function getValidationExpectations(runId: number) {
  return readApiResult(
    client.GET("/api/runs/{run_id}/validation-expectations", { params: { path: { run_id: runId } } }),
  );
}

export function getIntegrationRequests(runId: number) {
  return readApiResult(
    client.GET("/api/runs/{run_id}/integration-requests", { params: { path: { run_id: runId } } }),
  );
}

export function approveStep(runId: number, stepId: number, approvedBy: string) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/steps/{step_id}/approve", {
      params: { path: { run_id: runId, step_id: stepId } },
      body: { approved_by: approvedBy },
    }),
  );
}

export function editAndApproveStep(
  runId: number,
  stepId: number,
  payload: {
    command: string;
    approved_by: string;
    purpose?: string | null;
    expected_signal?: string | null;
  },
) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/steps/{step_id}/edit-and-approve", {
      params: { path: { run_id: runId, step_id: stepId } },
      body: payload,
    }),
  );
}

export function rejectStep(runId: number, stepId: number, rejectedBy: string, reason: string) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/steps/{step_id}/reject", {
      params: { path: { run_id: runId, step_id: stepId } },
      body: { rejected_by: rejectedBy, reason },
    }),
  );
}

export function recordBackupNotApplicable(runId: number, sourcePath: string | null, reason: string, recordedBy: string) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/backups/not-applicable", {
      params: { path: { run_id: runId } },
      body: { source_path: sourcePath, reason, recorded_by: recordedBy },
    }),
  );
}

export function retryRun(runId: number, requestedBy: string) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/retry", {
      params: { path: { run_id: runId } },
      body: { requested_by: requestedBy, reason: "Retry requested by technician." },
    }),
  );
}

export function abortRun(runId: number, abortedBy: string) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/abort", {
      params: { path: { run_id: runId } },
      body: { aborted_by: abortedBy, reason: "Aborted from run console." },
    }),
  );
}

export function draftActivity(runId: number) {
  return readApiResult(client.POST("/api/runs/{run_id}/activity/draft", { params: { path: { run_id: runId } } }));
}

export function saveActivityDraft(runId: number, draft: ActivityDraft, editedBy: string) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/activity/save", {
      params: { path: { run_id: runId }, query: { edited_by: editedBy } },
      body: draft,
    }),
  );
}

export function submitActivity(runId: number) {
  return readApiResult(
    client.POST("/api/runs/{run_id}/activity/submit", {
      params: { path: { run_id: runId } },
    }),
  );
}
