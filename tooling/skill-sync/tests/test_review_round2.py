"""Regression tests for the second review round, through the CLI with local fixtures."""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from support import BASE_FILES, LAUNCHER, SANDBOX, VERIFY_FLAGS, CliTest

DROP_BRAND = {"id": "brand/hosting", "file": "skills/demo/SKILL.md", "anchor": "Use BrandCo hosting.",
              "replacement": "Use any host.", "reason": "brand"}


def py(code: str, name: str = "probe", timeout: int = 60) -> dict:
    return {"name": name, "argv": ["{python}", "-c", code], "timeout": timeout}


class TcpReceipts:
    """A local listener: a process outside skill-sync's control reports here."""

    def __init__(self, test: unittest.TestCase):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.2)
        test.addCleanup(self.listener.close)
        self.port = self.listener.getsockname()[1]

    def first(self, wait: float) -> bool:
        """True as soon as one process reports, False after `wait` seconds of silence."""
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                conn, _ = self.listener.accept()
                conn.close()
                return True
            except socket.timeout:
                pass
        return False

    def count(self, wait: float) -> int:
        seen, deadline = 0, time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                conn, _ = self.listener.accept()
                conn.close()
                seen += 1
            except socket.timeout:
                pass
        return seen


class CredentialSockets(CliTest):
    """Round 2 #1: Unix sockets outside the stage (Docker, ssh-agent, keyrings) are unreachable."""

    @unittest.skipUnless(SANDBOX, "needs sandbox-exec (macOS) or a working bwrap (Linux)")
    def test_unix_socket_outside_the_stage_is_unreachable(self):
        # A short path: macOS limits AF_UNIX paths to 104 bytes.
        folder = tempfile.mkdtemp(prefix="ss-", dir="/tmp")
        self.addCleanup(lambda: os.path.exists(folder) and os.rmdir(folder))
        path = os.path.join(folder, "agent.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        self.addCleanup(server.close)
        server.listen(1)
        server.settimeout(0.2)
        accepted = []

        def serve():
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not accepted:
                try:
                    conn, _ = server.accept()
                    accepted.append(True)
                    conn.sendall(b"dummy-credential")
                    conn.close()
                except (socket.timeout, OSError):
                    pass

        threading.Thread(target=serve, daemon=True).start()
        code = (
            "import socket\n"
            "s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
            "try:\n"
            f"    s.connect({path!r})\n"
            "except (PermissionError, FileNotFoundError, ConnectionRefusedError):\n"
            "    pass  # sandbox-exec denies; bwrap doesn't mount it\n"
            "else:\n"
            "    raise SystemExit('reached a credential socket: %r' % s.recv(100))\n"
            # A socket inside the stage still works (tools use them for local IPC).
            "inner = socket.socket(socket.AF_UNIX); inner.bind('ipc.sock'); inner.listen(1)\n"
            "c = socket.socket(socket.AF_UNIX); c.connect('ipc.sock'); c.close(); inner.close()\n"
            # IP networking (pip) stays open: a localhost round trip.
            "tcp = socket.socket(); tcp.bind(('127.0.0.1', 0)); tcp.listen(1)\n"
            "socket.create_connection(tcp.getsockname()).close(); tcp.close()\n"
        )
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, [DROP_BRAND], verify=[py(code)]))
        self.assertExit(0, self.run_cli("sync", "demo", "--checks"))
        self.assertFalse(accepted, "the sandboxed command connected to the outside socket")


class SandboxExecutable(CliTest):
    """Round 2 #2: a file planted in the stage can't stand in for the sandbox or any tool."""

    @unittest.skipUnless(SANDBOX, "needs sandbox-exec (macOS) or a working bwrap (Linux)")
    def test_relative_path_entries_cant_replace_the_sandbox(self):
        receipt = self.tmp / "fake-sandbox-ran"
        fake = f"#!/bin/sh\necho ran > {receipt}\nexec \"$@\"\n"
        plant = ("import pathlib, os\n"
                 f"for name in ('sandbox-exec', 'bwrap', 'git'):\n"
                 f"    p = pathlib.Path(name); p.write_text({fake!r}); p.chmod(0o755)\n")
        check_path = ("import os\n"
                      "entries = os.environ['PATH'].split(os.pathsep)\n"
                      "assert all(os.path.isabs(e) for e in entries), entries\n")
        self.env["PATH"] = os.pathsep.join([".", "", "bin", self.env["PATH"]])
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, [DROP_BRAND], verify=[py(plant, "plant"), py(check_path, "path")]))
        self.assertExit(0, self.run_cli("sync", "demo", "--checks"))
        self.assertFalse(receipt.exists(), "a planted executable ran")


