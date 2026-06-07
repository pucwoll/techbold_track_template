import re

FORBIDDEN_PATTERNS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+/\*",
    r"chmod\s+-R\s+777\s+/",
    r"chmod\s+-R\s+777\s+/etc",
    r"chmod\s+-R\s+777\s+/var",
    r"chown\s+-R\s+.*:.*\s+/",
    r"mkfs",
    r"dd\s+if=.*\s+of=/dev/.*",
    r">.*\.bash_history",
    r"cat\s+/dev/null\s+>\s+/var/log/.*",
    r"rm\s+-rf\s+/var/lib/mysql",
    r"rm\s+-rf\s+/var/lib/postgresql",
    r"ufw\s+disable",
    r"systemctl\s+stop\s+firewalld",
]

class SafetyLayer:
    def __init__(self):
        self.compiled_patterns = [re.compile(p) for p in FORBIDDEN_PATTERNS]

    def is_safe(self, command: str) -> tuple[bool, str]:
        for pattern in self.compiled_patterns:
            if pattern.search(command):
                return False, f"Command matched forbidden pattern: {pattern.pattern}"
        return True, "Safe"

safety_layer = SafetyLayer()