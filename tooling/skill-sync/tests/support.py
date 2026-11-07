"""Fixtures for skill-sync's CLI tests: a local git repository plays upstream."""
from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Dict, Optional

LAUNCHER = Path(__file__).resolve().parents[1] / "skill-sync"


def _sandbox_backend() -> Optional[str]:
    """The OS sandbox these tests exercise: sandbox-exec or a working bwrap. Containers are never used here."""
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "sandbox-exec"
    bwrap = shutil.which("bwrap")
    if sys.platform.startswith("linux") and bwrap:
        probe = subprocess.run([bwrap, "--ro-bind", "/", "/", "--unshare-all", "true"], capture_output=True)
        if probe.returncode == 0:
            return "bwrap"
    return None


# SKILL_SYNC_TEST_SANDBOX=none runs the suite as if no sandbox existed (the CI path).
SANDBOX = None if os.environ.get("SKILL_SYNC_TEST_SANDBOX") == "none" else _sandbox_backend()
# Without an OS sandbox, tests that only need verify commands to run opt out explicitly.
VERIFY_FLAGS = [] if SANDBOX else ["--unsandboxed-verify"]


class FakeGitHub:
    """Serves GitHub's commit-verification API for the SHAs in `verified` (sha -> bool)."""

    def __init__(self):
        self.verified: Dict[str, bool] = {}
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append(self.path)
                match = re.match(r"^/repos/[^/]+/[^/]+/commits/([0-9a-f]{40})$", self.path)
                if not match or match.group(1) not in outer.verified:
                    self.send_response(404)
                    self.end_headers()
                    return
                ok = outer.verified[match.group(1)]
                body = json.dumps({"commit": {"verification": {"verified": ok, "reason": "valid" if ok else "unsigned"}}})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def isolated_git_env(home: Path) -> Dict[str, str]:
    """Git with a throwaway identity and no user/system config: fixtures never sign or use a real identity."""
    config = home / "gitconfig"
    if not config.exists():
        config.write_text("[user]\n\tname = skill-sync fixture\n\temail = fixture@example.invalid\n"
                          "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "SKILL_SYNC_"))}
    env.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1", HOME=str(home),
               SKILL_SYNC_SANDBOX=SANDBOX or "none")
    return env


class Upstream:
    def __init__(self, path: Path, env: Dict[str, str]):
        self.path = path
        self.env = env
        path.mkdir(parents=True)
        self.git("init", "-q")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.path, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    @property
    def url(self) -> str:
        return self.path.as_uri()

    def commit(self, files: Dict[str, Optional[str]], message: str = "change") -> str:
        for name, content in files.items():
            target = self.path / name
            if content is None:
                self.git("rm", "-q", name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")


class CliTest(unittest.TestCase):
    """Each test gets an upstream repo, a downstream repo root and a cache outside both."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="skill-sync-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.env = isolated_git_env(self.home)
        self.upstream = Upstream(self.tmp / "upstream", self.env)
        self.root = self.tmp / "downstream"
        (self.root / "upstream").mkdir(parents=True)
        self.cache = self.tmp / "cache"

    def run_cli(self, *args: str):
        result = subprocess.run([sys.executable, str(LAUNCHER), "--root", str(self.root), "--cache", str(self.cache), *args],
                                env=self.env, capture_output=True, text=True, timeout=300)
        return result.returncode, result.stdout + result.stderr

    def write_fork(self, data: dict) -> Path:
        path = self.root / "upstream" / f"{data['name']}.json"
        path.write_text(json.dumps(data, indent=2) + "\n")
        return path

    def github(self, slug: str = "fixture/upstream") -> "FakeGitHub":
        """Makes https://github.com/<slug>.git resolve to the fixture repo and serves a fake API."""
        with (self.home / "gitconfig").open("a") as config:
            config.write(f'\n[url "{self.upstream.url}"]\n\tinsteadOf = https://github.com/{slug}.git\n')
        fake = FakeGitHub()
        self.addCleanup(fake.close)
        self.env["SKILL_SYNC_GITHUB_API"] = fake.url
        return fake

    def fork(self, sha: str, rules=None, **extra) -> dict:
        data = {
            "name": "demo",
            "mode": "rules",
            "upstream": {"repo": self.upstream.url, "branch": "main", "sha": sha},
            "paths": [{"upstream": "skills/demo", "output": "skills/demo"}],
            "license": "LICENSE",
            "rules": rules or [],
        }
        data.update(extra)
        return data

    def out(self, name: str = "SKILL.md") -> str:
        return (self.root / "skills" / "demo" / name).read_text()

    def tree(self, base: Optional[Path] = None) -> Dict[str, bytes]:
        base = base or self.root / "skills"
        return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}

    def assertExit(self, expected: int, result):
        code, output = result
        self.assertEqual(code, expected, f"exit {code}, expected {expected}; output:\n{output}")
        return output


BASE_FILES = {
    "LICENSE": "MIT License\n\nCopyright (c) BrandCo\n",
    "skills/demo/SKILL.md": "# Demo\n\nUse BrandCo hosting.\n\n## Hosted\n\nAsk the bot.\n\n## Run\n\nrun it\nrun it\n",
    "skills/demo/references/more.md": "See [the skill](../SKILL.md#run).\n",
    "other/unrelated.txt": "not tracked\n",
}
