"""Fixture repos and CLI invocation for the test-audit suite.

Every test drives the real launcher (skills/test-audit/scripts/test-audit)
against a throwaway git repository created in a temp dir outside this repo.
The tool runs under the same interpreter as the suite (TEST_AUDIT_PYTHON), so
running the suite with /usr/bin/python3 and python3.14 covers both ends.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
TOOL = os.path.join(REPO_ROOT, "skills", "test-audit", "scripts", "test-audit")
PY = sys.executable

# A tiny but real test runner used by fixtures through the testCommand
# override: imports the test file, calls every test_* function, exits
# 0 (all passed), 1 (an assertion failed), 2 (the file could not load).
RUNNER = r'''
import os, runpy, sys, traceback
sys.path.insert(0, os.getcwd())
try:
    ns = runpy.run_path(sys.argv[1])
except Exception:
    traceback.print_exc()
    sys.exit(2)
failed = 0
for name in sorted(ns):
    if name.startswith("test_") and callable(ns[name]):
        try:
            ns[name]()
            print("ok", name)
        except AssertionError:
            failed += 1
            print("FAIL", name)
sys.exit(1 if failed else 0)
'''


def hermetic_git_env(base=None):
    """Isolate every git process from the host: no global/system config, a
    fixed identity, no signing, no hooks. Overrides whatever the caller set."""
    env = dict(os.environ if base is None else base)
    for k in list(env):
        if k.startswith("GIT_") and k not in ("GIT_EXEC_PATH",):
            del env[k]
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_CONFIG_COUNT": "4",
        "GIT_CONFIG_KEY_0": "commit.gpgsign", "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "tag.gpgsign", "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "core.hooksPath", "GIT_CONFIG_VALUE_2": os.devnull,
        "GIT_CONFIG_KEY_3": "core.fsmonitor", "GIT_CONFIG_VALUE_3": "false",
    })
    return env


GIT_ENV = hermetic_git_env()
# Every process the suite starts (git, the CLI, keepers, package managers)
# inherits the isolated git environment, including ones that bypass git().
os.environ.clear()
os.environ.update(GIT_ENV)


def git(cwd, *args):
    p = subprocess.run(["git"] + list(args), cwd=cwd, env=GIT_ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise AssertionError("git %s failed: %s" % (" ".join(args), p.stderr.decode()))
    return p.stdout.decode()


def write(root, files):
    for rel, content in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(path, mode) as f:
            f.write(content)


def runner_config(**extra):
    cfg = {"testCommand": "%s runner.py {file}" % shlex.quote(PY), "testErrorExitCodes": [2]}
    cfg.update(extra)
    return cfg


class FixtureCase(unittest.TestCase):
    """Base class: self.repo(...) builds a committed fixture repository."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="test-audit-suite-")
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def tmpdir(self, name):
        path = os.path.join(self._tmp, name)
        os.makedirs(path, exist_ok=True)
        return path

    def repo(self, files, name="repo", commit=True, branch="main"):
        root = os.path.realpath(self.tmpdir(name))
        git(root, "init", "-q")
        git(root, "symbolic-ref", "HEAD", "refs/heads/" + branch)
        write(root, files)
        if commit:
            git(root, "add", "-A")
            git(root, "commit", "-q", "-m", "fixture")
        return root

    def tool(self, *args, cwd, env=None, timeout=300):
        e = hermetic_git_env()
        e["TEST_AUDIT_PYTHON"] = PY
        e.pop("VIRTUAL_ENV", None)
        if env:
            e.update(env)
        p = subprocess.run([TOOL] + list(args), cwd=cwd, env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")

    def tool_json(self, *args, cwd, env=None, expect=None, timeout=300):
        code, out, err = self.tool("--json", *args, cwd=cwd, env=env, timeout=timeout)
        try:
            data = json.loads(out)
        except ValueError:
            raise AssertionError("not JSON (exit %s):\n%s\nstderr:\n%s" % (code, out, err)) from None
        self.assertEqual(data["exit_code"], code)
        if expect is not None:
            self.assertEqual(code, expect, "stdout:\n%s\nstderr:\n%s" % (out, err))
        return data

    def safe_path(self, extra_dirs=()):
        """A PATH holding only basic tools (no Python, Node, or uv)."""
        d = self.tmpdir("safebin")
        for tool in ("git", "sh", "env", "dirname", "readlink", "cat", "sleep", "rm", "mkdir", "ls", "grep", "sed", "tr", "uname", "basename", "expr", "head", "tail"):
            w = shutil.which(tool)
            dst = os.path.join(d, tool)
            if w and not os.path.exists(dst):
                os.symlink(w, dst)
        return os.pathsep.join(list(extra_dirs) + [d])

    def shims(self, names, marker):
        """Executables that record their own name in `marker` and exit 127."""
        d = self.tmpdir("shims-%d" % len(os.listdir(self._tmp)))
        for n in names:
            p = os.path.join(d, n)
            with open(p, "w") as f:
                f.write('#!/bin/sh\necho "%s $*" >> %s\nexit 127\n' % (n, shlex.quote(marker)))
            os.chmod(p, 0o755)
        return d


def has_module(name):
    p = subprocess.run([PY, "-c", "import " + name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p.returncode == 0
