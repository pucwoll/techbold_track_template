# Live Browser Terminal Spec

## Goal

Provide a terminal-like view in the browser where the technician can watch, live, exactly what the agent is running and what the remote system returns.

This is a visibility and control surface, not an unrestricted remote shell. It must strengthen the safety story:

- The technician sees the proposed command before execution.
- The technician approves, edits, rejects, retries, or aborts.
- The approved command appears in the terminal.
- stdout and stderr stream back live.
- exit code, duration, safety verdict, and validation status are logged.
- all terminal content is sanitized before display and persistence.

## Product Behavior

The ticket detail screen should include a live terminal panel in the troubleshooting area.

For each command, the terminal displays:

1. A prompt-style header with target host, username, and run context.
2. The proposed command in a pending state before approval.
3. The safety verdict and risk level.
4. An approval marker when the technician approves.
5. The exact approved command as the command line.
6. Live stdout and stderr output as it arrives.
7. Timeout or error markers when execution fails.
8. Final exit code and duration.
9. A short agent interpretation after completion.

Example terminal transcript:

```text
[ticket 7001] azureuser@10.0.0.5:22
AI proposed: systemctl status nginx --no-pager
Safety: READ_ONLY allowed
Approved by technician at 10:14:03

$ systemctl status nginx --no-pager
● nginx.service - A high performance web server
   Loaded: loaded (/lib/systemd/system/nginx.service; enabled)
   Active: failed (Result: exit-code)
...

[exit 3, 842 ms]
Agent: nginx is installed and enabled, but currently failed. Next step should inspect recent nginx logs.
```

## Terminal Modes

### Agent Mode

Default mode.

- The agent proposes one command.
- The terminal shows it as pending.
- The technician approves it.
- The worker executes it.
- Output streams live.
- The agent interprets the result.

### Manual Command Mode

Fallback/control mode.

- The technician types a command into a command input.
- The command does not execute immediately.
- It becomes a proposed manual step.
- Safety layer classifies it.
- The technician must approve it after classification.
- Execution and logging are identical to agent mode.

Manual mode is useful if the LLM stalls or the technician wants to take over. It must not bypass safety, audit, redaction, or approval.

### Read-Only Replay Mode

After a command completes, the technician can reopen previous terminal transcripts.

- Shows command, output, approval, exit code, and timing.
- Does not re-run anything.
- Used for demo, debugging, and activity review.

## What It Is Not

Do not implement a raw persistent interactive shell as the primary path.

Avoid:

- Browser-connected PTY sessions with arbitrary unlogged keystrokes.
- Long-running root shells.
- Commands that execute without a discrete approval event.
- Hidden helper commands that do not appear in the terminal and audit log.
- Client-side SSH.

If an interactive command is unavoidable, it should be rejected or converted into a non-interactive equivalent. The case incidents are expected to be local service issues, so non-interactive command execution is sufficient.

## Streaming Architecture

Use the worker to stream command output chunks as it reads from SSH.

Flow:

1. Worker starts approved command.
2. Worker creates the `command_executions` row and appends `command.started`.
3. Worker emits output chunks as they arrive.
4. Backend streams chunks to the browser through SSE or WebSocket.
5. Worker stores sanitized chunks in Postgres linked to the command execution.
6. Worker updates final command metadata and appends `command.completed`, `command.failed`, or `command.timed_out`.
7. UI marks command as complete and keeps transcript available.

Preferred transport:

- SSE for simplicity and one-way live updates.
- WebSocket only if we need bidirectional terminal controls later.
- Polling fallback for reliability.

The command input is still normal HTTP API, not direct terminal stdin.

## Events

Add these event types:

- `terminal.session_opened`
- `terminal.command_rendered`
- `terminal.output_chunk`
- `terminal.output_truncated`
- `terminal.session_closed`
- `manual_step.entered`

Command events remain authoritative:

- `command.started`
- `command.completed`
- `command.failed`
- `command.timed_out`

Each output chunk should include:

- `run_id`
- `command_execution_id`
- `sequence`
- `stream`: `stdout` or `stderr`
- `content`
- `redacted`: boolean
- `created_at`

## Postgres Storage

Add a `command_output_chunks` table.

Fields:

- `id`
- `command_execution_id`
- `run_id`
- `sequence`
- `stream`
- `content`
- `redacted`
- `created_at`

Constraints:

- Unique `(command_execution_id, sequence)`.
- Chunks must reference an existing `command_executions` row.
- Chunks are append-only.

Retention:

- Keep chunks for the hackathon run history.
- Cap each command's stored output to a configured limit.
- When output exceeds the limit, store a `terminal.output_truncated` event and stop storing further chunks while still allowing the process to complete or timeout.

## Redaction

Redaction must happen before:

- storing chunks in Postgres,
- streaming chunks to the browser,
- showing terminal transcripts,
- including evidence in activity drafts.

The terminal should display a visible placeholder such as `[REDACTED_SECRET]` so the technician understands content was removed.

## UI Requirements

The terminal panel should support:

- Monospace transcript.
- Live auto-scroll with a pause-scroll toggle.
- stdout/stderr visual distinction.
- Copy command button.
- Expand/collapse long output.
- Clear marker for approved, rejected, blocked, timed out, and completed commands.
- Abort current run button near the terminal.
- Manual command input with safety classification before approval.
- Links from terminal output to the "Logs & files checked" evidence panel when a command inspected a log, journal, config, metadata source, or endpoint.

The UI must not show:

- SSH private key content.
- Phoenix token.
- LLM API key.
- raw unredacted environment output.

## Safety Requirements

The terminal must reinforce these constraints:

- No command line appears as executed unless approval exists.
- Edited commands are reclassified and then approved.
- Blocked commands appear in the transcript as blocked, not executed.
- Abort sends a cancellation request to the worker and records `run.aborted` or `command.cancel_requested`.
- If cancellation cannot stop the remote process immediately, the UI shows that clearly and waits for timeout/final status.

## Activity Generation

The activity generator should use completed command executions and summarized output, not raw terminal transcripts.

It should also use the inspected evidence ledger specified in [evidence-log-spec.md](evidence-log-spec.md), so the final activity can say which logs/files were checked and what they proved.

Terminal output is evidence, but Phoenix activity text should remain clean:

- Include relevant command classes.
- Include key commands when useful.
- Include validation facts.
- Exclude noisy logs and secrets.

## Demo Value

The live terminal gives the jury immediate proof of:

- human approval before execution,
- real SSH work,
- exact commands,
- real remote output,
- safety checks,
- auditability,
- validation evidence.

This directly supports categories B, C, and D.