class DetachedDescendants(CliTest):
    """Round 2 #4: children that start their own session die with the verify command."""

    def setUp(self):
        super().setUp()
        self.receipts = TcpReceipts(self)

    def _detached(self, delay: float, scrub_env: bool = True) -> str:
        child = (f"import time, socket; time.sleep({delay}); "
                 f"socket.create_connection(('127.0.0.1', {self.receipts.port})).close()")
        env = ", env={}" if scrub_env else ""
        return ("import subprocess, sys; "
                f"subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True{env})")

    def _fork(self, code: str, timeout: int = 60):
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, [DROP_BRAND], verify=[py(code, timeout=timeout)]))

    def test_control_detached_child_reports_when_not_killed(self):
        self._fork(self._detached(0) + "; import time; time.sleep(2)")
        self.assertExit(0, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS))
        self.assertEqual(self.receipts.count(1), 1)

    @unittest.skipUnless(SANDBOX, "an orphan with a scrubbed environment is only traceable inside a sandbox")
    def test_detached_child_dies_after_a_normal_exit(self):
        self._fork(self._detached(2))
        self.assertExit(0, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS))
        self.assertEqual(self.receipts.count(4), 0, "a detached child (scrubbed env, own session) survived")

    @unittest.skipUnless(SANDBOX or sys.platform.startswith("linux"),
                         "unsandboxed macOS can't see other processes' environments")
    def test_detached_child_with_inherited_env_dies_after_a_normal_exit(self):
        self._fork(self._detached(2, scrub_env=False))
        self.assertExit(0, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS))
        self.assertEqual(self.receipts.count(4), 0, "a detached child survived")

    def test_detached_child_dies_on_timeout(self):
        self._fork(self._detached(3) + "; import time; time.sleep(60)", timeout=1)
        self.assertIn("timed out", self.assertExit(1, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS)))
        self.assertEqual(self.receipts.count(5), 0)

    def test_detached_child_dies_on_sigterm(self):
        ready = TcpReceipts(self)
        self._fork(self._detached(3) + f"; import socket, time; socket.create_connection(('127.0.0.1', {ready.port})).close()"
                   "; time.sleep(60)")
        cli = subprocess.Popen([sys.executable, "-B", str(LAUNCHER), "--root", str(self.root), "--cache", str(self.cache),
                                "sync", "demo", "--checks", *VERIFY_FLAGS], env=self.env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertTrue(ready.first(20), "the verify command never started")
        cli.send_signal(signal.SIGTERM)
        cli.communicate(timeout=30)
        self.assertEqual(cli.returncode, 130)
        self.assertEqual(self.receipts.count(5), 0)

    @unittest.skipUnless(SANDBOX == "sandbox-exec", "macOS identifies orphans by their sandbox profile")
    def test_cleanup_leaves_unrelated_processes_alone(self):
        # Young, same-user, sandboxed and unsandboxed bystanders must survive the sweep.
        boxed = subprocess.Popen(["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/bin/sleep", "30"])
        plain = subprocess.Popen(["/bin/sleep", "30"])
        self.addCleanup(lambda: [p.kill() for p in (boxed, plain)])
        self._fork(self._detached(2))
        self.assertExit(0, self.run_cli("sync", "demo", "--checks"))
        self.assertIsNone(boxed.poll(), "killed an unrelated sandboxed process")
        self.assertIsNone(plain.poll(), "killed an unrelated process")


class StalePublication(CliTest):
    """Round 2 #5: a build never publishes after its fork definition changed underneath it."""

    def test_sync_built_before_a_bump_publishes_nothing(self):
        gate = TcpReceipts(self)
        hold = ("from pathlib import Path\nimport socket\nif not Path('skills/demo/new.md').exists():\n"
                f"    s = socket.create_connection(('127.0.0.1', {gate.port})); s.recv(1); s.close()\n")
        pin = self.upstream.commit(BASE_FILES)
        manifest = self.write_fork(self.fork(pin, verify=[py(hold, "gate")]))
        self.assertExit(0, self.run_cli("sync", "demo"))
        candidate = self.upstream.commit({"skills/demo/new.md": "new\n"})
        slow = subprocess.Popen([sys.executable, "-B", str(LAUNCHER), "--root", str(self.root), "--cache", str(self.cache),
                                 "sync", "demo", "--checks", *VERIFY_FLAGS], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        gate.listener.settimeout(30)
        conn, _ = gate.listener.accept()  # the slow sync has built pin A and is verifying
        self.assertExit(0, self.run_cli("bump", "demo", "--to", candidate, *VERIFY_FLAGS))
        conn.sendall(b"x")
        conn.close()
        output, _ = slow.communicate(timeout=60)
        self.assertEqual(slow.returncode, 1, output)
        self.assertIn("changed while this run was building", output)
        self.assertEqual(json.loads(manifest.read_text())["upstream"]["sha"], candidate)
        self.assertTrue((self.root / "skills/demo/new.md").exists(), "stale output replaced the bump's")
        self.assertExit(0, self.run_cli("verify"))


class Bytecode(CliTest):
    """Low: running the launcher never writes __pycache__ into the repository."""

    def test_launcher_writes_no_bytecode(self):
        package = LAUNCHER.parent / "skill_sync"
        env = {k: v for k, v in self.env.items() if k != "PYTHONDONTWRITEBYTECODE"}
        before = (package / "__pycache__").exists()
        result = subprocess.run([sys.executable, str(LAUNCHER), "--root", str(self.root), "list"], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        if not before:
            self.assertFalse((package / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
