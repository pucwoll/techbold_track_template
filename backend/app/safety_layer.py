from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SafetyVerdict:
    verdict: str
    risk_class: str
    summary: str
    notes: list[str] = field(default_factory=list)


_BLOCK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+[^;\n|&]*-[^\s]*r[^\s]*f[^\n]*(?:\s|=)/(?:$|\s|etc\b|home\b|srv\b|var\b)"), "Broad recursive deletion is blocked."),
    (re.compile(r"\brm\s+[^;\n|&]*(?:/var/lib/(?:postgresql|mysql|redis)|/var/lib/docker)\b"), "Database or service data deletion is blocked."),
    (re.compile(r"\bchmod\s+[^;\n|&]*-[^\s]*r[^\s]*[^;\n|&]*\b777\b[^;\n|&]*(?:/|/etc\b|/home\b|/srv\b|/var\b)"), "Blanket recursive world-writable permissions are blocked."),
    (re.compile(r"\bch(?:own|mod)\s+[^;\n|&]*-[^\s]*r[^\s]*[^;\n|&]*(?:\s|=)/(?:etc|home|srv|var)\b"), "Broad recursive ownership or permission changes are blocked."),
    (re.compile(r"(?:^|\s)(?:cat|tail|head|less|more|sed|awk|grep)\b[^;\n|&]*(?:/etc/shadow|\.env\b|id_rsa|id_ed25519|ssh_host_[A-Za-z0-9_]*_key|private[_-]?key|credential|token)"), "Reading likely secret material is blocked."),
    (re.compile(r"\b(?:history\s+-c|rm\s+[^;\n|&]*(?:/\.bash_history|/\.zsh_history)|truncate\s+[^;\n|&]*/var/log|journalctl\s+--vacuum)"), "Clearing logs or shell history is blocked."),
    (re.compile(r"\b(?:ufw\s+disable|iptables\s+-F|nft\s+flush|setenforce\s+0|systemctl\s+(?:stop|disable)\s+(?:auditd|ufw|firewalld|apparmor))\b"), "Disabling firewall, audit, or security controls is blocked."),
    (re.compile(r"\b(?:curl|wget)\b[^;\n|&]*(?:\||\)\s*\|)\s*(?:sudo\s+)?(?:bash|sh)\b"), "Piping remote scripts directly into a shell is blocked."),
    (re.compile(r"\b(?:mkfs|fdisk|parted|wipefs|dd\s+if=|mount\s+-o\s+remount,rw\s+/|grub-install|grub-mkconfig|update-grub|bootctl|efibootmgr|kexec\b|modprobe\b|sysctl\s+-w\s+kernel\.)\b"), "Destructive storage, kernel, and boot operations are blocked."),
    (re.compile(r"\b(?:drop\s+database|dropdb|mysqladmin\s+drop|psql\b[^;\n|&]*\bDROP\s+DATABASE)\b", re.IGNORECASE), "Database reinitialization or deletion is blocked."),
    (re.compile(r"\b(?:sudo\s+su\b|su\s+-|bash\s+-i|sh\s+-i|python\d?\s+-c\s+['\"].*pty\.spawn)"), "Interactive privilege escalation shells are blocked."),
    (re.compile(r"(?:^|\s)(?:top|htop|watch|less|more|vi|vim|nano)\b|\b(?:tail|journalctl)\b[^;\n|&]*(?:--follow|\s-[A-Za-z]*f[A-Za-z]*)\b"), "Long-running interactive sessions are blocked."),
    (re.compile(r"\b(?:base64\s+-d|openssl\s+enc|eval\s+\$|bash\s+-c\s+\$)\b"), "Obfuscated command execution is blocked."),
    (re.compile(r"\b(?:apt(?:-get)?|yum|dnf|apk|zypper|pacman|pip(?:3)?|npm|pnpm|yarn)\s+(?:install|add|-S)\b"), "Package installation is blocked in the automated fix flow."),
)

