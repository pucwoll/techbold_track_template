# LLM Agent Spec

## Decision

Use a custom event-driven orchestrator with structured LLM calls. Do not use LangGraph, Pi harness, or autonomous LangChain tool agents for the product runtime.

Recommended stack:

- Primary control plane: our FastAPI API, worker, Postgres outbox, and audit event stream.
- LLM provider access: direct provider SDK or a thin model adapter.
- Structured output validation: Pydantic models.
- Agent orchestration: plain worker state machine backed by Postgres events.
- Avoid: autonomous LangChain tool agents with direct SSH tools.
- Avoid: LangGraph for this hackathon build.
- Avoid: Pi harness as the service runtime.

Why:

- The rubric rewards safety, auditability, and human control more than agent-framework sophistication.
- Our system must prove that every command was proposed, checked, approved, executed, logged, and summarized.
- A custom event loop gives us exact control over approval gates and logs.
- Skipping LangGraph removes one moving part while preserving the same explicit states in our worker.
- Pi is a terminal coding-agent harness, not the right fit for a FastAPI service desk product with durable Postgres state and a React approval UI.

## Framework Position

### LangChain

Useful for:

- Model provider abstraction.
- Tool schema conventions.
- Human-in-the-loop middleware concepts.
- Streaming agent updates.

Risk for this project:

- A generic tool agent can blur the line between reasoning and execution.
- Tool execution can become framework-owned unless carefully constrained.
- We need command-by-command traceability in our own schema, not just framework traces.

Use only if:

- We keep SSH execution outside LangChain tools.
- The LLM output is only a structured proposal.
- Every proposed command still goes through our safety layer, approval API, worker, and audit tables.

### LangGraph

Do not use for this build.

Reason:

- It adds another state runtime on top of the Postgres run state machine.
- We can represent the needed phases directly in worker code: `analyze_ticket`, `choose_diagnostic`, `interpret_observation`, `choose_fix`, `validate`, and `draft_activity`.
- Human approval is already modeled by our API, audit events, and outbox state.
- Worker restart safety is already handled by Postgres outbox rows and durable run events.
- The implementation time is better spent on troubleshooting quality, safety checks, live terminal streaming, and activity generation.

If we revisit this after the hackathon, LangGraph could be useful for making planner phases more declarative, but it is not part of the hackathon architecture.

### Pi Harness

Do not use for this app.

Reason:

- Pi is designed as a terminal coding-agent harness with TypeScript extensions and coding tools.
- Our product is a service desk web app with FastAPI, React, Postgres, Phoenix ERP, and SSH execution.
- Using Pi would add an extra runtime model around the wrong problem.

## Agent Boundaries

The LLM is not an operator. It is a planner and summarizer.

The LLM may:

- Interpret the ticket.
- Rank hypotheses.
- Propose one diagnostic command.
- Interpret command output.
- Propose one minimal fix command.
- Propose validation commands.
- Draft activity fields from the audit log.

The LLM may not:

- Execute SSH commands.
- Bypass safety classification.
- Approve its own action.
- Read private key material.
- Access Phoenix credentials.
- Submit activities directly.
- Mark tickets done directly.
- Run multiple commands as an unreviewed batch.

## Agent Shape

Use one orchestrated agent with specialized nodes rather than multiple independent agents.

Nodes:

- `ticket_analyzer`: extracts symptom, affected service hints, customer benefit, likely ports, and initial uncertainty.
- `system_context_planner`: decides the next read-only discovery command from known ticket and system facts.
- `observation_interpreter`: summarizes command result and updates hypotheses.
- `fix_planner`: proposes the smallest evidence-backed fix.
- `validation_planner`: proposes concrete service and customer-benefit checks.
- `activity_writer`: drafts Phoenix activity fields from the event and command logs.

This gives us multi-agent-like separation without multi-agent coordination overhead.

## Run Loop

The run loop is event-driven:

1. `run.created`
2. `connection.approval_requested`
3. Technician approves SSH connection.
4. Worker starts planner.
5. Planner emits `step.proposed`.
6. Safety layer emits `step.safety_classified`.
7. UI shows pending command.
8. Technician approves, edits, rejects, retries, or aborts.
9. Worker executes approved command.
10. Worker writes command log.
11. Planner receives sanitized observation.
12. Loop repeats until validation passes.
13. Activity writer drafts ERP activity.
14. Technician reviews and submits.

Only one proposed SSH command may be pending at a time.

## LLM Input Contract

Each planner call receives:

- Ticket snapshot.
- Customer system snapshot.
- Run status.
- Current hypotheses.
- Sanitized audit summary.
- Recent command results.
- Safety policy summary.
- Allowed action type for the current phase.
- Required output schema.

The LLM does not receive:

- Phoenix bearer token.
- SSH private key.
- Full raw environment.
- Unredacted command output.
- Browser/frontend secrets.

## LLM Output Contract

Planner output must be structured JSON validated by Pydantic before use.

For a proposed step:

