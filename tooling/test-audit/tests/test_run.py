"""run: one file at a time, single worker, pass / fail / error, --via, --dry-run."""

import json
import os
import unittest

from ta_helpers import PY, RUNNER, FixtureCase, git, has_module, runner_config, write

CALC = "def add(a, b):\n    return a + b\n"


def calc_repo_files(**tests):
    files = {"src/calc.py": CALC, "runner.py": RUNNER, ".test-audit.json": json.dumps(runner_config())}
    files.update(tests)
    return files


class RunTest(FixtureCase):
    def test_reports_each_file_as_passed_failed_or_error(self):
        root = self.repo(calc_repo_files(**{
            "tests/test_ok.py": "from src.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
            "tests/test_bad.py": "from src.calc import add\n\ndef test_add_wrong():\n    assert add(2, 2) == 5\n",
            "tests/test_broken.py": "import does_not_exist\n",
        }))
        data = self.tool_json("run", "tests/test_ok.py", "tests/test_bad.py", "tests/test_broken.py", cwd=root, expect=1)
        res = {r["file"]: r for r in data["result"]["results"]}
        self.assertEqual({f: r["status"] for f, r in res.items()},
                         {"tests/test_ok.py": "passed", "tests/test_bad.py": "failed", "tests/test_broken.py": "error"})
        self.assertIn("FAIL test_add_wrong", "\n".join(res["tests/test_bad.py"]["output_tail"]))
        self.assertTrue(all(r["duration_s"] >= 0 for r in res.values()))
        self.assertEqual(data["result"]["summary"]["total"], 3)
        only_error = self.tool_json("run", "tests/test_broken.py", cwd=root, expect=2)
        self.assertEqual(only_error["result"]["results"][0]["status"], "error")

    def test_non_test_file_is_a_usage_error_and_nothing_runs(self):
        root = self.repo(calc_repo_files(**{"tests/test_ok.py": "def test_x():\n    open('ran', 'w').close()\n"}))
        code, _, err = self.tool("run", "tests/test_ok.py", "src/calc.py", cwd=root)
        self.assertEqual(code, 2)
        self.assertIn("not a test file", err)
        self.assertFalse(os.path.exists(os.path.join(root, "ran")))

    def test_via_prefix_carries_the_command_with_quoting_intact(self):
        root = self.repo(calc_repo_files(**{
            "tests/test_with space.py": "import os\n\ndef test_env():\n    print('VIA=' + os.environ.get('VIA_MARK', ''))\n",
        }))
        appended = self.tool_json("--via", "env VIA_MARK=appended", "run", "tests/test_with space.py", cwd=root, expect=0)
        self.assertIn("VIA=appended", "\n".join(appended["result"]["results"][0]["output_tail"]))
        wrapped = self.tool_json("--via", "env VIA_MARK=wrapped sh -c {cmd}", "run", "tests/test_with space.py", cwd=root, expect=0)
        self.assertIn("VIA=wrapped", "\n".join(wrapped["result"]["results"][0]["output_tail"]))

    def test_dry_run_prints_commands_without_running(self):
        root = self.repo(calc_repo_files(**{"tests/test_ok.py": "def test_x():\n    open('ran', 'w').close()\n"}))
        code, out, _ = self.tool("--dry-run", "run", "tests/test_ok.py", cwd=root)
        self.assertEqual(code, 0)
        self.assertIn("runner.py tests/test_ok.py", out)
        self.assertFalse(os.path.exists(os.path.join(root, "ran")))


