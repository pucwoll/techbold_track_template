from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from .safety_layer import redact_output


@dataclass(frozen=True)
class BackupRequirement:
    required: bool
    source_path: str | None
    reason: str
    backup_type: str = "not_applicable"


@dataclass(frozen=True)
class BackupPlan:
    backup_path: str | None
    restore_command: str | None
    persistent_across_reboot: bool


@dataclass(frozen=True)
class DetectedBackupRecord:
    source_path: str
    backup_path: str
    backup_type: str
    reason: str
    restore_command: str
    stored_content: bool
    redacted: bool
    persistent_across_reboot: bool
    pre_change_hash: str | None = None
    owner_before: str | None = None
    group_before: str | None = None
    mode_before: str | None = None
    size_before: int | None = None
    mtime_before: str | None = None
    checksum_before: str | None = None
    sanitized_diff: str | None = None


_PATH_RE = re.compile(r"(?:/[\w.@:+-]+)+(?:\.[\w+-]+)?")
_BACKUP_PREFIXES = (
    "/var/backups/techbold-autopilot/",
    "/tmp/techbold-autopilot-backups/",
)
_BROAD_PATHS = {"/", "/etc", "/home", "/var", "/srv"}
_SERVICE_STATE_ACTIONS = {"enable", "disable", "restart", "reload", "try-restart", "start", "stop"}


def backup_requirement_for_command(command: str, risk_class: str) -> BackupRequirement:
    parts = _safe_split(command)
    if not parts:
        return BackupRequirement(False, None, "No command parsed.")
    if is_targeted_backup_command(command):
        return BackupRequirement(False, None, "Targeted backup command does not require a prior backup.")
    if _sed_inline_backup(parts):
        return BackupRequirement(False, None, "The command creates its targeted pre-change backup atomically.")

    executable = parts[0].split("/")[-1]
    paths = _paths(parts[1:])

    if executable == "systemctl":
        action, unit = _systemctl_action_and_unit(parts)
        if action in _SERVICE_STATE_ACTIONS and unit:
            return BackupRequirement(True, unit, f"Service state change requires previous systemd state for {unit}.", "service_state")
    if risk_class != "MEDIUM_RISK":
        return BackupRequirement(False, None, "Backup not required for read-only or low-risk command.")
    if executable in {"sed", "tee", "install", "cp", "mv"}:
        source_path = _first_persistent_path(paths)
        if source_path:
            return BackupRequirement(True, source_path, f"Persistent file change requires rollback support for {source_path}.", "file_copy")
    if executable in {"chmod", "chown"}:
        source_path = _first_persistent_path(paths)
        if source_path:
            return BackupRequirement(True, source_path, f"Ownership or mode change requires original metadata for {source_path}.", "metadata_snapshot")

    return BackupRequirement(False, None, "No persistent file, metadata, or service enablement target was detected.")


def backup_plan_for_requirement(
    *,
    run_id: int,
    ticket_id: int,
    requirement: BackupRequirement,
) -> BackupPlan:
    if not requirement.required or not requirement.source_path:
        return BackupPlan(None, None, False)
    if requirement.backup_type == "file_copy":
        name = requirement.source_path.rstrip("/").rsplit("/", 1)[-1] or "source"
        backup_path = f"/var/backups/techbold-autopilot/{ticket_id}/{run_id}/{name}.prechange"
        return BackupPlan(
            backup_path=backup_path,
            restore_command=f"cp -a {shlex.quote(backup_path)} {shlex.quote(requirement.source_path)}",
            persistent_across_reboot=True,
        )
    if requirement.backup_type == "metadata_snapshot":
        return BackupPlan(
            backup_path=None,
            restore_command=(
                "Restore recorded owner/group/mode for "
                f"{shlex.quote(requirement.source_path)} with targeted chown/chmod after reviewing the metadata snapshot."
            ),
            persistent_across_reboot=True,
        )
    if requirement.backup_type == "service_state":
        return BackupPlan(
            backup_path=None,
            restore_command=(
                "Restore recorded systemd enablement state for "
                f"{shlex.quote(requirement.source_path)} with systemctl enable/disable as captured."
            ),
            persistent_across_reboot=True,
        )
    return BackupPlan(None, None, False)


def is_targeted_backup_command(command: str) -> bool:
    return detect_backup_record(
        run_id=0,
        ticket_id=0,
        command_execution_id=0,
        command=command,
    ) is not None


