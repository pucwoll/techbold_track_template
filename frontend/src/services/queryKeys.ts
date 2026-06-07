import type { TicketStatus } from "./backendClient";

export const queryKeys = {
  me: ["me"] as const,
  tickets: {
    all: ["tickets"] as const,
    list: (filters: { status: TicketStatus | ""; priority: string; sort: string }) =>
      ["tickets", "list", filters] as const,
    detail: (ticketId: number) => ["tickets", "detail", ticketId] as const,
    customerSystem: (ticketId: number) => ["tickets", "customer-system", ticketId] as const,
  },
  runs: {
    all: ["runs"] as const,
    detail: (runId: number) => ["runs", "detail", runId] as const,
    events: (runId: number) => ["runs", "events", runId] as const,
    outboxEvents: (runId: number) => ["runs", "outbox-events", runId] as const,
    evidence: (runId: number) => ["runs", "evidence", runId] as const,
    backups: (runId: number) => ["runs", "backups", runId] as const,
    validationResults: (runId: number) => ["runs", "validation-results", runId] as const,
    validationExpectations: (runId: number) => ["runs", "validation-expectations", runId] as const,
    integrationRequests: (runId: number) => ["runs", "integration-requests", runId] as const,
  },
};
