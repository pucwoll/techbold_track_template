from __future__ import annotations

import tempfile
from pathlib import Path
import unittest

import paramiko

from app.ssh_runner import CommandTarget, SSHCommandRunner


class FakeHostKeys:
    def __init__(self) -> None:
        self.added: list[tuple[str, str, object]] = []

    def add(self, hostname: str, key_type: str, key: object) -> None:
        self.added.append((hostname, key_type, key))


class FakeHostKey:
    def get_name(self) -> str:
        return "ssh-ed25519"


class FakePKey:
    pass


class FakeChannel:
    def __init__(self, chunks: list[tuple[str, bytes]], *, exit_status: int = 0) -> None:
        self.chunks = chunks
        self.exit_status = exit_status
        self.command: str | None = None
        self.closed = False

    def exec_command(self, command: str) -> None:
        self.command = command

    def recv_ready(self) -> bool:
        return bool(self.chunks) and self.chunks[0][0] == "stdout"

    def recv(self, _size: int) -> bytes:
        stream, chunk = self.chunks.pop(0)
        if stream != "stdout":
            raise AssertionError(f"expected stdout chunk, got {stream}")
        return chunk

    def recv_stderr_ready(self) -> bool:
        return bool(self.chunks) and self.chunks[0][0] == "stderr"

    def recv_stderr(self, _size: int) -> bytes:
        stream, chunk = self.chunks.pop(0)
        if stream != "stderr":
            raise AssertionError(f"expected stderr chunk, got {stream}")
        return chunk

    def exit_status_ready(self) -> bool:
        return not self.chunks

    def recv_exit_status(self) -> int:
        return self.exit_status

    def close(self) -> None:
        self.closed = True


class FakeTransport:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    def open_session(self, *, timeout: int | None = None) -> FakeChannel:
        self.timeout = timeout
        return self.channel


class FakeClient:
    def __init__(self, channel: FakeChannel | None = None) -> None:
        self.channel = channel or FakeChannel([])
        self.host_keys = FakeHostKeys()
        self.loaded_host_key_paths: list[str] = []
        self.saved_host_key_paths: list[str] = []
        self.connect_kwargs: dict[str, object] | None = None
        self.missing_host_key_policy: object | None = None
        self.closed = False

    def load_host_keys(self, path: str) -> None:
        self.loaded_host_key_paths.append(path)

    def set_missing_host_key_policy(self, policy: object) -> None:
        self.missing_host_key_policy = policy

    def get_host_keys(self) -> FakeHostKeys:
        return self.host_keys

    def save_host_keys(self, path: str) -> None:
        self.saved_host_key_paths.append(path)

    def connect(self, **kwargs: object) -> None:
        self.connect_kwargs = kwargs

    def get_transport(self) -> FakeTransport:
        return FakeTransport(self.channel)

    def close(self) -> None:
        self.closed = True