def detect_backup_record(
    *,
    run_id: int,
    ticket_id: int,
    command_execution_id: int,
    command: str,
    output: str = "",
) -> DetectedBackupRecord | None:
    del run_id, ticket_id, command_execution_id
    parts = _safe_split(command)
    if len(parts) < 3:
        return None
    executable = parts[0].split("/")[-1]
    if executable == "sed":
        inline_backup = _sed_inline_backup(parts)
        if inline_backup:
            source_path, backup_path = inline_backup
            return DetectedBackupRecord(
                source_path=source_path,
                backup_path=backup_path,
                backup_type="file_copy",
                reason=f"Atomic pre-change backup created for {source_path}.",
                restore_command=f"cp -a {shlex.quote(backup_path)} {shlex.quote(source_path)}",
                stored_content=False,
                redacted=False,
                persistent_across_reboot=True,
            )
        return None
    if executable == "systemctl":
        action, unit = _systemctl_action_and_unit(parts)
        service_state = _parse_service_state_snapshot(output)
        if action in {"show", "status"} and unit and service_state:
            active_state = service_state.get("active_state")
            unit_file_state = service_state.get("unit_file_state")
            return DetectedBackupRecord(
                source_path=unit,
                backup_path=None,
                backup_type="service_state",
                reason=(
                    f"Pre-change systemd state for {unit}: "
                    f"active={active_state or 'unknown'}, enabled={unit_file_state or 'unknown'}."
                ),
                restore_command=_service_state_restore_command(unit, active_state, unit_file_state),
                stored_content=True,
                redacted=False,
                persistent_across_reboot=True,
            )
        return None
    if executable == "diff":
        path_args = _paths(parts[1:])
        if len(path_args) < 2:
            return None
        backup_path = path_args[-2]
        source_path = path_args[-1]
        if not _is_backup_path(backup_path) or not _first_persistent_path([source_path]) or _looks_secret_path(source_path):
            return None
        sanitized_diff, redacted = redact_output(output)
        redacted = redacted or "[REDACTED_SECRET]" in sanitized_diff
        if not sanitized_diff.strip():
            return None
        return DetectedBackupRecord(
            source_path=source_path,
            backup_path=backup_path,
            backup_type="config_dump",
            reason=f"Sanitized config diff recorded for {source_path} against {backup_path}.",
            restore_command=f"cp -a {shlex.quote(backup_path)} {shlex.quote(source_path)}",
            stored_content=True,
            redacted=redacted,
            persistent_across_reboot=True,
            sanitized_diff=sanitized_diff,
        )
    if executable == "stat":
        path = _first_persistent_path(_paths(parts[1:]))
        metadata = _parse_metadata_snapshot(output)
        if path and metadata:
            checksum = metadata.get("checksum")
            return DetectedBackupRecord(
                source_path=path,
                backup_path=None,
                backup_type="metadata_snapshot",
                reason=f"Pre-change metadata snapshot for {path}.",
                restore_command=(
                    f"chown {shlex.quote(metadata['owner'])}:{shlex.quote(metadata['group'])} {shlex.quote(path)} && "
                    f"chmod {shlex.quote(metadata['mode'])} {shlex.quote(path)}"
                ),
                stored_content=False,
                redacted=False,
                persistent_across_reboot=True,
                pre_change_hash=checksum,
                owner_before=metadata.get("owner"),
                group_before=metadata.get("group"),
                mode_before=metadata.get("mode"),
                size_before=int(metadata["size"]) if metadata.get("size", "").isdigit() else None,
                mtime_before=metadata.get("mtime"),
                checksum_before=checksum,
            )
        return None
    if executable != "cp":
        return None

    path_args = _paths(parts[1:])
    if len(path_args) < 2:
        return None
    source_path = path_args[-2]
    backup_path = path_args[-1]
    if _is_broad_path(source_path) or not _is_backup_path(backup_path):
        return None
    if _looks_secret_path(source_path):
        return None

    return DetectedBackupRecord(
        source_path=source_path,
        backup_path=backup_path,
        backup_type="file_copy",
        reason=f"Targeted pre-change backup for {source_path}.",
        restore_command=f"cp -a {shlex.quote(backup_path)} {shlex.quote(source_path)}",
        stored_content=False,
        redacted=False,
        persistent_across_reboot=backup_path.startswith("/var/backups/"),
    )


