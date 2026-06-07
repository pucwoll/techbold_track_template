import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  ClipboardCheck,
  Database,
  FileText,
  ListChecks,
  Play,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  Terminal,
  Ticket as TicketIcon,
  XCircle,
} from "lucide-react";
import { Controller, useForm } from "react-hook-form";
import { z } from "zod";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  ApiError,
  API_BASE,
  abortRun,
  approveConnection,
  approveStep,
  draftActivity,
  editAndApproveStep,
  getBackups,
  getCustomerSystem,
  getEvidence,
  getIntegrationRequests,
  getMe,
  getOutboxEvents,
  getRun,
  getRunEvents,
  getTicket,
  getValidationExpectations,
  getValidationResults,
  listTickets,
  recordBackupNotApplicable,
  rejectStep,
  retryRun,
  saveActivityDraft,
  startRun,
  submitActivity,
  submitManualStep,
  userFacingError,
  type ActivityDraft,
  type BackupRecord,
  type CustomerSystem,
  type Employee,
  type InspectedSource,
  type IntegrationRequest,
  type IntegrationRequestStatus,
  type OutboxEvent,
  type ProposedStep,
  type Run,
  type RunEvent,
  type RunStatus,
  type Ticket,
  type TicketStatus,
  type ValidationExpectation,
  type ValidationResult,
} from "./services/backendClient";
import { queryKeys } from "./services/queryKeys";

type DetailState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; ticket: Ticket; customerSystem: CustomerSystem }
  | { status: "error"; message: string };