class SSHCommandRunnerTest(unittest.TestCase):
    def test_default_known_hosts_path_uses_user_home_not_shared_tmp(self) -> None:
        runner = SSHCommandRunner(private_key_path="/keys/customer.pem")

        self.assertEqual(runner.known_hosts_path, str(Path.home() / ".ssh" / "known_hosts"))

    def test_strict_host_key_policy_uses_configured_known_hosts_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "customer.pem"
            known_hosts_path = Path(temp_dir) / "known_hosts"
            key_path.write_text("fake-key")
            known_hosts_path.write_text("known-host")
            client = FakeClient()
            runner = SSHCommandRunner(
                private_key_path=str(key_path),
                known_hosts_path=str(known_hosts_path),
                host_key_policy="strict",
                client_factory=lambda: client,
                private_key_loader=lambda path: FakePKey(),
            )

            result = runner.execute(
                target=CommandTarget(host="10.0.0.5", port=2222, username="azureuser"),
                command="uptime",
                timeout_s=5,
                on_chunk=lambda _stream, _content: None,
            )

        self.assertFalse(result.timed_out)
        self.assertEqual(client.loaded_host_key_paths, [str(known_hosts_path)])
        self.assertIsInstance(client.missing_host_key_policy, paramiko.RejectPolicy)
        self.assertEqual(client.connect_kwargs["port"], 2222)

    def test_insecure_ignore_policy_is_explicit_and_uses_dev_null_by_default(self) -> None:
        runner = SSHCommandRunner(private_key_path="/keys/customer.pem", host_key_policy="insecure-ignore")

        self.assertEqual(runner.known_hosts_path, "/dev/null")

    def test_paramiko_runner_streams_stdout_and_stderr_with_non_interactive_private_key_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "customer.pem"
            known_hosts_path = Path(temp_dir) / "known_hosts"
            key_path.write_text("fake-key")
            channel = FakeChannel(
                [
                    ("stdout", b"active\n"),
                    ("stderr", b"warning\n"),
                    ("stdout", b"done\n"),
                ]
            )
            client = FakeClient(channel)
            private_key = FakePKey()
            loaded_key_paths: list[str] = []
            chunks: list[tuple[str, str]] = []
            runner = SSHCommandRunner(
                private_key_path=str(key_path),
                known_hosts_path=str(known_hosts_path),
                host_key_policy="accept-new",
                connect_timeout_s=7,
                client_factory=lambda: client,
                private_key_loader=lambda path: loaded_key_paths.append(path) or private_key,
            )

            result = runner.execute(
                target=CommandTarget(host="10.0.0.5", port=22, username="azureuser"),
                command="systemctl status nginx --no-pager",
                timeout_s=9,
                on_chunk=lambda stream, content: chunks.append((stream, content)),
            )

            host_key = FakeHostKey()
            client.missing_host_key_policy.missing_host_key(client, "10.0.0.5", host_key)

        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)
        self.assertEqual(chunks, [("stdout", "active\n"), ("stderr", "warning\n"), ("stdout", "done\n")])
        self.assertEqual(loaded_key_paths, [str(key_path)])
        self.assertEqual(channel.command, "systemctl status nginx --no-pager")
        self.assertEqual(client.connect_kwargs["hostname"], "10.0.0.5")
        self.assertEqual(client.connect_kwargs["username"], "azureuser")
        self.assertEqual(client.connect_kwargs["pkey"], private_key)
        self.assertEqual(client.connect_kwargs["allow_agent"], False)
        self.assertEqual(client.connect_kwargs["look_for_keys"], False)
        self.assertEqual(client.connect_kwargs["timeout"], 7)
        self.assertEqual(client.host_keys.added, [("10.0.0.5", "ssh-ed25519", host_key)])
        self.assertEqual(client.saved_host_key_paths, [str(known_hosts_path)])
        self.assertTrue(client.closed)

    def test_numbered_target_uses_matching_case_key_from_key_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            default_key_path = Path(temp_dir) / "case1_key.pem"
            selected_key_path = Path(temp_dir) / "case4_key.pem"
            default_key_path.write_text("default-key")
            selected_key_path.write_text("selected-key")
            client = FakeClient()
            loaded_key_paths: list[str] = []
            runner = SSHCommandRunner(
                private_key_path=str(default_key_path),
                private_key_dir=temp_dir,
                client_factory=lambda: client,
                private_key_loader=lambda path: loaded_key_paths.append(path) or FakePKey(),
            )

            result = runner.execute(
                target=CommandTarget(
                    host="10.0.0.4",
                    port=22,
                    username="azureuser",
                    key_number=4,
                ),
                command="uptime",
                timeout_s=5,
                on_chunk=lambda _stream, _content: None,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(loaded_key_paths, [str(selected_key_path)])

    def test_numbered_target_reports_missing_case_key_without_using_wrong_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            default_key_path = Path(temp_dir) / "case1_key.pem"
            default_key_path.write_text("default-key")
            runner = SSHCommandRunner(
                private_key_path=str(default_key_path),
                private_key_dir=temp_dir,
            )

            result = runner.execute(
                target=CommandTarget(
                    host="10.0.0.9",
                    port=22,
                    username="azureuser",
                    key_number=9,
                ),
                command="uptime",
                timeout_s=5,
                on_chunk=lambda _stream, _content: None,
            )

        self.assertIsNone(result.exit_code)
        self.assertIn("case9_key.pem", result.error)

    def test_invalid_private_key_is_reported_before_connecting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            key_path = Path(temp_dir) / "customer.pem"
            key_path.write_text("not-a-private-key")
            client = FakeClient()
            runner = SSHCommandRunner(
                private_key_path=str(key_path),
                client_factory=lambda: client,
                private_key_loader=lambda _path: (_ for _ in ()).throw(ValueError("invalid key")),
            )

            result = runner.execute(
                target=CommandTarget(host="10.0.0.5", port=22, username="azureuser"),
                command="uptime",
                timeout_s=5,
                on_chunk=lambda _stream, _content: None,
            )

        self.assertIsNone(result.exit_code)
        self.assertIn("SSH private key is not valid", result.error)
        self.assertIsNone(client.connect_kwargs)


if __name__ == "__main__":
    unittest.main()
