# Evidence and Checked Logs Spec

## Goal

The technician should be able to see every related log file, journal source, and diagnostic file the agent checked or opened during a run.

This is separate from the live terminal:

- The live terminal shows command execution and output as it happens.
- The evidence ledger shows the durable list of files/log sources inspected and why they mattered.

The ledger helps the demo and the final activity because it makes the diagnosis traceable.

## What Counts as an Evidence Source

Record an evidence source whenever a command reads or inspects:

- log files, such as `/var/log/nginx/error.log`, `/var/log/syslog`, app logs, or service logs,
- systemd journal streams, such as `journalctl -u nginx`,
- service status output, such as `systemctl status nginx`,
- config files used to explain or fix an issue,
- file metadata used as evidence, such as `stat`, `ls -la`, or ownership/permission checks,
- local endpoint responses used as validation evidence.

The UI label should be "Logs & files checked" because that is what the technician expects, but the data model should support multiple source types.

## UI Behavior

The run console should include a "Logs & files checked" panel.

Each row should show:

- source type: `file`, `journal`, `service_status`, `config`, `metadata`, or `endpoint`,
- path or source name,
- command that opened or checked it,
- timestamp,
- actor: agent or technician,
- purpose,
- short sanitized excerpt or key finding,
- whether secrets were redacted,
- linked command transcript,
- whether the source supports root cause, fix choice, or validation.

Examples:

```text
journal · nginx
Opened by: journalctl -u nginx --no-pager -n 80
Finding: nginx failed to bind because port 80 was already in use.
Supports: root cause

file · /etc/nginx/sites-enabled/default
Opened by: nginx -T
Finding: server block listens on port 80.
Supports: fix choice

metadata · /var/www/uploads
Opened by: stat /var/www/uploads
Finding: directory owned by root, service runs as www-data.
Supports: root cause
```

The technician should be able to click an evidence row to jump to the relevant terminal transcript and output excerpt.

## Backend Recording Rules

Every command result should be inspected for evidence source metadata.

Sources can be recorded in two ways:

- deterministic extraction from command patterns,
- agent annotation after interpreting output.

Deterministic extraction examples:

- `journalctl -u nginx ...` records source type `journal`, source `nginx`.
- `tail -n 100 /var/log/nginx/error.log` records source type `file`, path `/var/log/nginx/error.log`.
- `cat /etc/nginx/nginx.conf` records source type `config`, path `/etc/nginx/nginx.conf`.
- `systemctl status redis --no-pager` records source type `service_status`, source `redis`.
- `stat /srv/app/uploads` records source type `metadata`, path `/srv/app/uploads`.
- `curl -i http://localhost:8080/health` records source type `endpoint`, source `http://localhost:8080/health`.

Agent annotation should add:

- purpose,
- finding,
- relation to hypothesis/root cause/fix/validation,
- confidence.

The system should not rely only on the LLM for source extraction because the ledger is an audit artifact.

## Postgres Storage

Add `inspected_sources`.

Fields:

- `id`
- `run_id`
- `command_execution_id`
- `source_type`
- `source_name`
- `path`
- `command`
- `purpose`
- `finding`
- `supports`
- `sanitized_excerpt`
- `redacted`
- `line_range`
- `created_at`

Suggested values:

- `source_type`: `file`, `journal`, `service_status`, `config`, `metadata`, `endpoint`, `other`
- `supports`: `hypothesis`, `root_cause`, `fix_choice`, `validation`, `context`, `none`

Rules:

- Append-only.
- Store sanitized excerpts only.
- Do not store entire large log files.
- Do not store secret-bearing files.
- Link every source to the command that checked it.

## Events

Add these events:

- `evidence.source_detected`
- `evidence.source_opened`
- `evidence.finding_recorded`
- `evidence.source_redacted`

These events should appear in the audit timeline and feed the "Logs & files checked" panel.

## Safety and Secret Handling

The system should refuse or strongly warn before opening likely secret files:

- `.env`
- private keys,
- credential stores,
- `/etc/shadow`,
- application secret files,
- token files.

If a command output appears to contain secrets, the redactor must run before:

- storing `inspected_sources`,
- streaming terminal output,
- rendering evidence rows,
- generating activity text.

## Activity Generation

The activity writer should use the evidence ledger to make the final Phoenix activity precise.

Examples:

- Root cause should cite the evidence source, such as "nginx journal showed bind failure on port 80."
- Actions taken should mention relevant logs checked in order.
- Validation should cite endpoint or service-status evidence.

Do not paste raw log files into the activity.

## Demo Value

The panel gives the jury a quick answer to:

- what did the agent inspect,
- why did it inspect it,
- what did it learn,
- which evidence supports the root cause,
- which evidence proves validation,
- whether secrets were protected.
