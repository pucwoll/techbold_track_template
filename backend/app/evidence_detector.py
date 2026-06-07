from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedEvidence:
    source_type: str
    source_name: str | None
    path: str | None
    purpose: str
    finding: str
    supports: str
    sanitized_excerpt: str
    redacted: bool
    line_range: str | None = None


_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
_PATH_RE = re.compile(r"(?:/[\w.@:+-]+)+(?:\.[\w+-]+)?")
_ROOT_CAUSE_RE = re.compile(
    r"\b(error|failed|failure|denied|refused|timeout|timed out|bind\(\)|no such file|not found|inactive|dead)\b",
    re.IGNORECASE,
)
_SECRET_MARKER_RE = re.compile(r"\[REDACTED_SECRET\]")
_OTHER_DIAGNOSTIC_COMMANDS = {"df", "free", "ss", "netstat", "lsof", "uname", "hostnamectl", "uptime", "ps"}


def detect_inspected_sources(
    *,
    command: str,
    sanitized_stdout: str,
    sanitized_stderr: str,
    purpose: str,
    phase: str,
    redacted: bool,
) -> list[DetectedEvidence]:
    parts = _safe_split(command)
    if not parts:
        return []

    executable = parts[0].split("/")[-1]
    combined = f"{sanitized_stdout}\n{sanitized_stderr}".strip()
    excerpt = _excerpt(combined)
    detected_redaction = redacted or bool(_SECRET_MARKER_RE.search(combined))
    supports = _support_type(command=command, output=combined, phase=phase)

    if executable == "journalctl":
        unit = _option_value(parts, "-u") or _option_value(parts, "--unit")
        if unit:
            return [
                DetectedEvidence(
                    source_type="journal",
                    source_name=_strip_service_suffix(unit),
                    path=None,
                    purpose=purpose,
                    finding=_finding(excerpt, "Journal inspected."),
                    supports=supports,
                    sanitized_excerpt=excerpt,
                    redacted=detected_redaction,
                )
            ]

    if executable == "systemctl":
        action = parts[1] if len(parts) > 1 else ""
        if action in {"status", "is-active", "is-enabled"} and len(parts) > 2:
            return [
                DetectedEvidence(
                    source_type="service_status",
                    source_name=_strip_service_suffix(parts[2]),
                    path=None,
                    purpose=purpose,
                    finding=_finding(excerpt, f"Service {action} checked."),
                    supports=supports,
                    sanitized_excerpt=excerpt,
                    redacted=detected_redaction,
                )
            ]
        if action == "--failed":
            return [
                DetectedEvidence(
                    source_type="service_status",
                    source_name="failed-units",
                    path=None,
                    purpose=purpose,
                    finding=_finding(excerpt, "Failed systemd units checked."),
                    supports=supports,
                    sanitized_excerpt=excerpt,
                    redacted=detected_redaction,
                )
            ]

    if executable == "curl":
        url = next((part for part in parts[1:] if _URL_RE.fullmatch(part)), None)
        if url:
            return [
                DetectedEvidence(
                    source_type="endpoint",
                    source_name=url,
                    path=None,
                    purpose=purpose,
                    finding=_finding(excerpt, "Endpoint checked."),
                    supports="validation" if phase == "validation" else supports,
                    sanitized_excerpt=excerpt,
                    redacted=detected_redaction,
                )
            ]

    if executable in {"stat", "ls"}:
        path = _first_path(parts[1:])
        if path:
            return [_path_evidence("metadata", path, purpose, excerpt, supports, detected_redaction)]

    if executable in {"cat", "tail", "head", "less", "more", "grep", "sed", "awk"}:
        path = _first_path(parts[1:])
        if path:
            path_supports = supports
            if (
                executable == "grep"
                and path.startswith("/etc/")
                and re.search(r"(?m)^PORT=\d+\s*$", combined)
            ):
                path_supports = "fix_choice"
            return [_path_evidence(_path_source_type(path), path, purpose, excerpt, path_supports, detected_redaction)]

    if executable == "nginx" and any(part in {"-t", "-T"} for part in parts[1:]):
        return [
            DetectedEvidence(
                source_type="config",
                source_name="nginx",
                path=None,
                purpose=purpose,
                finding=_finding(excerpt, "Nginx configuration checked."),
                supports="fix_choice" if supports == "context" else supports,
                sanitized_excerpt=excerpt,
                redacted=detected_redaction,
            )
        ]

    if executable in {"apachectl", "apache2ctl"} and any(part in {"configtest", "-t"} for part in parts[1:]):
        return [
            DetectedEvidence(
                source_type="config",
                source_name="apache",
                path=None,
                purpose=purpose,
                finding=_finding(excerpt, "Apache configuration checked."),
                supports="fix_choice" if supports == "context" else supports,
                sanitized_excerpt=excerpt,
                redacted=detected_redaction,
            )
        ]

    url_match = _URL_RE.search(command)
    if url_match:
        url = url_match.group(0)
        return [
            DetectedEvidence(
                source_type="endpoint",
                source_name=url,
                path=None,
                purpose=purpose,
                finding=_finding(excerpt, "Endpoint checked."),
                supports=supports,
                sanitized_excerpt=excerpt,
                redacted=detected_redaction,
            )
        ]

    if executable in _OTHER_DIAGNOSTIC_COMMANDS:
        return [
            DetectedEvidence(
                source_type="other",
                source_name=executable,
                path=_first_path(parts[1:]),
                purpose=purpose,
                finding=_finding(excerpt, f"{executable} diagnostic checked."),
                supports=supports,
                sanitized_excerpt=excerpt,
                redacted=detected_redaction,
            )
        ]

    return []


def _safe_split(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _option_value(parts: list[str], option: str) -> str | None:
    for index, part in enumerate(parts):
        if part == option and index + 1 < len(parts):
            return parts[index + 1]
        if part.startswith(f"{option}="):
            return part.split("=", 1)[1]
    return None


def _first_path(parts: list[str]) -> str | None:
    for part in parts:
        if _PATH_RE.fullmatch(part):
            return part
    return None


def _path_source_type(path: str) -> str:
    lowered = path.lower()
    if lowered.startswith("/etc/") or lowered.endswith((".conf", ".ini", ".yaml", ".yml", ".json", ".toml")):
        return "config"
    if lowered.startswith("/var/log/") or lowered.endswith(".log"):
        return "file"
    return "file"


def _path_evidence(
    source_type: str,
    path: str,
    purpose: str,
    excerpt: str,
    supports: str,
    redacted: bool,
) -> DetectedEvidence:
    return DetectedEvidence(
        source_type=source_type,
        source_name=path,
        path=path,
        purpose=purpose,
        finding=_finding(excerpt, f"{source_type.replace('_', ' ').title()} inspected."),
        supports="fix_choice" if source_type in {"config", "metadata"} and supports == "context" else supports,
        sanitized_excerpt=excerpt,
        redacted=redacted,
    )


def _support_type(*, command: str, output: str, phase: str) -> str:
    if phase == "validation" or " is-active " in f" {command} " or "curl " in command:
        return "validation"
    if _ROOT_CAUSE_RE.search(output):
        return "root_cause"
    if phase == "fix":
        return "fix_choice"
    if phase == "diagnostic":
        return "hypothesis"
    return "context"


def _excerpt(output: str) -> str:
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line:
            return line[:280]
    return "No output captured."


def _finding(excerpt: str, fallback: str) -> str:
    return excerpt if excerpt and excerpt != "No output captured." else fallback


def _strip_service_suffix(value: str) -> str:
    return value.removesuffix(".service")
