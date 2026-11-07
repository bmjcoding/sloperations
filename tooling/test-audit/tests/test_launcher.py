"""Launcher contract: interpreter resolution and isolation from the cwd."""

import json
import os
import subprocess

from ta_helpers import PY, TOOL, FixtureCase, hermetic_git_env


class LauncherTest(FixtureCase):
    def _run(self, args, cwd, path, extra_env=None):
        env = {k: v for k, v in hermetic_git_env().items() if k not in ("TEST_AUDIT_PYTHON", "VIRTUAL_ENV")}
        env["PATH"] = path
        env.update(extra_env or {})
        p = subprocess.run([TOOL] + args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        return p.returncode, p.stdout.decode(), p.stderr.decode()

    def test_falls_back_to_python_when_python3_is_unusable(self):
        marker = os.path.join(self.tmpdir("m"), "calls")
        shims = self.shims(["python3"], marker)  # a python3 that cannot run
        good = self.tmpdir("goodpy")
        os.symlink(PY, os.path.join(good, "python"))
        code, out, err = self._run(["--version"], self._tmp, self.safe_path([shims, good]))
        self.assertEqual(code, 0, err)
        self.assertIn("test-audit", out)
        self.assertTrue(os.path.exists(marker), "python3 should have been tried first")

    def test_reports_what_to_install_when_no_python_exists(self):
        code, out, err = self._run(["detect"], self._tmp, self.safe_path())
        self.assertEqual(code, 2)
        self.assertIn("Python >= 3.9", err)
        self.assertIn("uv", err)

    def test_rejects_a_broken_explicit_interpreter(self):
        code, _, err = self._run(["--version"], self._tmp, self.safe_path(), {"TEST_AUDIT_PYTHON": "/nonexistent/python"})
        self.assertEqual(code, 2)
        self.assertIn("TEST_AUDIT_PYTHON", err)

    def test_modules_in_the_target_repo_cannot_shadow_the_tool(self):
        boom = "raise SystemExit('shadowed module imported')\n"
        root = self.repo({
            "json.py": boom, "argparse.py": boom, "subprocess.py": boom,
            "test_audit/__init__.py": boom, "test_audit/cli.py": boom,
            "pkg/a.test.ts": "it('x', () => {})\n", "package.json": "{}\n",
        })
        code, out, err = self._run(["--json", "detect"], root, os.environ["PATH"],
                                   {"TEST_AUDIT_PYTHON": PY, "PYTHONPATH": root, "PYTHONSTARTUP": os.path.join(root, "json.py")})
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["result"]["ecosystems"], ["js"])
        self.assertFalse(os.path.exists(os.path.join(root, "__pycache__")))