function formatDate(value?: string | null): string {
  if (!value) {
    return "Not set";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function priorityTone(priority: string): string {
  const normalized = priority.toLowerCase();
  if (["critical", "urgent", "high"].includes(normalized)) {
    return "danger";
  }
  if (["medium", "normal"].includes(normalized)) {
    return "warn";
  }
  return "calm";
}

function toneBadgeClass(tone: string): string {
  const tones: Record<string, string> = {
    danger: "border-transparent bg-red-100 text-red-800",
    warn: "border-transparent bg-amber-100 text-amber-800",
    calm: "border-transparent bg-emerald-100 text-emerald-800",
    neutral: "border-transparent bg-sky-100 text-sky-800",
  };
  return tones[tone] ?? tones.neutral;
}

function riskBadgeClass(riskClass: string): string {
  const normalized = riskClass.toLowerCase().replace("_", "-");
  const tones: Record<string, string> = {
    "read-only": "border-emerald-300/30 bg-emerald-400/15 text-emerald-100",
    "low-risk": "border-sky-300/30 bg-sky-400/15 text-sky-100",
    "medium-risk": "border-amber-300/35 bg-amber-400/15 text-amber-100",
    blocked: "border-red-300/35 bg-red-400/15 text-red-100",
  };
  return tones[normalized] ?? "border-white/20 bg-white/10 text-console-foreground";
}

function PriorityBadge({ priority }: { priority: string }) {
  return <Badge className={cn("max-w-[120px] break-words", toneBadgeClass(priorityTone(priority)))}>{priority}</Badge>;
}

function StatusBadge({ status }: { status: string }) {
  return <Badge className={cn("max-w-[120px] break-words", toneBadgeClass("neutral"))}>{status}</Badge>;
}

function StateBlock({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "error" }) {
  if (tone === "error") {
    return (
      <Alert variant="destructive" className="state-block error-state">
        <AlertTriangle className="size-4" aria-hidden="true" />
        <AlertDescription>{children}</AlertDescription>
      </Alert>
    );
  }

  return (
    <Alert className="state-block">
      <AlertDescription>{children}</AlertDescription>
    </Alert>
  );
}

function IconButton({
  label,
  children,
  onClick,
}: {
  label: string;
  children: ReactNode;
  onClick: () => void;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button aria-label={label} className="size-[38px]" size="icon" type="button" variant="outline" onClick={onClick}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

const consoleButtonClass =
  "border-white/20 bg-white/10 text-console-foreground hover:bg-white hover:text-console disabled:hover:bg-white/10 disabled:hover:text-console-foreground";

const consolePrimaryButtonClass =
  "bg-white text-console hover:bg-emerald-100 disabled:hover:bg-white disabled:hover:text-console";

const consoleFieldControlClass =
  "border-white/15 bg-black/45 text-console-foreground placeholder:text-console-muted focus-visible:ring-emerald-300";

const fieldErrorClass = "text-xs font-semibold text-red-200";

const manualStepDefaults = {
  command: "",
  purpose: "Manual technician command.",
  phase: "diagnostic",
  timeoutS: 30,
} as const;

const manualStepSchema = z.object({
  command: z.string().trim().min(1, "Command is required.").max(4_000, "Command is too long."),
  purpose: z.string().trim().min(1, "Purpose is required.").max(1_000, "Purpose is too long."),
  phase: z.enum(["diagnostic", "fix", "validation"]),
  timeoutS: z.number().int("Timeout must be a whole number.").min(1, "Timeout must be at least 1 second.").max(600, "Timeout must be 600 seconds or less."),
});

const backupNotApplicableSchema = z.object({
  reason: z.string().trim().min(1, "Reason is required.").max(1_000, "Reason is too long."),
});

const defaultBackupNotApplicableReason = "Targeted rollback is not applicable for this change.";

const activityDraftFormSchema = z.object({
  summary: z.string().trim().min(1, "Summary is required.").max(2_000, "Summary is too long."),
  root_cause: z.string().trim().min(1, "Root cause is required.").max(2_000, "Root cause is too long."),
  actions_taken: z.string().trim().min(1, "Actions taken are required.").max(4_000, "Actions taken are too long."),
  commands_summary: z.string().trim().min(1, "Commands summary is required.").max(4_000, "Commands summary is too long."),
  validation_result: z.string().trim().min(1, "Validation result is required.").max(2_000, "Validation result is too long."),
});

type ManualStepForm = z.infer<typeof manualStepSchema>;
type BackupNotApplicableForm = z.infer<typeof backupNotApplicableSchema>;
type ActivityDraftForm = z.infer<typeof activityDraftFormSchema>;

function activityDraftFormDefaults(draft: ActivityDraft | null): ActivityDraftForm {
  return {
    summary: draft?.summary ?? "",
    root_cause: draft?.root_cause ?? "",
    actions_taken: draft?.actions_taken ?? "",
    commands_summary: draft?.commands_summary ?? "",
    validation_result: draft?.validation_result ?? "",
  };
}

function FieldError({ message }: { message?: string }) {
  return message ? (
    <span className={fieldErrorClass} role="alert">
      {message}
    </span>
  ) : null;
}

function readableRunStatus(status?: RunStatus): string {
  const labels: Record<RunStatus, string> = {
    awaiting_connection_approval: "Awaiting connection approval",
    investigating: "Investigating",
    planning: "Planning diagnostics",
    awaiting_step_approval: "Awaiting command approval",
    executing: "Executing command",
    fixing: "Applying fix",
    validating: "Validating fix",
    ready_for_activity: "Ready for activity",
    submitted: "Submitted",
    aborted: "Aborted",
    failed: "Failed",
  };
  return status ? labels[status] : "Standby";
}

function mergeRunEvents(current: RunEvent[], incoming: RunEvent[]): RunEvent[] {
  const seen = new Set(current.map((event) => event.id));
  return [...current, ...incoming.filter((event) => !seen.has(event.id))].sort((a, b) => a.id - b.id);
}

function upsertIntegrationRequest(current: IntegrationRequest[], request: IntegrationRequest): IntegrationRequest[] {
  const withoutRequest = current.filter((existing) => existing.id !== request.id);
  return [...withoutRequest, request].sort((a, b) => a.id - b.id);
}

function latestCompletedIntegrationRequest(requests: IntegrationRequest[]): IntegrationRequest | null {
  return [...requests].reverse().find((request) => request.status === "completed") ?? null;
}

function integrationStatusHeading(request: IntegrationRequest): string {
  const labels: Record<IntegrationRequestStatus, string> = {
    pending: "Queued for Phoenix",
    processing: "Phoenix worker running",
    activity_created: "Activity created; ticket status retrying",
    completed: "Activity submitted and ticket closed",
    failed: "Phoenix submission retry scheduled",
    dead_letter: "Phoenix submission needs review",
  };
  return labels[request.status];
}

function integrationStatusCopy(request: IntegrationRequest): string {
  if (request.status === "activity_created") {
    return "The Phoenix activity exists, but the ticket still needs the DONE status patch.";
  }
  if (request.status === "completed") {
    return "The activity write and ticket DONE update both completed.";
  }
  if (request.status === "dead_letter") {
    return "Worker retries were exhausted. Review the error before submitting another request.";
  }
  if (request.status === "failed") {
    return "The worker recorded a retryable integration failure.";
  }
  return "The saved activity draft is waiting for durable worker processing.";
}

function eventPayloadText(event: RunEvent, key: string): string | null {
  const value = event.payload?.[key];
  return typeof value === "string" ? value : null;
}

function formatRunEvent(event: RunEvent): string {
  if (event.event_type === "terminal.output_chunk") {
    const stream = eventPayloadText(event, "stream") ?? "stdout";
    const content = eventPayloadText(event, "content") ?? "";
    return `${stream}> ${content}`;
  }
  if (event.event_type === "terminal.output_truncated") {
    return `[output truncated at configured cap]`;
  }
  if (event.event_type === "command.started") {
    return `$ ${event.command ?? ""}`;
  }
  if (["command.completed", "command.failed", "command.timed_out"].includes(event.event_type)) {
    const exit = event.exit_code === null || event.exit_code === undefined ? "n/a" : String(event.exit_code);
    const duration = event.duration_ms === null || event.duration_ms === undefined ? "n/a" : `${event.duration_ms} ms`;
    return `[exit ${exit}, ${duration}] ${event.summary}`;
  }
  const approval = event.approval_status ? ` · ${event.approval_status}` : "";
  const command = event.command ? `\n$ ${event.command}` : "";
  return `[${event.id}] ${formatDate(event.created_at)} · ${event.actor} · ${event.event_type}${approval}\n${event.summary}${command}`;
}

function commandExecutionIdFromEvent(event: RunEvent): number | null {
  const value = event.payload?.command_execution_id;
  return typeof value === "number" ? value : null;
}

function transcriptAnchorId(commandExecutionId: number): string {
  return `transcript-command-${commandExecutionId}`;
}

function runTarget(run: Run | null): Partial<{ ip: string; port: number; username: string; os: string }> {
  const snapshot = run?.customer_system_snapshot;
  const system =
    snapshot && typeof snapshot === "object"
      ? (snapshot as { system?: unknown }).system
      : null;
  if (!system || typeof system !== "object") {
    return {};
  }
  return system as Partial<{ ip: string; port: number; username: string; os: string }>;
}

function pendingStepFromRun(run: Run | null): ProposedStep | null {
  const step = run?.pending_step;
  if (!step || typeof step !== "object") {
    return null;
  }
  const maybeStep = step as Partial<ProposedStep>;
  if (typeof maybeStep.id !== "number" || typeof maybeStep.command !== "string") {
    return null;
  }
  return {
    id: maybeStep.id,
    run_id: maybeStep.run_id ?? run?.id ?? 0,
    created_at: maybeStep.created_at ?? run?.started_at ?? "",
    updated_at: maybeStep.updated_at ?? null,
    source: maybeStep.source === "manual" ? "manual" : "agent",
    phase: maybeStep.phase ?? "diagnostic",
    command: maybeStep.command,
    purpose: maybeStep.purpose ?? "Command proposed.",
    expected_signal: maybeStep.expected_signal ?? null,
    risk_class: maybeStep.risk_class ?? "UNKNOWN",
    safety_verdict: maybeStep.safety_verdict === "blocked" ? "blocked" : "allowed",
    safety_summary: maybeStep.safety_summary ?? "Safety classification recorded.",
    safety_notes: Array.isArray(maybeStep.safety_notes) ? maybeStep.safety_notes.filter((note): note is string => typeof note === "string") : [],
    status: maybeStep.status ?? "proposed",
    timeout_s: maybeStep.timeout_s ?? 30,
  };
}

const RUN_EVENT_TYPES = [
  "run.created",
  "connection.approval_requested",
  "connection.approved",
  "agent.plan_requested",
  "agent.plan_skipped",
  "agent.context_built",
  "agent.prompt_submitted",
  "agent.output_received",
  "agent.output_invalid",
  "agent.step_selected",
  "agent.fallback_used",
  "agent.activity_draft_generated",
  "step.proposed",
  "step.safety_classified",
  "step.approved",
  "step.edited_and_approved",
  "step.rejected",
  "evidence.source_detected",
  "evidence.source_redacted",
  "backup.approval_requested",
  "backup.created",
  "backup.not_applicable",
  "validation.required",
  "validation.passed",
  "validation.failed",
  "command.execution_requested",
  "command.started",
  "terminal.output_chunk",
  "terminal.output_truncated",
  "command.completed",
  "command.failed",
  "command.timed_out",
  "command.skipped",
  "manual_step.entered",
  "run.retry_requested",
  "run.aborted",
  "activity.draft_edited",
  "activity.submission_requested",
  "integration.activity_created",
  "integration.failed",
  "activity.submitted",
  "ticket.status_updated",
  "outbox.ignored",
  "outbox.recovered",
];

export default function App() {
  const queryClient = useQueryClient();
  const [selectedTicketId, setSelectedTicketId] = useState<number | null>(null);
  const [statusFilter, setStatusFilter] = useState<"" | TicketStatus>("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [sort, setSort] = useState("date");
  const [activeRunIdsByTicket, setActiveRunIdsByTicket] = useState<Record<number, number>>({});
  const [recentBlockedStepsByRun, setRecentBlockedStepsByRun] = useState<Record<number, ProposedStep>>({});
  const lastCompletedIntegrationRequestIdRef = useRef<number | null>(null);

  const ticketFilters = useMemo(
    () => ({ status: statusFilter, priority: priorityFilter, sort }),
    [priorityFilter, sort, statusFilter],
  );
  const selectedQueryTicketId = selectedTicketId ?? 0;
  const activeRunId = selectedTicketId === null ? null : (activeRunIdsByTicket[selectedTicketId] ?? null);
  const activeQueryRunId = activeRunId ?? 0;
  const hasSelectedTicket = selectedTicketId !== null;
  const hasActiveRun = activeRunId !== null;

  const meQuery = useQuery({
    queryKey: queryKeys.me,
    queryFn: getMe,
  });
  const ticketListQuery = useQuery({
    queryKey: queryKeys.tickets.list(ticketFilters),
    queryFn: () => listTickets(ticketFilters),
  });
  const workspaceError = meQuery.error ?? ticketListQuery.error;
  const me = workspaceError ? null : (meQuery.data ?? null);
  const tickets = workspaceError ? [] : (ticketListQuery.data ?? []);
  const listLoading = meQuery.isPending || ticketListQuery.isPending;
  const listError = workspaceError ? userFacingError(workspaceError) : null;

  useEffect(() => {
    if (workspaceError) {
      setSelectedTicketId(null);
      return;
    }
    if (!ticketListQuery.data) {
      return;
    }
    setSelectedTicketId((current) => {
      if (ticketListQuery.data.length === 0) return null;
      if (current && ticketListQuery.data.some((ticket) => ticket.id === current)) return current;
      return ticketListQuery.data[0].id;
    });
  }, [ticketListQuery.data, workspaceError]);

  const selectedTicket = useMemo(
    () => tickets.find((ticket) => ticket.id === selectedTicketId) ?? null,
    [selectedTicketId, tickets],
  );

  const ticketDetailQuery = useQuery({
    queryKey: queryKeys.tickets.detail(selectedQueryTicketId),
    queryFn: () => getTicket(selectedQueryTicketId),
    enabled: hasSelectedTicket,
  });
  const customerSystemQuery = useQuery({
    queryKey: queryKeys.tickets.customerSystem(selectedQueryTicketId),
    queryFn: () => getCustomerSystem(selectedQueryTicketId),
    enabled: hasSelectedTicket,
  });
  const detailState: DetailState = !hasSelectedTicket
    ? { status: "idle" }
    : ticketDetailQuery.isPending || customerSystemQuery.isPending
      ? { status: "loading" }
      : ticketDetailQuery.error || customerSystemQuery.error
        ? { status: "error", message: userFacingError(ticketDetailQuery.error ?? customerSystemQuery.error) }
        : ticketDetailQuery.data && customerSystemQuery.data
          ? { status: "ready", ticket: ticketDetailQuery.data, customerSystem: customerSystemQuery.data }
          : { status: "loading" };

  useEffect(() => {
    lastCompletedIntegrationRequestIdRef.current = null;
  }, [selectedTicketId]);

  const activeRunQuery = useQuery({
    queryKey: queryKeys.runs.detail(activeQueryRunId),
    queryFn: () => getRun(activeQueryRunId),
    enabled: hasActiveRun,
    refetchInterval: hasActiveRun ? 2000 : false,
  });
  const runEventsQuery = useQuery({
    queryKey: queryKeys.runs.events(activeQueryRunId),
    queryFn: () => getRunEvents(activeQueryRunId),
    enabled: hasActiveRun,
    refetchInterval: hasActiveRun ? 2000 : false,
  });
  const outboxEventsQuery = useQuery({
    queryKey: queryKeys.runs.outboxEvents(activeQueryRunId),
    queryFn: () => getOutboxEvents(activeQueryRunId),
    enabled: hasActiveRun,
    refetchInterval: hasActiveRun ? 2000 : false,
  });
  const evidenceQuery = useQuery({
    queryKey: queryKeys.runs.evidence(activeQueryRunId),
    queryFn: () => getEvidence(activeQueryRunId),
    enabled: hasActiveRun,
    refetchInterval: hasActiveRun ? 2000 : false,
  });
  const backupsQuery = useQuery({
    queryKey: queryKeys.runs.backups(activeQueryRunId),
    queryFn: () => getBackups(activeQueryRunId),
    enabled: hasActiveRun,
    refetchInterval: hasActiveRun ? 2000 : false,
  });
  const validationResultsQuery = useQuery({
    queryKey: queryKeys.runs.validationResults(activeQueryRunId),
    queryFn: () => getValidationResults(activeQueryRunId),
    enabled: hasActiveRun,
    refetchInterval: hasActiveRun ? 2000 : false,
  });
  const validationExpectationsQuery = useQuery({
    queryKey: queryKeys.runs.validationExpectations(activeQueryRunId),
    queryFn: () => getValidationExpectations(activeQueryRunId),
    enabled: hasActiveRun,
    refetchInterval: hasActiveRun ? 2000 : false,
  });
  const integrationRequestsQuery = useQuery({
    queryKey: queryKeys.runs.integrationRequests(activeQueryRunId),
    queryFn: () => getIntegrationRequests(activeQueryRunId),
    enabled: hasActiveRun,
    refetchInterval: hasActiveRun ? 2000 : false,
  });

  const queriedRun = activeRunQuery.data ?? null;
  const activeRun = queriedRun && queriedRun.ticket_id === selectedTicketId ? queriedRun : null;
  const runEvents = useMemo(
    () => (activeRun ? [...(runEventsQuery.data ?? [])].sort((a, b) => a.id - b.id) : []),
    [activeRun, runEventsQuery.data],
  );
  const outboxEvents = activeRun ? (outboxEventsQuery.data ?? []) : [];
  const inspectedSources = activeRun ? (evidenceQuery.data ?? []) : [];
  const backupRecords = activeRun ? (backupsQuery.data ?? []) : [];
  const validationResults = activeRun ? (validationResultsQuery.data ?? []) : [];
  const validationExpectations = activeRun ? (validationExpectationsQuery.data ?? []) : [];
  const integrationRequests = activeRun ? (integrationRequestsQuery.data ?? []) : [];
  const activityDraft = activeRun?.activity_draft ? (activeRun.activity_draft as ActivityDraft) : null;
  const recentBlockedStep = activeRunId ? (recentBlockedStepsByRun[activeRunId] ?? null) : null;

  useEffect(() => {
    if (activeRun && pendingStepFromRun(activeRun)) {
      setRecentBlockedStepsByRun((current) => {
        if (!(activeRun.id in current)) {
          return current;
        }
        const next = { ...current };
        delete next[activeRun.id];
        return next;
      });
    }
  }, [activeRun]);

  useEffect(() => {
    if (!activeRunId) {
      return;
    }
    const runId = activeRunId;
    const eventsKey = queryKeys.runs.events(runId);
    const existingEvents = queryClient.getQueryData<RunEvent[]>(eventsKey) ?? [];
    const afterId = existingEvents[existingEvents.length - 1]?.id ?? 0;
    let eventSource: EventSource | null = null;
    if ("EventSource" in window) {
      eventSource = new EventSource(`${API_BASE}/api/runs/${runId}/stream?after_id=${afterId}`);
      const handleEvent = (message: MessageEvent<string>) => {
        try {
          const event = JSON.parse(message.data) as RunEvent;
          queryClient.setQueryData<RunEvent[]>(eventsKey, (current = []) => mergeRunEvents(current, [event]));
        } catch (error) {
          console.debug("Could not parse run stream event", error);
        }
      };
      RUN_EVENT_TYPES.forEach((eventType) => eventSource?.addEventListener(eventType, handleEvent));
      eventSource.onerror = () => {
        eventSource?.close();
        eventSource = null;
      };
    }
    return () => {
      eventSource?.close();
    };
  }, [activeRunId, queryClient]);

  async function invalidateRunQueries(runId: number) {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.detail(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.events(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.outboxEvents(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.evidence(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.backups(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.validationResults(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.validationExpectations(runId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs.integrationRequests(runId) }),
    ]);
  }

  useEffect(() => {
    const completed = latestCompletedIntegrationRequest(integrationRequests);
    if (!completed || completed.id === lastCompletedIntegrationRequestIdRef.current) {
      return;
    }
    lastCompletedIntegrationRequestIdRef.current = completed.id;
    void queryClient.invalidateQueries({ queryKey: queryKeys.tickets.all });
    void queryClient.invalidateQueries({ queryKey: queryKeys.tickets.detail(completed.ticket_id) });
    void queryClient.invalidateQueries({ queryKey: queryKeys.tickets.customerSystem(completed.ticket_id) });
  }, [integrationRequests, queryClient]);

  const technicianName = me ? `${me.firstname} ${me.lastname}` : "technician";

  const startRunMutation = useMutation({
    mutationFn: (ticket: Ticket) => startRun(ticket.id),
    onSuccess: async (run) => {
      setActiveRunIdsByTicket((current) => ({ ...current, [run.ticket_id]: run.id }));
      setRecentBlockedStepsByRun((current) => {
        if (!(run.id in current)) {
          return current;
        }
        const next = { ...current };
        delete next[run.id];
        return next;
      });
      lastCompletedIntegrationRequestIdRef.current = null;
      queryClient.setQueryData(queryKeys.runs.detail(run.id), run);
      queryClient.setQueryData(queryKeys.runs.events(run.id), []);
      queryClient.setQueryData(queryKeys.runs.outboxEvents(run.id), []);
      queryClient.setQueryData(queryKeys.runs.evidence(run.id), []);
      queryClient.setQueryData(queryKeys.runs.backups(run.id), []);
      queryClient.setQueryData(queryKeys.runs.validationResults(run.id), []);
      queryClient.setQueryData(queryKeys.runs.validationExpectations(run.id), []);
      queryClient.setQueryData(queryKeys.runs.integrationRequests(run.id), []);
      await invalidateRunQueries(run.id);
    },
  });

  const approveConnectionMutation = useMutation({
    mutationFn: () => {
      if (!activeRun) throw new Error("No active run.");
      return approveConnection(activeRun.id, technicianName);
    },
    onSuccess: async (run) => {
      queryClient.setQueryData(queryKeys.runs.detail(run.id), run);
      await invalidateRunQueries(run.id);
    },
  });

  const submitManualStepMutation = useMutation({
    mutationFn: (payload: { command: string; purpose: string; phase: string; timeoutS: number }) => {
      if (!activeRun) throw new Error("No active run.");
      return submitManualStep(activeRun.id, {
        command: payload.command,
        purpose: payload.purpose,
        phase: payload.phase,
        timeout_s: payload.timeoutS,
        entered_by: technicianName,
      });
    },
    onSuccess: async (step) => {
      setRecentBlockedStepsByRun((current) => {
        const next = { ...current };
        if (step.safety_verdict === "blocked" || step.status === "blocked") {
          next[step.run_id] = step;
        } else {
          delete next[step.run_id];
        }
        return next;
      });
      await invalidateRunQueries(step.run_id);
    },
  });

  const approveStepMutation = useMutation({
    mutationFn: () => {
      const pendingStep = pendingStepFromRun(activeRun);
      if (!activeRun || !pendingStep) throw new Error("No pending step.");
      return approveStep(activeRun.id, pendingStep.id, technicianName);
    },
    onSuccess: async (run) => {
      queryClient.setQueryData(queryKeys.runs.detail(run.id), run);
      await invalidateRunQueries(run.id);
    },
  });

  const editAndApproveStepMutation = useMutation({
    mutationFn: (command: string) => {
      const pendingStep = pendingStepFromRun(activeRun);
      if (!activeRun || !pendingStep) throw new Error("No pending step.");
      return editAndApproveStep(activeRun.id, pendingStep.id, {
        command,
        approved_by: technicianName,
        purpose: pendingStep.purpose,
        expected_signal: pendingStep.expected_signal,
      });
    },
    onSuccess: async (run) => {
      queryClient.setQueryData(queryKeys.runs.detail(run.id), run);
      await invalidateRunQueries(run.id);
    },
  });

  const rejectStepMutation = useMutation({
    mutationFn: (reason: string) => {
      const pendingStep = pendingStepFromRun(activeRun);
      if (!activeRun || !pendingStep) throw new Error("No pending step.");
      return rejectStep(activeRun.id, pendingStep.id, technicianName, reason);
    },
    onSuccess: async (run) => {
      queryClient.setQueryData(queryKeys.runs.detail(run.id), run);
      await invalidateRunQueries(run.id);
    },
  });

  const recordBackupNotApplicableMutation = useMutation({
    mutationFn: async ({ sourcePath, reason }: { sourcePath: string | null; reason: string }) => {
      const pendingStep = pendingStepFromRun(activeRun);
      if (!activeRun || !pendingStep) throw new Error("No pending step.");
      await recordBackupNotApplicable(activeRun.id, sourcePath, reason, technicianName);
      return approveStep(activeRun.id, pendingStep.id, technicianName);
    },
    onSuccess: async (run) => {
      queryClient.setQueryData(queryKeys.runs.detail(run.id), run);
      await invalidateRunQueries(run.id);
    },
  });

  const retryRunMutation = useMutation({
    mutationFn: () => {
      if (!activeRun) throw new Error("No active run.");
      return retryRun(activeRun.id, technicianName);
    },
    onSuccess: async (run) => {
      setRecentBlockedStepsByRun((current) => {
        if (!(run.id in current)) {
          return current;
        }
        const next = { ...current };
        delete next[run.id];
        return next;
      });
      queryClient.setQueryData(queryKeys.runs.detail(run.id), run);
      await invalidateRunQueries(run.id);
    },
  });

  const abortRunMutation = useMutation({
    mutationFn: () => {
      if (!activeRun) throw new Error("No active run.");
      return abortRun(activeRun.id, technicianName);
    },
    onSuccess: async (run) => {
      setRecentBlockedStepsByRun((current) => {
        if (!(run.id in current)) {
          return current;
        }
        const next = { ...current };
        delete next[run.id];
        return next;
      });
      queryClient.setQueryData(queryKeys.runs.detail(run.id), run);
      await invalidateRunQueries(run.id);
    },
  });

  const draftActivityMutation = useMutation({
    mutationFn: async () => {
      if (!activeRun) throw new Error("No active run.");
      const draft = await draftActivity(activeRun.id);
      return { draft, runId: activeRun.id };
    },
    onSuccess: async ({ draft, runId }) => {
      queryClient.setQueryData<Run>(queryKeys.runs.detail(runId), (run) =>
        run ? { ...run, activity_draft: draft as unknown as Record<string, unknown> } : run,
      );
      await invalidateRunQueries(runId);
    },
  });

  const submitActivityMutation = useMutation({
    mutationFn: async (draft: ActivityDraft) => {
      if (!activeRun) throw new Error("No active run.");
      const saved = await saveActivityDraft(activeRun.id, draft, technicianName);
      const request = await submitActivity(activeRun.id);
      return { request, runId: activeRun.id, saved };
    },
    onSuccess: async ({ request, runId, saved }) => {
      queryClient.setQueryData<Run>(queryKeys.runs.detail(runId), (run) =>
        run ? { ...run, activity_draft: saved as unknown as Record<string, unknown> } : run,
      );
      queryClient.setQueryData<IntegrationRequest[]>(queryKeys.runs.integrationRequests(runId), (current = []) =>
        upsertIntegrationRequest(current, request),
      );
      await invalidateRunQueries(runId);
    },
  });

  const runActionMutations = [
    startRunMutation,
    approveConnectionMutation,
    submitManualStepMutation,
    approveStepMutation,
    editAndApproveStepMutation,
    rejectStepMutation,
    recordBackupNotApplicableMutation,
    retryRunMutation,
    abortRunMutation,
    draftActivityMutation,
    submitActivityMutation,
  ];
  const runBusy = runActionMutations.some((mutation) => mutation.isPending);
  const runMutationError = runActionMutations.find((mutation) => mutation.isError)?.error;
  const runQueryError =
    activeRunQuery.error ??
    runEventsQuery.error ??
    outboxEventsQuery.error ??
    evidenceQuery.error ??
    backupsQuery.error ??
    validationResultsQuery.error ??
    validationExpectationsQuery.error ??
    integrationRequestsQuery.error;
  const runError = runMutationError ? userFacingError(runMutationError) : runQueryError ? userFacingError(runQueryError) : null;

  function resetRunMutationErrors() {
    runActionMutations.forEach((mutation) => mutation.reset());
  }

  function handleStartRun(ticket: Ticket) {
    resetRunMutationErrors();
    startRunMutation.mutate(ticket);
  }

  function handleApproveConnection() {
    resetRunMutationErrors();
    approveConnectionMutation.mutate();
  }

  function handleSubmitManualStep(command: string, purpose: string, phase: string, timeoutS: number) {
    resetRunMutationErrors();
    submitManualStepMutation.mutate({ command, phase, purpose, timeoutS });
  }

  function handleApproveStep() {
    resetRunMutationErrors();
    approveStepMutation.mutate();
  }

  function handleEditAndApproveStep(command: string) {
    resetRunMutationErrors();
    editAndApproveStepMutation.mutate(command);
  }

  function handleRejectStep(reason: string) {
    resetRunMutationErrors();
    rejectStepMutation.mutate(reason);
  }

  function handleRecordBackupNotApplicable(sourcePath: string | null, reason: string) {
    resetRunMutationErrors();
    recordBackupNotApplicableMutation.mutate({ reason, sourcePath });
  }

  function handleRetryRun() {
    resetRunMutationErrors();
    retryRunMutation.mutate();
  }

  function handleAbortRun() {
    resetRunMutationErrors();
    abortRunMutation.mutate();
  }

  function handleDraftActivity() {
    resetRunMutationErrors();
    draftActivityMutation.mutate();
  }

  function handleSubmitActivity(draft: ActivityDraft) {
    resetRunMutationErrors();
    submitActivityMutation.mutate(draft);
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">techbold control plane</p>
          <h1>AI Service Desk Autopilot</h1>
        </div>
        <div className="operator-strip" aria-label="Technician identity">
          <span>{me ? `${me.firstname} ${me.lastname}` : "No technician"}</span>
          <strong>{me?.teamname ?? "Phoenix offline"}</strong>
        </div>
      </header>

      <div className="workspace-grid">
        <aside className="ticket-rail" aria-label="Ticket overview">
          <div className="rail-header">
            <div>
              <p className="eyebrow">Assigned tickets</p>
              <h2>{listLoading ? "Loading" : `${tickets.length} visible`}</h2>
            </div>
            <IconButton
              label="Refresh tickets"
              onClick={() => {
                void queryClient.invalidateQueries({ queryKey: queryKeys.me });
                void queryClient.invalidateQueries({ queryKey: queryKeys.tickets.all });
              }}
            >
              <RefreshCw aria-hidden="true" />
            </IconButton>
          </div>

          <div className="filters">
            <Label className="grid gap-1.5 text-[0.74rem] font-extrabold uppercase text-muted-foreground">
              Status
              <Select
                value={statusFilter || "all"}
                onValueChange={(value) => setStatusFilter(value === "all" ? "" : (value as TicketStatus))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All</SelectItem>
                  <SelectItem value="OPEN">Open</SelectItem>
                  <SelectItem value="PENDING">Pending</SelectItem>
                  <SelectItem value="DONE">Done</SelectItem>
                </SelectContent>
              </Select>
            </Label>
            <Label className="grid gap-1.5 text-[0.74rem] font-extrabold uppercase text-muted-foreground">
              Priority
              <Select value={priorityFilter || "all"} onValueChange={(value) => setPriorityFilter(value === "all" ? "" : value)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All</SelectItem>
                  <SelectItem value="critical">Critical</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                  <SelectItem value="medium">Medium</SelectItem>
                  <SelectItem value="low">Low</SelectItem>
                </SelectContent>
              </Select>
            </Label>
            <Label className="grid gap-1.5 text-[0.74rem] font-extrabold uppercase text-muted-foreground">
              Sort
              <Select value={sort} onValueChange={setSort}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="date">Date</SelectItem>
                  <SelectItem value="priority">Priority</SelectItem>
                  <SelectItem value="status">Status</SelectItem>
                </SelectContent>
              </Select>
            </Label>
          </div>

          {listError && <StateBlock tone="error">{listError}</StateBlock>}
          {listLoading && (
            <div className="skeleton-stack" aria-label="Loading tickets">
              <Skeleton className="h-[76px]" />
              <Skeleton className="h-[76px]" />
              <Skeleton className="h-[76px]" />
            </div>
          )}
          {!listLoading && !listError && tickets.length === 0 && (
            <StateBlock>No assigned tickets match the filters.</StateBlock>
          )}

          <div className="ticket-list">
            {tickets.map((ticket) => (
              <Button
                className={cn("ticket-row h-auto justify-start", ticket.id === selectedTicketId && "selected")}
                key={ticket.id}
                type="button"
                variant="ghost"
                onClick={() => setSelectedTicketId(ticket.id)}
              >
                <span className="ticket-row-top">
                  <strong>{ticket.title}</strong>
                  <PriorityBadge priority={ticket.priority} />
                </span>
                <span className="ticket-meta">
                  {ticket.customer_name} · {ticket.status}
                </span>
                <span className="ticket-date">Created {formatDate(ticket.created_at)}</span>
              </Button>
            ))}
          </div>
        </aside>

        <main className="detail-pane" aria-label="Ticket detail">
          {detailState.status === "idle" && <EmptyDetail />}
          {detailState.status === "loading" && <DetailSkeleton ticket={selectedTicket} />}
          {detailState.status === "error" && <StateBlock tone="error">{detailState.message}</StateBlock>}
          {detailState.status === "ready" && (
            <TicketDetail
              ticket={detailState.ticket}
              customerSystem={detailState.customerSystem}
              activeRun={activeRun}
              runBusy={runBusy}
              onStart={() => handleStartRun(detailState.ticket)}
            />
          )}
        </main>

          <RunConsole
          activeRun={activeRun}
          runBusy={runBusy}
          runError={runError}
          runEvents={runEvents}
          outboxEvents={outboxEvents}
          inspectedSources={inspectedSources}
          backupRecords={backupRecords}
          validationResults={validationResults}
          validationExpectations={validationExpectations}
          activityDraft={activityDraft}
          integrationRequests={integrationRequests}
          recentBlockedStep={recentBlockedStep}
          ticket={detailState.status === "ready" ? detailState.ticket : selectedTicket}
          onApproveConnection={handleApproveConnection}
          onSubmitManualStep={handleSubmitManualStep}
          onApproveStep={handleApproveStep}
          onEditAndApproveStep={handleEditAndApproveStep}
          onRejectStep={handleRejectStep}
          onRecordBackupNotApplicable={handleRecordBackupNotApplicable}
          onRetryRun={handleRetryRun}
          onAbortRun={handleAbortRun}
          onDraftActivity={handleDraftActivity}
          onSubmitActivity={handleSubmitActivity}
        />
      </div>
      </div>
    </TooltipProvider>
  );
}

function EmptyDetail() {
  return (
    <section className="empty-detail">
      <p className="eyebrow">Ticket detail</p>
      <h2>No ticket selected</h2>
    </section>
  );
}

function DetailSkeleton({ ticket }: { ticket: Ticket | null }) {
  return (
    <section className="detail-card">
      <p className="eyebrow">Loading ticket</p>
      <h2>{ticket?.title ?? "Fetching detail"}</h2>
      <div className="skeleton-stack wide">
        <Skeleton className="h-12" />
        <Skeleton className="h-12" />
        <Skeleton className="h-12" />
      </div>
    </section>
  );
}

function TicketDetail({
  ticket,
  customerSystem,
  activeRun,
  runBusy,
  onStart,
}: {
  ticket: Ticket;
  customerSystem: CustomerSystem;
  activeRun: Run | null;
  runBusy: boolean;
  onStart: () => void;
}) {
  const hasActiveRun = activeRun?.ticket_id === ticket.id;
  const tags = ticket.tags ?? [];

  return (
    <section className="detail-card">
      <div className="detail-head">
        <div>
          <p className="eyebrow">Ticket #{ticket.id}</p>
          <h2>{ticket.title}</h2>
        </div>
        <div className="status-stack">
          <PriorityBadge priority={ticket.priority} />
          <StatusBadge status={ticket.status} />
        </div>
      </div>

      <p className="customer-line">{ticket.customer_name}</p>
      <p className="report">{ticket.description}</p>

      <div className="tag-line">
        {tags.length > 0 ? tags.map((tag) => <span key={tag}>{tag}</span>) : <span>untagged</span>}
      </div>

      <div className="info-grid">
        <Info label="Created" value={formatDate(ticket.created_at)} />
        <Info label="SLA" value={formatDate(ticket.sla_due_at)} />
        <Info label="Host" value={customerSystem.system.ip} />
        <Info label="Port" value={String(customerSystem.system.port)} />
        <Info label="Username" value={customerSystem.system.username} />
        <Info label="OS" value={customerSystem.system.os} />
      </div>

      {customerSystem.system.notes && (
        <section className="notes-band">
          <p className="eyebrow">System notes</p>
          <p>{customerSystem.system.notes}</p>
        </section>
      )}

      <div className="detail-actions">
        <Button type="button" disabled={runBusy || hasActiveRun} onClick={onStart}>
          <Play aria-hidden="true" />
          {runBusy ? "Creating run" : hasActiveRun ? "Run created" : "Start troubleshooting"}
        </Button>
      </div>
    </section>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div className="info-cell">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function RunConsole({
  activeRun,
  runBusy,
  runError,
  runEvents,
  outboxEvents,
  inspectedSources,
  backupRecords,
  validationResults,
  validationExpectations,
  activityDraft,
  integrationRequests,
  recentBlockedStep,
  ticket,
  onApproveConnection,
  onSubmitManualStep,
  onApproveStep,
  onEditAndApproveStep,
  onRejectStep,
  onRecordBackupNotApplicable,
  onRetryRun,
  onAbortRun,
  onDraftActivity,
  onSubmitActivity,
}: {
  activeRun: Run | null;
  runBusy: boolean;
  runError: string | null;
  runEvents: RunEvent[];
  outboxEvents: OutboxEvent[];
  inspectedSources: InspectedSource[];
  backupRecords: BackupRecord[];
  validationResults: ValidationResult[];
  validationExpectations: ValidationExpectation[];
  activityDraft: ActivityDraft | null;
  integrationRequests: IntegrationRequest[];
  recentBlockedStep: ProposedStep | null;
  ticket: Ticket | null;
  onApproveConnection: () => void;
  onSubmitManualStep: (command: string, purpose: string, phase: string, timeoutS: number) => void;
  onApproveStep: () => void;
  onEditAndApproveStep: (command: string) => void;
  onRejectStep: (reason: string) => void;
  onRecordBackupNotApplicable: (sourcePath: string | null, reason: string) => void;
  onRetryRun: () => void;
  onAbortRun: () => void;
  onDraftActivity: () => void;
  onSubmitActivity: (draft: ActivityDraft) => void;
}) {
  const activeTicket = ticket?.title ?? "No active ticket";
  const target = runTarget(activeRun);
  const awaitingConnection = activeRun?.status === "awaiting_connection_approval";
  const pendingStep = pendingStepFromRun(activeRun);
  const displayedStep = pendingStep ?? recentBlockedStep;
  const displayedStepBlocked =
    Boolean(displayedStep) && (displayedStep?.safety_verdict === "blocked" || displayedStep?.status === "blocked");
  const displayedStepSafetyNotes = displayedStep?.safety_notes ?? [];
  const canUseManualMode =
    Boolean(activeRun) &&
    !awaitingConnection &&
    activeRun?.status !== "executing" &&
    activeRun?.status !== "fixing" &&
    activeRun?.status !== "validating" &&
    activeRun?.status !== "aborted" &&
    activeRun?.status !== "submitted" &&
    !pendingStep;
  const activityReady = activeRun?.status === "ready_for_activity";
  const visibleOutboxEvents = outboxEvents.filter(
    (event) => event.status === "failed" || event.status === "dead_letter",
  );
  const latestIntegrationRequest = integrationRequests[integrationRequests.length - 1] ?? null;
  const integrationInFlight = latestIntegrationRequest
    ? !["completed", "dead_letter"].includes(latestIntegrationRequest.status)
    : false;
  const anchoredExecutionIds = new Set<number>();
  const transcriptRows = runEvents.map((event) => {
    const commandExecutionId = commandExecutionIdFromEvent(event);
    const anchorId =
      commandExecutionId !== null && !anchoredExecutionIds.has(commandExecutionId)
        ? transcriptAnchorId(commandExecutionId)
        : undefined;
    if (commandExecutionId !== null) {
      anchoredExecutionIds.add(commandExecutionId);
    }
    return { event, anchorId };
  });
  const [editCommand, setEditCommand] = useState("");
  const [rejectReason, setRejectReason] = useState("Rejected by technician.");
  const manualStepForm = useForm<ManualStepForm>({
    resolver: zodResolver(manualStepSchema),
    defaultValues: manualStepDefaults,
    mode: "onChange",
  });
  const backupNotApplicableForm = useForm<BackupNotApplicableForm>({
    resolver: zodResolver(backupNotApplicableSchema),
    defaultValues: { reason: defaultBackupNotApplicableReason },
    mode: "onChange",
  });
  const activityDraftForm = useForm<ActivityDraftForm>({
    resolver: zodResolver(activityDraftFormSchema),
    defaultValues: activityDraftFormDefaults(activityDraft),
    mode: "onChange",
  });
  const manualStepErrors = manualStepForm.formState.errors;
  const backupNotApplicableErrors = backupNotApplicableForm.formState.errors;
  const activityDraftErrors = activityDraftForm.formState.errors;

  useEffect(() => {
    setEditCommand(pendingStep?.command ?? "");
  }, [pendingStep?.id, pendingStep?.command]);

  useEffect(() => {
    activityDraftForm.reset(activityDraftFormDefaults(activityDraft));
  }, [activityDraft, activityDraftForm]);

  const pendingBackupEvent = pendingStep
    ? [...runEvents]
        .reverse()
        .find(
          (event) =>
            (event.event_type === "backup.approval_requested" || event.event_type === "backup.planned") &&
            typeof event.payload?.step_id === "number" &&
            event.payload.step_id === pendingStep.id,
        )
    : null;
  const pendingBackupSource =
    pendingBackupEvent && typeof pendingBackupEvent.payload?.source_path === "string"
      ? pendingBackupEvent.payload.source_path
      : null;
  const pendingBackupRecords = pendingBackupSource
    ? backupRecords.filter((record) => record.source_path === pendingBackupSource)
    : [];
  const pendingFixNeedsBackup = pendingStep?.phase === "fix";
  const pendingBackupCreated = pendingBackupRecords.some((record) => record.backup_created);
  const pendingBackupNotApplicable = pendingBackupRecords.some((record) => record.backup_type === "not_applicable");
  const pendingBackupState = displayedStepBlocked && displayedStep?.phase === "fix"
    ? "blocked"
    : !pendingFixNeedsBackup
      ? null
      : pendingBackupCreated
        ? "created"
        : pendingBackupNotApplicable
          ? "not applicable"
          : pendingBackupEvent
            ? "missing"
            : "required";

  function submitManual(values: ManualStepForm) {
    onSubmitManualStep(values.command.trim(), values.purpose.trim(), values.phase, values.timeoutS);
    manualStepForm.reset({ ...manualStepDefaults, purpose: values.purpose.trim() });
  }

  function submitBackupNotApplicable(values: BackupNotApplicableForm) {
    onRecordBackupNotApplicable(pendingBackupSource, values.reason.trim());
  }

  function submitActivityDraft(values: ActivityDraftForm) {
    if (activityDraft) {
      onSubmitActivity({ ...activityDraft, ...values });
    }
  }

  return (
    <aside className="run-console" aria-label="Troubleshooting run console">
      <div className="console-head">
        <div>
          <p className="eyebrow">Run console</p>
          <h2>{readableRunStatus(activeRun?.status)}</h2>
        </div>
        <span className={`signal ${awaitingConnection ? "pending" : activeRun ? "active" : ""}`} />
      </div>

      <div className="run-status">
        <span className="inline-flex items-center gap-2">
          <TicketIcon className="size-4" aria-hidden="true" />
          Ticket
        </span>
        <strong>{activeTicket}</strong>
        {activeRun && <em>Run #{activeRun.id} · started {formatDate(activeRun.started_at)}</em>}
      </div>

      {visibleOutboxEvents.length > 0 && (
        <section className="outbox-panel" aria-label="Worker queue failures">
          <div className="step-head">
            <div>
              <p className="eyebrow">Worker queue</p>
              <h3 className="inline-flex items-center gap-2">
                <ListChecks className="size-4" aria-hidden="true" />
                {visibleOutboxEvents.length} item{visibleOutboxEvents.length === 1 ? "" : "s"} need attention
              </h3>
            </div>
            <Badge className="queue-count">{visibleOutboxEvents.length}</Badge>
          </div>
          <div className="queue-list">
            {visibleOutboxEvents.map((event) => (
              <article className={`queue-row ${event.status}`} key={event.id}>
                <div>
                  <span className="ledger-kicker">
                    {event.status.replace("_", " ")} · attempt {event.attempts}
                  </span>
                  <strong>{event.event_type}</strong>
                </div>
                {event.error && <p>{event.error}</p>}
                <span className="ledger-meta">
                  queued {formatDate(event.created_at)}
                  {event.available_at ? ` · retry ${formatDate(event.available_at)}` : ""}
                </span>
              </article>
            ))}
          </div>
        </section>
      )}

      <div className="approval-panel">
        <p className="eyebrow">SSH gate</p>
        <h3>{awaitingConnection ? "Approve connection" : activeRun ? "Connection recorded" : "No run active"}</h3>
        <p>
          {awaitingConnection
            ? `${target.username ?? "user"}@${target.ip ?? "host"}:${target.port ?? 22} · ${target.os ?? "unknown OS"}`
            : activeRun
              ? "The approval audit stream is persisted and ready for the next worker step."
              : "No ticket run has been created."}
        </p>
        {runError && (
          <Alert variant="destructive" className="border-red-300/35 bg-red-400/10 text-red-100">
            <AlertTriangle className="size-4" aria-hidden="true" />
            <AlertDescription>{runError}</AlertDescription>
          </Alert>
        )}
        <div className="action-row">
          <Button
            className={consolePrimaryButtonClass}
            type="button"
            disabled={!awaitingConnection || runBusy}
            onClick={onApproveConnection}
          >
            <CheckCircle2 aria-hidden="true" />
            {runBusy && awaitingConnection ? "Approving" : "Approve"}
          </Button>
          <Button
            className={consoleButtonClass}
            type="button"
            variant="outline"
            disabled={!activeRun || activeRun.status === "aborted" || runBusy}
            onClick={onAbortRun}
          >
            <Ban aria-hidden="true" />
            Abort
          </Button>
        </div>
      </div>

      {displayedStep && (
        <section
          className={`approval-panel step-panel ${displayedStepBlocked ? "blocked-step" : ""}`}
          aria-label={displayedStepBlocked ? "Blocked command" : "Pending command approval"}
        >
          <div className="step-head">
            <div>
              <p className="eyebrow">{displayedStep.source === "manual" ? "Manual command" : "Agent command"}</p>
              <h3>{displayedStepBlocked ? "Blocked command" : displayedStep.phase}</h3>
            </div>
            <Badge className={cn("max-w-[132px] break-words rounded-md border px-2 py-1 text-[0.68rem]", riskBadgeClass(displayedStep.risk_class))}>
              {displayedStep.risk_class}
            </Badge>
          </div>
          <pre className="command-preview">{displayedStep.command}</pre>
          <p>{displayedStep.purpose}</p>
          {displayedStep.expected_signal && <p className="signal-copy">{displayedStep.expected_signal}</p>}
          <div className="safety-line">
            <strong>{displayedStep.safety_verdict}</strong>
            <span>{displayedStep.safety_summary}</span>
          </div>
          {displayedStepSafetyNotes.length > 0 && (
            <ul className="safety-notes">
              {displayedStepSafetyNotes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          {displayedStepBlocked && <p className="blocked-copy">No SSH execution was queued.</p>}
          {pendingBackupState && (
            <div className={`backup-gate ${pendingBackupState.replace(" ", "-")}`}>
              <span>Backup required: yes</span>
              <span>Backup state: {pendingBackupState}</span>
              <strong>{pendingBackupSource ?? "rollback decision required"}</strong>
              {!displayedStepBlocked && (pendingBackupState === "missing" || pendingBackupState === "required") && (
                <form className="backup-decision" onSubmit={backupNotApplicableForm.handleSubmit(submitBackupNotApplicable)}>
                  <Label className="console-field">
                    Not applicable reason
                    <Input
                      {...backupNotApplicableForm.register("reason")}
                      className={consoleFieldControlClass}
                      aria-invalid={Boolean(backupNotApplicableErrors.reason)}
                    />
                    <FieldError message={backupNotApplicableErrors.reason?.message} />
                  </Label>
                  <Button
                    className={consolePrimaryButtonClass}
                    type="submit"
                    disabled={runBusy || !backupNotApplicableForm.formState.isValid}
                  >
                    <Database aria-hidden="true" />
                    Mark not applicable
                  </Button>
                </form>
              )}
            </div>
          )}
          {!displayedStepBlocked && (
            <>
              <Label className="console-field">
                Approved command
                <Textarea
                  className={consoleFieldControlClass}
                  value={editCommand}
                  onChange={(event) => setEditCommand(event.target.value)}
                  rows={3}
                  spellCheck={false}
                />
              </Label>
              <Label className="console-field">
                Reject reason
                <Input className={consoleFieldControlClass} value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} />
              </Label>
              <div className="action-row wrap">
                <Button className={consolePrimaryButtonClass} type="button" disabled={runBusy} onClick={onApproveStep}>
                  <CheckCircle2 aria-hidden="true" />
                  Approve
                </Button>
                <Button
                  className={consoleButtonClass}
                  type="button"
                  variant="outline"
                  disabled={runBusy || editCommand.trim().length === 0}
                  onClick={() => onEditAndApproveStep(editCommand.trim())}
                >
                  <ShieldCheck aria-hidden="true" />
                  Edit & approve
                </Button>
                <Button
                  className={consoleButtonClass}
                  type="button"
                  variant="outline"
                  disabled={runBusy}
                  onClick={() => onRejectStep(rejectReason.trim() || "Rejected by technician.")}
                >
                  <XCircle aria-hidden="true" />
                  Reject
                </Button>
              </div>
            </>
          )}
        </section>
      )}

      <form className="manual-panel" onSubmit={manualStepForm.handleSubmit(submitManual)}>
        <div className="step-head">
          <div>
            <p className="eyebrow">Manual entry</p>
            <h3>{canUseManualMode ? "Command proposal" : "Standby"}</h3>
          </div>
          <Button
            className={consolePrimaryButtonClass}
            type="submit"
            disabled={!canUseManualMode || runBusy || !manualStepForm.formState.isValid}
          >
            <ListChecks aria-hidden="true" />
            Queue
          </Button>
        </div>
        <Label className="console-field">
          Command
          <Textarea
            {...manualStepForm.register("command")}
            className={consoleFieldControlClass}
            aria-invalid={Boolean(manualStepErrors.command)}
            rows={3}
            spellCheck={false}
            placeholder="systemctl --failed"
            disabled={!canUseManualMode || runBusy}
          />
          <FieldError message={manualStepErrors.command?.message} />
        </Label>
        <Label className="console-field">
          Purpose
          <Input
            {...manualStepForm.register("purpose")}
            className={consoleFieldControlClass}
            aria-invalid={Boolean(manualStepErrors.purpose)}
            disabled={!canUseManualMode || runBusy}
          />
          <FieldError message={manualStepErrors.purpose?.message} />
        </Label>
        <Label className="console-field">
          Phase
          <Controller
            control={manualStepForm.control}
            name="phase"
            render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange} disabled={!canUseManualMode || runBusy}>
                <SelectTrigger className={consoleFieldControlClass} aria-invalid={Boolean(manualStepErrors.phase)}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="diagnostic">Diagnostic</SelectItem>
                  <SelectItem value="fix">Fix</SelectItem>
                  <SelectItem value="validation">Validation</SelectItem>
                </SelectContent>
              </Select>
            )}
          />
          <FieldError message={manualStepErrors.phase?.message} />
        </Label>
        <Label className="console-field">
          Timeout
          <Input
            {...manualStepForm.register("timeoutS", { valueAsNumber: true })}
            className={consoleFieldControlClass}
            type="number"
            min={1}
            max={600}
            step={1}
            inputMode="numeric"
            aria-invalid={Boolean(manualStepErrors.timeoutS)}
            disabled={!canUseManualMode || runBusy}
          />
          <FieldError message={manualStepErrors.timeoutS?.message} />
        </Label>
        <div className="action-row wrap">
          <Button
            className={consoleButtonClass}
            type="button"
            variant="outline"
            disabled={!activeRun || runBusy || activeRun.status === "aborted"}
            onClick={onRetryRun}
          >
            <RotateCcw aria-hidden="true" />
            Retry
          </Button>
          <Button
            className={consoleButtonClass}
            type="button"
            variant="outline"
            disabled={!activeRun || runBusy || activeRun.status === "aborted"}
            onClick={onAbortRun}
          >
            <Ban aria-hidden="true" />
            Abort
          </Button>
        </div>
      </form>

      <div className="terminal-shell">
        <div className="terminal-bar">
          <Terminal className="mr-1 size-4 text-console-muted" aria-hidden="true" />
          <span />
          <span />
          <span />
        </div>
        <div className="terminal-transcript">
          {transcriptRows.length === 0 ? (
            <pre>$ no transcript yet</pre>
          ) : (
            transcriptRows.map(({ event, anchorId }) => (
              <pre id={anchorId} key={event.id}>
                {formatRunEvent(event)}
              </pre>
            ))
          )}
        </div>
      </div>

      <section className="ledger-preview" aria-label="Logs & files checked">
        <h3 className="inline-flex items-center gap-2">
          <FileText className="size-4" aria-hidden="true" />
          Logs & files checked
        </h3>
        {inspectedSources.length === 0 ? (
          <p>No inspected sources recorded.</p>
        ) : (
          <div className="ledger-list">
            {inspectedSources.map((source) => (
              <article className="ledger-row" key={source.id}>
                <div>
                  <span className="ledger-kicker">
                    {source.source_type} · {source.supports}
                  </span>
                  <strong>{source.path ?? source.source_name ?? "source"}</strong>
                </div>
                <p>{source.finding}</p>
                <code>{source.command}</code>
                <a className="transcript-link" href={`#${transcriptAnchorId(source.command_execution_id)}`}>
                  Transcript #{source.command_execution_id}
                </a>
                <span className="ledger-meta">
                  {source.actor} · {formatDate(source.created_at)}
                  {source.redacted ? " · redacted" : ""}
                </span>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="ledger-preview">
        <h3 className="inline-flex items-center gap-2">
          <Database className="size-4" aria-hidden="true" />
          Backups & rollback
        </h3>
        {backupRecords.length === 0 ? (
          <p>No backup or rollback records.</p>
        ) : (
          <div className="ledger-list">
            {backupRecords.map((record) => (
              <article className="ledger-row" key={record.id}>
                <div>
                  <span className="ledger-kicker">
                    {record.backup_type} · created {record.backup_created ? "yes" : "no"}
                  </span>
                  <strong>{record.source_path ?? "no source path"}</strong>
                </div>
                <p>{record.reason}</p>
                {record.backup_path && <code>{record.backup_path}</code>}
                {record.restore_command && <code>{record.restore_command}</code>}
                <span className="ledger-meta">
                  persistent {record.persistent_across_reboot ? "yes" : "no"} · {formatDate(record.created_at)}
                  {record.redacted ? " · redacted" : ""}
                </span>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="ledger-preview">
        <h3 className="inline-flex items-center gap-2">
          <ClipboardCheck className="size-4" aria-hidden="true" />
          Required validation checks
        </h3>
        {validationExpectations.length === 0 ? (
          <p>No required validation suite has been created yet.</p>
        ) : (
          <div className="ledger-list validation-suite" aria-label="Required validation checks">
            {validationExpectations.map((expectation) => (
              <article className={`ledger-row validation-expectation ${expectation.status}`} key={expectation.id}>
                <div>
                  <span className="ledger-kicker">
                    {expectation.check_type} · {expectation.status}
                  </span>
                  <strong>{expectation.target ?? "required check"}</strong>
                </div>
                <p>{expectation.expected_result}</p>
                <span className="ledger-meta">{expectation.relation_to_customer_symptom}</span>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="ledger-preview">
        <h3 className="inline-flex items-center gap-2">
          <ClipboardCheck className="size-4" aria-hidden="true" />
          Validation results
        </h3>
        {validationResults.length === 0 ? (
          <p>No validation result recorded. Activity submission stays locked until validation passes.</p>
        ) : (
          <div className="ledger-list">
            {validationResults.map((result) => (
              <article className={`ledger-row validation-row ${result.passed ? "passed" : "failed"}`} key={result.id}>
                <div>
                  <span className="ledger-kicker">
                    {result.check_type} · {result.passed ? "passed" : "failed"}
                  </span>
                  <strong>{result.target ?? "validation check"}</strong>
                </div>
                <p>{result.summary}</p>
                <code>{result.evidence}</code>
                <span className="ledger-meta">Recorded {formatDate(result.created_at)}</span>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="ledger-preview activity-review">
        <div className="step-head">
          <div>
            <p className="eyebrow">Phoenix activity</p>
            <h3 className="inline-flex items-center gap-2">
              <Send className="size-4" aria-hidden="true" />
              {activityDraft ? "Review draft" : "No draft yet"}
            </h3>
          </div>
          <Button
            className={consolePrimaryButtonClass}
            type="button"
            disabled={!activityReady || runBusy}
            onClick={onDraftActivity}
          >
            <FileText aria-hidden="true" />
            Draft
          </Button>
        </div>
        {activityDraft ? (
          <form className="activity-form" onSubmit={activityDraftForm.handleSubmit(submitActivityDraft)}>
            {latestIntegrationRequest && (
              <div
                className={`integration-status ${latestIntegrationRequest.status}`}
                aria-label="Phoenix integration status"
              >
                <span className="ledger-kicker">
                  Request #{latestIntegrationRequest.id} · attempt {latestIntegrationRequest.attempts}
                </span>
                <strong>{integrationStatusHeading(latestIntegrationRequest)}</strong>
                <p>{integrationStatusCopy(latestIntegrationRequest)}</p>
                <span className="ledger-meta">
                  {latestIntegrationRequest.phoenix_activity_id
                    ? `Phoenix activity #${latestIntegrationRequest.phoenix_activity_id}`
                    : "Phoenix activity not created yet"}
                  {latestIntegrationRequest.ticket_status ? ` · ticket ${latestIntegrationRequest.ticket_status}` : ""}
                </span>
                {latestIntegrationRequest.error && <p className="console-error">{latestIntegrationRequest.error}</p>}
              </div>
            )}
            <Label className="console-field">
              Summary
              <Textarea
                {...activityDraftForm.register("summary")}
                className={consoleFieldControlClass}
                aria-invalid={Boolean(activityDraftErrors.summary)}
                rows={3}
              />
              <FieldError message={activityDraftErrors.summary?.message} />
            </Label>
            <Label className="console-field">
              Root cause
              <Textarea
                {...activityDraftForm.register("root_cause")}
                className={consoleFieldControlClass}
                aria-invalid={Boolean(activityDraftErrors.root_cause)}
                rows={3}
              />
              <FieldError message={activityDraftErrors.root_cause?.message} />
            </Label>
            <Label className="console-field">
              Actions taken
              <Textarea
                {...activityDraftForm.register("actions_taken")}
                className={consoleFieldControlClass}
                aria-invalid={Boolean(activityDraftErrors.actions_taken)}
                rows={3}
              />
              <FieldError message={activityDraftErrors.actions_taken?.message} />
            </Label>
            <Label className="console-field">
              Commands summary
              <Textarea
                {...activityDraftForm.register("commands_summary")}
                className={consoleFieldControlClass}
                aria-invalid={Boolean(activityDraftErrors.commands_summary)}
                rows={4}
              />
              <FieldError message={activityDraftErrors.commands_summary?.message} />
            </Label>
            <Label className="console-field">
              Validation result
              <Textarea
                {...activityDraftForm.register("validation_result")}
                className={consoleFieldControlClass}
                aria-invalid={Boolean(activityDraftErrors.validation_result)}
                rows={3}
              />
              <FieldError message={activityDraftErrors.validation_result?.message} />
            </Label>
            <Button
              className={consolePrimaryButtonClass}
              type="submit"
              disabled={runBusy || !activityReady || integrationInFlight || !activityDraftForm.formState.isValid}
            >
              <Send aria-hidden="true" />
              {activeRun?.status === "submitted"
                ? "Submitted"
                : integrationInFlight
                  ? "Queued"
                  : "Submit activity"}
            </Button>
          </form>
        ) : (
          <p>Generate a draft after validation evidence is recorded.</p>
        )}
      </section>
    </aside>
  );
}