- `phase`: `diagnostic`, `fix`, or `validation`.
- `command`: exact command to run.
- `purpose`: one or two sentences.
- `hypothesis`: what this command tests or fixes.
- `expected_signal`: what result would be meaningful.
- `risk_level`: `read_only`, `low`, `medium`, or `requires_review`.
- `requires_service_restart`: boolean.
- `persistence_consideration`: how this affects persistence.
- `rollback_plan`: required for medium-risk changes.
- `stop_if`: conditions where execution should pause.

For updated hypotheses:

- `root_cause_candidate`.
- `confidence`: `low`, `medium`, or `high`.
- `supporting_evidence`.
- `contradicting_evidence`.
- `inspected_sources`: log files, journal sources, config files, service statuses, metadata checks, or endpoint validations used as evidence.
- `next_best_action`.

For final activity:

- `summary`.
- `root_cause`.
- `actions_taken`.
- `commands_summary`.
- `validation_result`.
- `confidence_notes`.

Invalid output is logged as `agent.output_invalid` and retried with a stricter prompt. After repeated invalid outputs, the worker falls back to deterministic diagnostics or pauses for technician input.

## Tool Model

Do not expose a generic shell tool to the LLM.

The LLM can only propose a command object. The actual tools are backend-owned:

- `classify_command`: safety layer, automatic.
- `request_approval`: API/UI state, automatic.
- `execute_ssh_command`: worker only, after approval.
- `summarize_observation`: LLM or deterministic summarizer.
- `generate_activity`: LLM plus deterministic required-field checks.

This preserves the human-in-the-loop proof the jury wants.

## Diagnostic Policy

Start with read-only discovery unless the ticket/system context proves a narrow safe action.

Preferred first steps:

- Check failed units.
- Check suspected service status.
- Check recent logs for the suspected service.
- Check listening ports.
- Check local customer-facing endpoint.
- Check disk/memory only when symptoms suggest resource pressure.
- Validate service config before restarting or editing.

The planner should avoid broad inventory sweeps. Fewer relevant commands help tie-breakers.

## Fix Policy

The fix planner must justify:

- The observed root cause.
- The exact target file/service/resource.
- Why the proposed change is minimal.
- Whether a targeted backup is required before the change.
- How the change persists.
- How it will be validated.
- How to roll back if needed.

Medium-risk fix proposals should include a targeted pre-change backup command when useful and safe, such as copying one config file to a timestamped backup path or recording file ownership/mode before a permission change. They should not create full-machine backups, broad archives, database dumps, or copies of customer data.

The full backup policy is specified in [backup-policy-spec.md](backup-policy-spec.md).

## Validation Policy

Validation must prove customer benefit, not just command success.

The validation planner should prefer:

- Service syntax/config validation.
- Restart or reload of only the affected service.
- `systemctl is-active` or equivalent.
- Local endpoint check.
- Recent log check showing the original error stopped.
- Persistence check after service restart. Reboot checks require explicit technician approval.

## Prompting Strategy

Use short, strict prompts:

- System prompt defines role, safety constraints, and output schema.
- Developer prompt includes scoring priorities and hard blocks.
- User payload includes ticket/system/run facts.
- The prompt tells the model to propose exactly one next command or no command.
- The prompt tells the model to prefer read-only diagnostics until evidence supports a fix.
- The prompt tells the model to never include secrets in outputs.

Do not ask the model for hidden chain-of-thought. Ask for concise evidence and rationale fields.

## Fallback Without LLM

The app should still be usable when the LLM fails.

Fallback behavior:

- Technician can manually enter a command.
- Safety layer still classifies it.
- Approval and audit flow remains identical.
- Activity draft can be generated from template fields and command logs.
- The run can still earn ERP, safety, audit, and partial troubleshooting points.

Deterministic diagnostic playbook:

1. `systemctl --failed`
2. Target service status if inferred from ticket.
3. Recent target service logs.
4. Listening ports.
5. Local endpoint check.
6. Config validation for inferred service.

## Model Choice

Prefer a capable general model with reliable structured output. The exact provider is less important than:

- JSON/schema reliability.
- Low latency.
- Good Linux troubleshooting ability.
- Configurable timeout.
- No secret retention in prompts.

The model name should be environment-configured, not hard-coded.

## Agent Events

Add these agent-specific events to the architecture event vocabulary:

- `agent.context_built`
- `agent.prompt_submitted`
- `agent.output_received`
- `agent.output_invalid`
- `agent.hypotheses_updated`
- `agent.step_selected`
- `agent.fallback_used`
- `agent.activity_draft_generated`

Payloads should be sanitized and concise.

## What We Show in the Demo

The UI should make the agent boundary obvious:

- "AI proposes" panel with hypothesis and next command.
- "Safety check" result before approval.
- "Technician approval" with edit/reject/abort.
- Live browser terminal that shows the exact approved command and streamed sanitized stdout/stderr.
- "Logs & files checked" panel with every inspected evidence source and its finding.
- "Evidence" summary after each result.
- "Validation" section before activity submission.
- "Activity generated from run log" review screen.

This visually proves the key scoring claims: human control, audit trail, minimal commands, and complete activity documentation.