# Output recorded from vitest 5.1.0 (pass, assertion failure, failed import).
VITEST_OUT = {
    "ok": " Test Files  1 passed (1)\n      Tests  1 passed (1)\n",
    "bad": " FAIL  src/bad.test.ts > f\nAssertionError: expected 1 to be 2\n Test Files  1 failed (1)\n      Tests  1 failed (1)\n",
    "broken": "Error: Cannot find module './nope' imported from src/broken.test.ts\n Test Files  1 failed (1)\n      Tests  no tests\n",
}
FAKE_VITEST = r"""#!/bin/sh
d=$(dirname "$0")
case " $* " in *" --help "*) cat "$d/help.txt"; exit 0;; esac
for a in "$@"; do case "$a" in
  *ok.test.ts) cat "$d/ok.out"; exit 0;;
  *bad.test.ts) cat "$d/bad.out"; exit 1;;
  *broken.test.ts) cat "$d/broken.out"; exit 1;;
esac; done
echo "No test files found, exiting with code 1"; exit 1
"""


class InstalledRunnerTest(FixtureCase):
    def _vitest_repo(self, help_text):
        root = self.repo({
            "package.json": json.dumps({"scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "*"}}),
            "src/ok.test.ts": "", "src/bad.test.ts": "", "src/broken.test.ts": "", ".gitignore": "node_modules\n",
        })
        files = {"node_modules/.bin/vitest": FAKE_VITEST, "node_modules/.bin/help.txt": help_text}
        files.update({"node_modules/.bin/%s.out" % k: v for k, v in VITEST_OUT.items()})
        write(root, files)
        os.chmod(os.path.join(root, "node_modules/.bin/vitest"), 0o755)
        return root

    def test_vitest_summary_separates_failures_from_load_errors(self):
        root = self._vitest_repo("  --maxWorkers <workers>  Maximum number of workers\n")
        data = self.tool_json("run", "src/ok.test.ts", "src/bad.test.ts", "src/broken.test.ts", cwd=root, expect=1)
        self.assertEqual([r["status"] for r in data["result"]["results"]], ["passed", "failed", "error"])
        self.assertIn("--maxWorkers=1", data["result"]["results"][0]["command"])

    def test_single_worker_flag_follows_the_installed_help(self):
        root = self._vitest_repo("  --fileParallelism  Use --no-file-parallelism to disable\n")
        cmd = self.tool_json("--dry-run", "run", "src/ok.test.ts", cwd=root, expect=0)["result"]["results"][0]["command"]
        self.assertIn("--no-file-parallelism", cmd)
        self.assertNotIn("--maxWorkers=1", cmd)


@unittest.skipUnless(has_module("pytest"), "pytest not importable by %s" % PY)
class PytestTest(FixtureCase):
    def _repo(self, extra_toml=""):
        cfg = {"roots": {".": {"python": PY}}}
        return self.repo({
            "pyproject.toml": '[project]\nname = "calc"\nversion = "0"\n\n[tool.pytest.ini_options]\npythonpath = ["src"]\n' + extra_toml,
            "src/calc.py": CALC,
            "tests/test_ok.py": "from calc import add\n\ndef test_add():\n    assert add(1, 1) == 2\n",
            "tests/test_bad.py": "from calc import add\n\ndef test_add():\n    assert add(1, 1) == 3\n",
            "tests/test_broken.py": "import does_not_exist\n\ndef test_x():\n    pass\n",
            "tests/test_empty.py": "X = 1\n",
            ".test-audit.json": json.dumps(cfg),
        })

    def test_pytest_exit_codes_map_to_statuses_and_leave_no_caches(self):
        root = self._repo()
        data = self.tool_json("run", "tests/test_ok.py", "tests/test_bad.py", "tests/test_broken.py", "tests/test_empty.py", cwd=root, expect=1)
        res = data["result"]["results"]
        self.assertEqual([r["status"] for r in res], ["passed", "failed", "error", "error"])
        self.assertEqual(res[3]["reason"], "no tests collected")
        self.assertEqual(git(root, "status", "--porcelain", "--ignored"), "")

    @unittest.skipUnless(has_module("xdist"), "pytest-xdist not installed")
    def test_xdist_addopts_are_overridden_to_one_process(self):
        root = self._repo('addopts = "-n 2"\n')
        r = self.tool_json("run", "tests/test_ok.py", cwd=root, expect=0)["result"]["results"][0]
        self.assertEqual(r["command"][-2:], ["-n", "0"])
