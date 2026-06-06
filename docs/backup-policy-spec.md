# Backup and Rollback Policy Spec

## Decision

Do not attempt a full machine backup before every ticket. Use targeted pre-change backups and rollback records before making any system change.

Full VM backups are only feasible if the organizers provide cloud, hypervisor, or snapshot API access. The case brief provides SSH access and a Phoenix reset endpoint, not VM snapshot permissions. A broad backup over SSH would be slow, intrusive, likely to copy secrets/customer data, and would conflict with the scoring emphasis on minimal changes and secret protection.

## Backup Levels

### 1. Full VM Snapshot

Default: not supported.

Use only if techbold explicitly provides snapshot API credentials or a supported endpoint.

Why not by default:

- SSH alone cannot create a real VM snapshot.
- Filesystem-level copies are not crash-consistent.
- Copying `/`, `/home`, `/var`, or database directories risks customer data exposure.
- It wastes evaluation time.
- It may violate the "minimal changes" and "secret protection" rubric.

### 2. Phoenix Reset Endpoint

The Phoenix reset endpoint is not a backup.

Use it for development/demo resets only:

- resets all team VMs to initial state,
- clears created activities,
- useful before rehearsals,
- not useful for preserving work during a live repair.

### 3. Targeted Pre-Change Backup

Default policy for this app.

Before an approved fix command modifies a file or persistent system setting, create a targeted backup or rollback record for exactly the thing being changed.

Examples:

- Before editing `/etc/nginx/sites-enabled/default`, copy that one file to a run-specific backup path.
- Before changing ownership on `/srv/app/uploads`, record current owner, group, mode, and path metadata.
- Before changing a systemd unit override, record `systemctl cat <unit>` and current enablement state.
- Before changing a config value, record pre-change checksum and a sanitized diff.

No backup is needed before read-only diagnostics.

## Remote Backup Location

Preferred remote location:

```text
/var/backups/techbold-autopilot/<ticket_id>/<run_id>/
```

Requirements:

- Directory mode `0700`.
- Owned by root or the SSH user, depending on permission constraints.
- File backups preserve original mode and ownership where feasible.
- Backup paths include timestamp and original path suffix.
- Backup commands are shown to and approved by the technician.

If `/var/backups` is not writable, use:

```text
/tmp/techbold-autopilot-backups/<ticket_id>/<run_id>/
```

Use `/tmp` only for short-lived rollback during the run, and state clearly that it is not persistent across reboot.

## Local/Postgres Backup Records

Postgres should store metadata and sanitized rollback instructions, not raw secret-bearing file contents.

Store:

- original path,
- backup path,
- file type,
- pre-change checksum when safe,
- post-change checksum when available,
- owner/group/mode metadata,
- command that created the backup,
- run and ticket IDs,
- restore command proposal,
- redaction marker,
- reason for backup.

Avoid storing:

- raw `.env` files,
- private keys,
- credential files,
- database dumps,
- customer data files,
- full log files.

If a config file may contain secrets, keep the backup on the remote host with restrictive permissions and store only metadata plus a sanitized summary in Postgres.

## Database and Customer Data Policy

Do not dump, copy, delete, reinitialize, or migrate customer databases as a generic backup step.

Database work is high-risk and should be avoided unless the ticket and evidence specifically point to database service configuration. Even then:

- back up config files only,
- do not copy table data,
- do not run destructive SQL,
- do not reinitialize data directories,
- ask for explicit technician approval with a high-risk warning.

## Backup Events

Add these event types:

- `backup.plan_created`
- `backup.skipped_read_only`
- `backup.approval_requested`
- `backup.created`
- `backup.failed`
- `backup.restore_proposed`
- `backup.restored`
- `backup.not_applicable`

Every backup command is also a normal command execution and appears in:

- audit events,
- live terminal,
- command execution log,
- evidence/backup UI.

## Postgres Data Model

Add `backup_records`.

Fields:

- `id`
- `run_id`
- `ticket_id`
- `command_execution_id`
- `source_path`
- `backup_path`
- `backup_type`: `file_copy`, `metadata_snapshot`, `service_state`, `config_dump`, `not_applicable`
- `reason`
- `pre_change_hash`
- `post_change_hash`
- `owner_before`
- `group_before`
- `mode_before`
- `restore_command`
- `stored_content`: boolean
- `redacted`: boolean
- `created_at`

Rules:

- Append-only.
- Every backup record links to the command that created it or explains why no backup was applicable.
- Restore commands require the same safety classification and technician approval as fix commands.

## Agent Policy

The fix planner must decide whether backup is required before proposing a write.

Required backup before:

- editing config files,
- changing service enablement,
- adding/removing systemd overrides,
- changing ownership or permissions,
- deleting or moving any file,
- changing persistent application state.

Backup usually not required before:

- read-only diagnostics,
- service status checks,
- log reads,
- config syntax checks,
- endpoint validation,
- restarting a service without config/state change.

For every medium-risk fix proposal, the agent output should include:

- whether a backup is required,
- exact backup command,
- exact fix command,
- exact restore command,
- why this is minimal.

## UI Requirements

Add a "Backups & rollback" panel to the run console.

Show:

- backup status per proposed fix,
- original path,
- backup path,
- backup command,
- restore command,
- timestamp,
- whether the backup is persistent across reboot,
- whether secrets were redacted or content was not stored locally.

Before executing a fix, the approval card should show:

- backup required: yes/no,
- backup already created: yes/no,
- rollback available: yes/no,
- restore command preview.

## Safety Rules

Block backup commands that:

- copy broad directories such as `/`, `/etc`, `/home`, `/var`, `/srv`, or database data directories,
- archive customer data,
- copy private keys or `.env` files into Postgres/local storage,
- run database dumps by default,
- clear logs/history,
- create world-readable backup files.

Allowed backup commands must be narrow, path-specific, and justified by the proposed change.

## Activity Generation

The final Phoenix activity should mention backups only when relevant:

- "Created a targeted backup of `/etc/nginx/sites-enabled/default` before editing."
- "Recorded original ownership and mode for `/srv/app/uploads` before correcting permissions."

Do not include backup file contents or secret-bearing paths unless necessary and safe.

## Mentor Question

Ask techbold mentors whether a VM snapshot API exists. If they provide one, add it as an optional pre-run action behind technician approval. Until then, use targeted pre-change backups only.