def backup_record_satisfies(
    *,
    source_path: str | None,
    record_source_path: str | None,
    record_type: str,
    backup_created: bool,
) -> bool:
    if record_type == "not_applicable":
        return source_path is None or record_source_path == source_path
    if not backup_created:
        return False
    if source_path is None:
        return True
    return record_source_path == source_path


def _safe_split(command: str) -> list[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        return []
    if len(parts) >= 3 and parts[0].split("/")[-1] == "sudo" and parts[1] in {"-n", "--non-interactive"}:
        return parts[2:]
    return parts


def _sed_inline_backup(parts: list[str]) -> tuple[str, str] | None:
    if len(parts) != 4 or parts[0].split("/")[-1] != "sed":
        return None
    backup_option = parts[1]
    if not backup_option.startswith("-i.") or backup_option == "-i.":
        return None
    source_path = parts[3]
    if not _first_persistent_path([source_path]):
        return None
    suffix = backup_option.removeprefix("-i")
    if not re.fullmatch(r"\.[A-Za-z0-9_.-]+", suffix):
        return None
    return source_path, f"{source_path}{suffix}"


def _paths(parts: list[str]) -> list[str]:
    return [part for part in parts if _PATH_RE.fullmatch(part)]


def _first_persistent_path(paths: list[str]) -> str | None:
    for path in paths:
        if path.startswith(("/etc/", "/srv/", "/var/www/", "/opt/")) and not _is_backup_path(path):
            return path
    return None


def _is_backup_path(path: str) -> bool:
    return path.startswith(_BACKUP_PREFIXES)


def _is_broad_path(path: str) -> bool:
    return path.rstrip("/") in _BROAD_PATHS


def _looks_secret_path(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in (".env", "id_rsa", "id_ed25519", "private_key", "credential", "token"))


def _parse_metadata_snapshot(output: str) -> dict[str, str] | None:
    metadata: dict[str, str] = {}
    for token in re.split(r"[\s\n]+", output.strip()):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key in {"owner", "group", "mode", "size", "mtime", "checksum"} and value:
            metadata[key] = value
    required = {"owner", "group", "mode", "size", "mtime"}
    if required.issubset(metadata):
        return metadata
    return None


def _systemctl_action_and_unit(parts: list[str]) -> tuple[str | None, str | None]:
    action: str | None = None
    unit: str | None = None
    skip_next = False
    for part in parts[1:]:
        if skip_next:
            skip_next = False
            continue
        if part in {"-p", "--property"}:
            skip_next = True
            continue
        if part.startswith("--property="):
            continue
        if part.startswith("-"):
            continue
        if action is None:
            action = part
            continue
        unit = part
    return action, unit


def _parse_service_state_snapshot(output: str) -> dict[str, str] | None:
    active_state: str | None = None
    unit_file_state: str | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("ActiveState="):
            active_state = stripped.split("=", 1)[1].strip() or active_state
        elif stripped.startswith("UnitFileState="):
            unit_file_state = stripped.split("=", 1)[1].strip() or unit_file_state
        elif stripped.startswith("Active:"):
            match = re.search(r"Active:\s+([A-Za-z-]+)", stripped)
            if match:
                active_state = match.group(1)
        elif stripped.startswith("Loaded:"):
            match = re.search(r";\s*(enabled|disabled|static|masked|generated|indirect|linked|transient)\b", stripped)
            if match:
                unit_file_state = match.group(1)
    if active_state or unit_file_state:
        return {"active_state": active_state or "", "unit_file_state": unit_file_state or ""}
    return None


def _service_state_restore_command(unit: str, active_state: str | None, unit_file_state: str | None) -> str:
    quoted_unit = shlex.quote(unit)
    active = (active_state or "").lower()
    enabled = (unit_file_state or "").lower()
    if enabled in {"enabled", "linked"}:
        if active == "active":
            return f"systemctl enable --now {quoted_unit}"
        return f"systemctl enable {quoted_unit}"
    if enabled in {"disabled", "masked"}:
        if active == "active":
            return f"systemctl disable {quoted_unit}"
        return f"systemctl disable --now {quoted_unit}"
    if active == "active":
        return f"systemctl start {quoted_unit}"
    if active in {"inactive", "failed"}:
        return f"systemctl stop {quoted_unit}"
    return f"Review recorded systemd state before restoring {quoted_unit}."