_READ_ONLY_COMMANDS = {
    "hostnamectl",
    "uname",
    "uptime",
    "df",
    "free",
    "ss",
    "netstat",
    "lsof",
    "stat",
    "ls",
    "cat",
    "tail",
    "head",
    "grep",
    "journalctl",
    "systemctl",
    "curl",
    "nginx",
    "apachectl",
    "apache2ctl",
}

_MUTATING_COMMANDS = {
    "chmod",
    "chown",
    "cp",
    "install",
    "mkdir",
    "mv",
    "rm",
    "sed",
    "tee",
    "touch",
    "truncate",
}
_SENSITIVE_GLOB_PATH_RE = re.compile(r"^/(?:etc|home|opt|root|srv|var)(?:/|$)")

_SECRET_ENV_RE = re.compile(
    r"(?im)^[+\-\s]?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API[_-]?KEY|CREDENTIAL|PRIVATE[_-]?KEY)[A-Z0-9_]*\s*=.*$"
)
_BEARER_LINE_RE = re.compile(r"(?im)^.*\bBearer\s+[A-Za-z0-9._~+/=-]{8,}.*$")
_PASSWORD_URI_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://[^:\s/@]+):(?P<password>[^@\s/]+)@", re.IGNORECASE)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)


def classify_command(command: str) -> SafetyVerdict:
    stripped = command.strip()
    if not stripped:
        return SafetyVerdict("blocked", "BLOCKED", "Empty commands cannot be executed.", ["Enter a non-empty command."])

    normalized = _normalize_command(stripped)
    for pattern, reason in _BLOCK_PATTERNS:
        if reason == "Reading likely secret material is blocked." and _is_narrow_port_environment_command(stripped):
            continue
        if pattern.search(normalized):
            return SafetyVerdict("blocked", "BLOCKED", reason, ["Command was not queued for execution."])

    syntax_block_reason = _shell_syntax_block_reason(stripped)
    if syntax_block_reason:
        return SafetyVerdict(
            "blocked",
            "BLOCKED",
            syntax_block_reason,
            ["Submit exactly one non-composed command for classification and approval."],
        )

    try:
        parts = shlex.split(stripped)
    except ValueError:
        return SafetyVerdict("blocked", "BLOCKED", "Command could not be parsed safely.", ["Fix shell quoting before retrying."])

    if not parts:
        return SafetyVerdict("blocked", "BLOCKED", "Empty commands cannot be executed.", ["Enter a non-empty command."])
    parts = _unwrap_noninteractive_sudo(parts)

    executable = parts[0].split("/")[-1]
    if _has_unsafe_globbing(stripped, executable):
        return SafetyVerdict(
            "blocked",
            "BLOCKED",
            "Unsafe shell globbing on sensitive or mutating targets is blocked.",
            ["Use an exact path or a bounded read-only command without shell expansion."],
        )

    backup_block_reason = _backup_or_archive_block_reason(executable, parts)
    if backup_block_reason:
        return SafetyVerdict("blocked", "BLOCKED", backup_block_reason, ["Use a targeted per-file rollback command instead."])

    if _looks_read_only(executable, parts):
        return SafetyVerdict("allowed", "READ_ONLY", "Read-only diagnostic command allowed.", [])
    if _looks_low_risk(parts):
        return SafetyVerdict("allowed", "LOW_RISK", "Targeted low-risk service action allowed.", [])
    if _looks_medium_risk(parts):
        return SafetyVerdict(
            "allowed",
            "MEDIUM_RISK",
            "Targeted change requires technician review and approval.",
            ["Confirm the target path or service is exact before approving."],
        )
    if executable in _READ_ONLY_COMMANDS:
        return SafetyVerdict("allowed", "LOW_RISK", "Command appears bounded but needs technician review.", [])

    return SafetyVerdict(
        "allowed",
        "MEDIUM_RISK",
        "Command requires explicit technician review before execution.",
        ["No hard block matched, but this is not a known diagnostic pattern."],
    )


