from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import paramiko


ChunkCallback = Callable[[str, str], None]
HOST_KEY_POLICIES = {"accept-new", "strict", "insecure-ignore"}
SSH_READ_SIZE = 4096
SSH_READ_POLL_S = 0.05


@dataclass(frozen=True)
class CommandTarget:
    host: str
    port: int
    username: str
    os: str | None = None
    key_number: int | None = None


@dataclass(frozen=True)
class CommandExecutionResult:
    exit_code: int | None
    timed_out: bool
    duration_ms: int
    error: str | None = None


class CommandRunner(Protocol):
    def execute(
        self,
        *,
        target: CommandTarget,
        command: str,
        timeout_s: int,
        on_chunk: ChunkCallback,
    ) -> CommandExecutionResult:
        ...


class SSHCommandRunner:
    def __init__(
        self,
        *,
        private_key_path: str | None,
        private_key_dir: str | None = None,
        connect_timeout_s: int = 10,
        known_hosts_path: str | None = None,
        host_key_policy: str = "accept-new",
        client_factory: Callable[[], paramiko.SSHClient] = paramiko.SSHClient,
        private_key_loader: Callable[[str], paramiko.PKey] = paramiko.PKey.from_path,
    ) -> None:
        if host_key_policy not in HOST_KEY_POLICIES:
            raise ValueError(f"Unsupported SSH host key policy: {host_key_policy}")
        self.private_key_path = private_key_path
        self.private_key_dir = private_key_dir
        self.connect_timeout_s = connect_timeout_s
        self.host_key_policy = host_key_policy
        self.client_factory = client_factory
        self.private_key_loader = private_key_loader
        self.known_hosts_path = known_hosts_path or (
            "/dev/null" if host_key_policy == "insecure-ignore" else str(Path.home() / ".ssh" / "known_hosts")
        )

    def execute(
        self,
        *,
        target: CommandTarget,
        command: str,
        timeout_s: int,
        on_chunk: ChunkCallback,
    ) -> CommandExecutionResult:
        private_key_path = self._private_key_path_for_target(target)
        if not private_key_path:
            return CommandExecutionResult(
                exit_code=None,
                timed_out=False,
                duration_ms=0,
                error="SSH_PRIVATE_KEY_PATH is not configured.",
            )
        if not Path(private_key_path).exists():
            return CommandExecutionResult(
                exit_code=None,
                timed_out=False,
                duration_ms=0,
                error=f"SSH private key path does not exist: {private_key_path}",
            )
        try:
            private_key = self.private_key_loader(private_key_path)
        except (OSError, ValueError, paramiko.SSHException) as error:
            return CommandExecutionResult(
                exit_code=None,
                timed_out=False,
                duration_ms=0,
                error=f"SSH private key is not valid: {error}",
            )

        if self.known_hosts_path != "/dev/null":
            try:
                Path(self.known_hosts_path).parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                return CommandExecutionResult(
                    exit_code=None,
                    timed_out=False,
                    duration_ms=0,
                    error=f"Could not prepare SSH known_hosts path: {error}",
                )

        started = time.monotonic()
        client = self.client_factory()
        try:
            self._configure_host_key_policy(client)
            client.connect(
                hostname=target.host,
                port=target.port,
                username=target.username,
                pkey=private_key,
                timeout=self.connect_timeout_s,
                banner_timeout=self.connect_timeout_s,
                auth_timeout=self.connect_timeout_s,
                allow_agent=False,
                look_for_keys=False,
            )
            transport = client.get_transport()
            if transport is None:
                raise RuntimeError("SSH transport was not established.")
            channel = transport.open_session(timeout=self.connect_timeout_s)
            if hasattr(channel, "set_combine_stderr"):
                channel.set_combine_stderr(False)
            channel.exec_command(command)
            if hasattr(channel, "shutdown_write"):
                channel.shutdown_write()
            timed_out = False
            exit_code: int | None = None
            while True:
                self._drain_available(channel, on_chunk)
                if channel.exit_status_ready():
                    self._drain_available(channel, on_chunk)
                    exit_code = channel.recv_exit_status()
                    break
                if time.monotonic() - started > timeout_s:
                    timed_out = True
                    channel.close()
                    break
                time.sleep(SSH_READ_POLL_S)

            return CommandExecutionResult(
                exit_code=exit_code,
                timed_out=timed_out,
                duration_ms=_elapsed_ms(started),
                error="Command timed out." if timed_out else None,
            )
        except (OSError, RuntimeError, paramiko.SSHException) as error:
            return CommandExecutionResult(
                exit_code=None,
                timed_out=False,
                duration_ms=_elapsed_ms(started),
                error=str(error),
            )
        finally:
            client.close()

    def _private_key_path_for_target(self, target: CommandTarget) -> str | None:
        if target.key_number is not None and self.private_key_dir:
            return str(Path(self.private_key_dir) / f"case{target.key_number}_key.pem")
        return self.private_key_path

    def _configure_host_key_policy(self, client: paramiko.SSHClient) -> None:
        if self.host_key_policy == "insecure-ignore":
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            return

        known_hosts = Path(self.known_hosts_path)
        if known_hosts.exists():
            client.load_host_keys(str(known_hosts))

        if self.host_key_policy == "strict":
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            return

        client.set_missing_host_key_policy(_AcceptNewHostKeyPolicy(self.known_hosts_path))

    def _drain_available(self, channel: paramiko.Channel, on_chunk: ChunkCallback) -> None:
        while channel.recv_ready() or channel.recv_stderr_ready():
            if channel.recv_ready():
                chunk = channel.recv(SSH_READ_SIZE)
                if chunk:
                    on_chunk("stdout", chunk.decode("utf-8", errors="replace"))
            if channel.recv_stderr_ready():
                chunk = channel.recv_stderr(SSH_READ_SIZE)
                if chunk:
                    on_chunk("stderr", chunk.decode("utf-8", errors="replace"))


class _AcceptNewHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, known_hosts_path: str) -> None:
        self.known_hosts_path = known_hosts_path

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(self.known_hosts_path)


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