def redact_output(output: str) -> tuple[str, bool]:
    redacted = output
    redacted = _PRIVATE_KEY_RE.sub("[REDACTED_SECRET]", redacted)
    redacted = _BEARER_LINE_RE.sub("[REDACTED_SECRET]", redacted)
    redacted = _PASSWORD_URI_RE.sub(r"\g<scheme>:[REDACTED_SECRET]@", redacted)
    redacted = _SECRET_ENV_RE.sub("[REDACTED_SECRET]", redacted)
    return redacted, redacted != output


def _normalize_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip()).lower()


def _is_narrow_port_environment_command(command: str) -> bool:
    try:
        parts = _unwrap_noninteractive_sudo(shlex.split(command))
    except ValueError:
        return False
    if len(parts) == 4 and parts[:2] == ["grep", "-E"]:
        return (
            parts[2] == "^PORT=[0-9]+$"
            and parts[3].startswith("/etc/")
            and parts[3].endswith(".env")
        )
    if len(parts) == 4 and parts[0] == "sed" and parts[1] in {"-i", "-i.techbold-prechange"}:
        return (
            re.fullmatch(r"s/\^PORT=\[0-9\]\\\+\$/PORT=[0-9]+/", parts[2]) is not None
            and parts[3].startswith("/etc/")
            and parts[3].endswith(".env")
        )
    return False


def _shell_syntax_block_reason(command: str) -> str | None:
    single_quoted = False
    double_quoted = False
    escaped = False
    for index, char in enumerate(command):
        if escaped:
            escaped = False
            continue
        if char == "\\" and not single_quoted:
            escaped = True
            continue
        if char == "'" and not double_quoted:
            single_quoted = not single_quoted
            continue
        if char == '"' and not single_quoted:
            double_quoted = not double_quoted
            continue

        next_char = command[index + 1] if index + 1 < len(command) else ""
        if not single_quoted and char == "$" and next_char == "(":
            return "Command substitution is blocked."
        if not single_quoted and char == "`":
            return "Command substitution is blocked."
        if single_quoted or double_quoted:
            continue

        if char in {";", "\n"}:
            return "Multi-command shell control operators are blocked."
        if char == "&":
            if next_char == ">":
                return "Shell redirection is blocked."
            return "Background or multi-command shell control operators are blocked."
        if char == "|":
            if next_char == "|":
                return "Multi-command shell control operators are blocked."
            return "Shell pipes are blocked."
        if char in {"<", ">"}:
            return "Shell redirection is blocked."
        if char in {"(", ")"}:
            return "Shell grouping or subshell syntax is blocked."
    return None


def _has_unsafe_globbing(command: str, executable: str) -> bool:
    glob_tokens = _unquoted_glob_tokens(command)
    if not glob_tokens:
        return False
    if executable in _MUTATING_COMMANDS:
        return True
    return any(_SENSITIVE_GLOB_PATH_RE.match(token) for token in glob_tokens)


def _unquoted_glob_tokens(command: str) -> list[str]:
    tokens: list[tuple[str, bool]] = []
    current: list[str] = []
    has_unquoted_glob = False
    single_quoted = False
    double_quoted = False
    escaped = False

    def flush() -> None:
        nonlocal current, has_unquoted_glob
        if current:
            tokens.append(("".join(current), has_unquoted_glob))
            current = []
            has_unquoted_glob = False

    for char in command:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and not single_quoted:
            escaped = True
            continue
        if char == "'" and not double_quoted:
            single_quoted = not single_quoted
            continue
        if char == '"' and not single_quoted:
            double_quoted = not double_quoted
            continue
        if char.isspace() and not single_quoted and not double_quoted:
            flush()
            continue
        if not single_quoted and not double_quoted and char in {"*", "?", "[", "{"}:
            has_unquoted_glob = True
        current.append(char)
    flush()
    return [token for token, has_glob in tokens if has_glob]


def _backup_or_archive_block_reason(executable: str, parts: list[str]) -> str | None:
    if executable not in {"tar", "zip", "rsync", "cp"}:
        return None
    if executable == "cp" and not any(part in {"-a", "-R", "-r", "--archive", "--recursive"} for part in parts[1:]):
        return None
    path_args = [part for part in parts[1:] if part.startswith("/")]
    for path in path_args:
        if _is_allowed_backup_destination(path):
            continue
        if _is_broad_backup_source(path):
            return "Broad archive or backup over system, data, or customer paths is blocked."
        if _looks_sensitive_backup_source(path):
            return "Archiving or backing up secrets and private key material is blocked."
    return None


def _is_allowed_backup_destination(path: str) -> bool:
    return path.startswith(("/var/backups/techbold-autopilot/", "/tmp/techbold-autopilot-backups/"))


def _is_broad_backup_source(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    if normalized in {"/", "/etc", "/home", "/var", "/srv"}:
        return True
    lowered = normalized.lower()
    return any(
        marker in lowered
        for marker in (
            "/var/lib/postgresql",
            "/var/lib/mysql",
            "/var/lib/redis",
            "/var/lib/docker",
            "customer-data",
            "customer_data",
            "/customers/",
            "/client-data",
        )
    )


def _looks_sensitive_backup_source(path: str) -> bool:
    lowered = path.lower()
    return any(
        marker in lowered
        for marker in (
            ".env",
            "id_rsa",
            "id_ed25519",
            "ssh_host_",
            "private_key",
            "private-key",
            "credential",
            "token",
        )
    )


def _looks_read_only(executable: str, parts: list[str]) -> bool:
    if executable == "systemctl":
        return len(parts) >= 2 and parts[1] in {"status", "show", "is-active", "is-enabled", "--failed", "list-units"}
    if executable == "curl":
        disallowed = {"-XPOST", "-XPUT", "-XPATCH", "-XDELETE", "--request"}
        joined = " ".join(parts).upper()
        return not any(flag in joined for flag in disallowed)
    if executable in {"nginx"}:
        return "-t" in parts or "-T" in parts
    if executable in {"apachectl", "apache2ctl"}:
        return any(part in {"configtest", "-t"} for part in parts[1:])
    return executable in {
        "hostnamectl",
        "uname",
        "uptime",
        "df",
        "free",
        "ss",
        "netstat",
        "lsof",
        "stat",
        "ls",
        "cat",
        "tail",
        "head",
        "grep",
        "journalctl",
    }


def _looks_low_risk(parts: list[str]) -> bool:
    if len(parts) >= 3 and parts[0].split("/")[-1] == "systemctl":
        return parts[1] in {"restart", "reload", "try-restart"} and _is_targeted_service(parts[2])
    return False


def _looks_medium_risk(parts: list[str]) -> bool:
    executable = parts[0].split("/")[-1]
    if executable == "systemctl" and len(parts) >= 3:
        service = next((part for part in parts[2:] if not part.startswith("-")), "")
        return parts[1] in {"enable", "disable"} and _is_targeted_service(service)
    if executable in {"chmod", "chown"}:
        return "-R" not in parts and any(part.startswith("/") for part in parts[1:])
    if executable in {"sed", "tee", "install", "cp", "mv", "mkdir"}:
        return any(part.startswith(("/etc/", "/srv/", "/var/www/", "/opt/")) for part in parts[1:])
    return False


def _is_targeted_service(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.@:-]+(?:\.service)?", value))


def _unwrap_noninteractive_sudo(parts: list[str]) -> list[str]:
    if len(parts) >= 3 and parts[0].split("/")[-1] == "sudo" and parts[1] in {"-n", "--non-interactive"}:
        return parts[2:]
    return parts
