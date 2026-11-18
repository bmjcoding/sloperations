"""Regressions from the CLI and docs reviews: owning-root execution, restore
safety, process cleanup, history and config strictness, change discovery,
pytest config discovery, bytecode freshness, LOC totals."""

import json
import os
import shlex
import signal
import stat
import subprocess
import time

from ta_helpers import PY, RUNNER, TOOL, FixtureCase, git, hermetic_git_env, runner_config, write

CALC = "def add(a, b):\n    return a + b\n"
TEST_ADD = "from src.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def calc_repo(case, **extra):
    files = {"src/calc.py": CALC, "runner.py": RUNNER, "tests/test_add.py": TEST_ADD,
             ".test-audit.json": json.dumps(runner_config())}
    files.update(extra)
    return case.repo(files)


def keeper_cmd(script_path):
    return " ".join(shlex.quote(x) for x in (PY, script_path))


class OwningRootTest(FixtureCase):
    def test_commands_run_in_the_owning_package_root(self):
        root = self.repo({
            "package.json": json.dumps({"private": True, "workspaces": ["packages/*"]}),
            "packages/pkg/package.json": json.dumps({"name": "x", "scripts": {"test": "bun test"}}),
            "packages/pkg/a.test.ts": "test('a', () => {});\n",
            ".test-audit.json": json.dumps({"testCommand": "sh -c 'pwd > ran_in.txt' {file}"}),
        })
        res = self.tool_json("run", "packages/pkg/a.test.ts", cwd=root, expect=0)["result"]["results"][0]
        self.assertEqual(res["root"], "packages/pkg")
        with open(os.path.join(root, "packages/pkg/ran_in.txt")) as f:
            self.assertEqual(os.path.realpath(f.read().strip()), os.path.join(root, "packages", "pkg"))


class RestoreTest(FixtureCase):
    def _keeper(self, body):
        path = os.path.join(self._tmp, "keeper.py")
        write(self._tmp, {"keeper.py": body})
        return keeper_cmd(path)

    def _mutate(self, root, test_cmd, *extra, expect=None):
        return self.tool_json("mutate", "--file", "src/calc.py", "--replace", "a + b", "--with", "a * b",
                              "--test", "tests/test_add.py", "--test-cmd", test_cmd, *extra, cwd=root, expect=expect)

    def test_keeper_that_deletes_the_target_cannot_lose_it(self):
        root = calc_repo(self)
        path = os.path.join(root, "src/calc.py")
        os.chmod(path, 0o640)
        cmd = self._keeper("import os\nif '*' in open('src/calc.py').read():\n    os.unlink('src/calc.py')\n")
        self._mutate(root, cmd, expect=1)
        with open(path) as f:
            self.assertEqual(f.read(), CALC)
        self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o640)

    def test_keeper_that_swaps_in_a_symlink_cannot_redirect_the_restore(self):
        root = calc_repo(self)
        outside = os.path.join(self._tmp, "outside.py")
        write(self._tmp, {"outside.py": "DO NOT OVERWRITE\n"})
        cmd = self._keeper("import os\nif '*' in open('src/calc.py').read():\n    os.unlink('src/calc.py')\n"
                           "    os.symlink(%r, 'src/calc.py')\n" % outside)
        self._mutate(root, cmd, expect=1)
        with open(outside) as f:
            self.assertEqual(f.read(), "DO NOT OVERWRITE\n")
        path = os.path.join(root, "src/calc.py")
        self.assertFalse(os.path.islink(path))
        with open(path) as f:
            self.assertEqual(f.read(), CALC)

    def test_a_symlink_file_argument_is_refused_before_any_keeper_runs(self):
        root = calc_repo(self)
        os.symlink("calc.py", os.path.join(root, "src/alias.py"))
        marker = os.path.join(self._tmp, "ran")
        cmd = self._keeper("open(%r, 'w').close()\n" % marker)
        code, _, err = self.tool("mutate", "--file", "src/alias.py", "--replace", "a + b", "--with", "a * b",
                                 "--test", "tests/test_add.py", "--test-cmd", cmd, cwd=root)
        self.assertEqual(code, 2)
        self.assertIn("symlink", err)
        self.assertFalse(os.path.exists(marker))

    def test_signal_killed_keeper_is_an_error_not_a_catch(self):
        root = calc_repo(self)
        cmd = self._keeper("import os, signal\nif '*' in open('src/calc.py').read():\n    os.kill(os.getpid(), signal.SIGKILL)\n")
        res = self._mutate(root, cmd, expect=2)["result"]
        self.assertEqual(res["verdict"], "ERROR")
        self.assertIn("signal 9", res["results"][0]["reason"])

    def test_leftover_background_descendant_is_killed_before_restore(self):
        root = calc_repo(self)
        child = os.path.join(self._tmp, "child.py")
        write(self._tmp, {"child.py": "import time\ntime.sleep(1.5)\nopen('src/calc.py', 'w').write('CLOBBERED\\n')\n"})
        cmd = self._keeper("import subprocess, sys\nsubprocess.Popen([sys.executable, %r], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n" % child)
        self._mutate(root, cmd, "--skip-precheck", expect=1)
        time.sleep(2.5)
        with open(os.path.join(root, "src/calc.py")) as f:
            self.assertEqual(f.read(), CALC)

    def test_sigterm_reaches_descendants_that_ignore_it(self):
        root = calc_repo(self)
        ready = os.path.join(self._tmp, "ready")
        child = os.path.join(self._tmp, "child.py")
        write(self._tmp, {"child.py": "import os, signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                      "open(%r, 'w').write(str(os.getpid()))\ntime.sleep(5)\n"
                                      "open('src/calc.py', 'w').write('LATE CLOBBER\\n')\n" % ready})
        cmd = self._keeper("import subprocess, sys, time\nsubprocess.Popen([sys.executable, %r])\ntime.sleep(60)\n" % child)
        proc = subprocess.Popen([TOOL, "mutate", "--file", "src/calc.py", "--replace", "a + b", "--with", "a * b",
                                 "--test", "tests/test_add.py", "--test-cmd", cmd, "--skip-precheck"], cwd=root,
                                env=dict(hermetic_git_env(), TEST_AUDIT_PYTHON=PY), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while not (os.path.exists(ready) and open(ready).read()):
            self.assertLess(time.time(), deadline, "descendant never started")
            time.sleep(0.05)
        pid = int(open(ready).read())
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=30), 143)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        time.sleep(0.5)
        with open(os.path.join(root, "src/calc.py")) as f:
            self.assertEqual(f.read(), CALC)


class HistoryAndConfigTest(FixtureCase):
    def test_missing_merge_base_is_an_error_not_a_direct_diff(self):
        src = self.repo({"a.py": "A = 1\n", "b.py": "B = 1\n"}, name="src")
        git(src, "checkout", "-q", "-b", "feature")
        write(src, {"a.py": "A = 2\n"})
        git(src, "commit", "-qam", "feature")
        git(src, "checkout", "-q", "main")
        write(src, {"b.py": "B = 2\n"})
        git(src, "commit", "-qam", "main moves")
        dest = os.path.join(self._tmp, "shallow")
        git(self._tmp, "clone", "-q", "--depth", "1", "--branch", "feature", "file://" + src, dest)
        git(dest, "fetch", "-q", "--depth", "1", "origin", "main:refs/remotes/origin/main")
        for args in (["changed", "--base", "origin/main"], ["loc", "--base", "origin/main"]):
            err = self.tool_json(*args, cwd=dest, expect=2)["error"]
            self.assertIn("no merge-base", err["message"])
            self.assertIn("shallow", err["message"])

    def test_nested_config_errors_are_usage_errors(self):
        root = self.repo({"package.json": "{}", "a.test.ts": "test('a', () => {});\n"})
        for cfg, needle in (
            ({"classify": {"test": [42]}}, "classify.test[0]"),
            ({"testGlobs": [None]}, "testGlobs[0]"),
            ({"runners": [{"glob": "**", "testCommand": 42}]}, "runners[0].testCommand"),
            ({"runners": [{"glob": "**", "runner": "pytest", "notAKey": True}]}, "notAKey"),
            ({"roots": {".": {"testCommand": "sh -c 'unterminated"}}}, "not a valid command"),
            ({"checks": {"lint": ""}}, "checks[\"lint\"]"),
            ({"testErrorExitCodes": ["2"]}, "testErrorExitCodes[0]"),
            ({"deadcode": {"disable": ["eslint"]}}, "deadcode.disable[0]"),
            ({"deadcode": {"vultureMinConfidence": True}}, "vultureMinConfidence"),
        ):
            write(root, {".test-audit.json": json.dumps(cfg)})
            for args in (["detect"], ["run", "a.test.ts"]):
                code, out, err = self.tool("--json", *args, cwd=root)
                self.assertEqual(code, 2, (cfg, err))
                self.assertNotIn("Traceback", err)
                data = json.loads(out)
                self.assertEqual(data["error"]["kind"], "usage")
                self.assertIn(needle, data["error"]["message"])


class ChangedUnionTest(FixtureCase):
    def test_staged_edit_reverted_in_worktree_is_still_reported(self):
        root = calc_repo(self, **{"tests/test_calc.py": TEST_ADD})
        write(root, {"src/calc.py": "def add(a, b):\n    return a * b\n"})
        git(root, "add", "src/calc.py")
        write(root, {"src/calc.py": CALC})
        res = self.tool_json("changed", cwd=root, expect=0)["result"]
        self.assertEqual([(r["path"], r["sources"], r["net_change"]) for r in res["files"]],
                         [("src/calc.py", ["staged", "unstaged"], False)])
        self.assertEqual([t["file"] for t in res["plan"]["tests"]], ["tests/test_calc.py"])

    def test_deletions_resolve_from_every_source(self):
        root = calc_repo(self, **{"old.py": "X = 1\n", "staged_gone.py": "Y = 1\n"})
        git(root, "rm", "-q", "staged_gone.py")
        os.remove(os.path.join(root, "old.py"))
        res = self.tool_json("changed", cwd=root, expect=0)["result"]
        self.assertEqual([(d["path"], d["sources"]) for d in res["deleted"]],
                         [("old.py", ["unstaged"]), ("staged_gone.py", ["staged"])])


class PytestConfigTest(FixtureCase):
    CHECKS = "class CheckThing:\n    def check_method(self):\n        pass\n\n\ndef check_add():\n    assert 1 + 1 == 2\n\n\ndef test_not_collected():\n    pass\n"

    def test_active_ini_file_drives_classification_and_ledger(self):
        for name, text in (
            ("pytest.ini", "[pytest]\npython_files = check_*.py\npython_functions = check_*\npython_classes = Check*\n"),
            ("tox.ini", "[pytest]\npython_files = check_*.py\npython_functions = check_*\npython_classes = Check*\n"),
            ("setup.cfg", "[tool:pytest]\npython_files = check_*.py\npython_functions = check_*\npython_classes = Check*\n"),
        ):
            root = self.repo({name: text, "tests/check_math.py": self.CHECKS, "tests/test_other.py": "def test_x():\n    pass\n"}, name=name)
            run = self.tool_json("--dry-run", "run", "tests/check_math.py", cwd=root, expect=0)["result"]["results"][0]
            self.assertEqual(run["status"], "dry-run", name)
            self.assertEqual(self.tool("run", "tests/test_other.py", "--dry-run", cwd=root)[0], 2, name)
            rows = self.tool_json("ledger", cwd=root, expect=0)["result"]["declarations"]
            self.assertEqual([r["name"] for r in rows], ["CheckThing::check_method", "check_add"], name)

    def test_pytest_precedence_and_testpaths(self):
        # an empty pytest.ini still wins over setup.cfg in the same directory
        root = self.repo({"pytest.ini": "", "setup.cfg": "[tool:pytest]\npython_files = check_*.py\n",
                          "tests/check_a.py": "def test_a():\n    pass\n", "tests/test_b.py": "def test_b():\n    pass\n"})
        self.assertEqual(self.tool_json("ledger", cwd=root, expect=0)["result"]["files"], ["tests/test_b.py"])
        scoped = self.repo({"pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
                            "tests/test_in.py": "def test_in():\n    pass\n", "scripts/test_out.py": "def test_out():\n    pass\n"}, name="scoped")
        self.assertEqual(self.tool_json("ledger", cwd=scoped, expect=0)["result"]["files"], ["tests/test_in.py"])


class BytecodeTest(FixtureCase):
    def test_stale_pycache_with_same_size_and_mtime_is_never_read(self):
        root = calc_repo(self, **{"tests/test_value.py": "from src.value import VALUE\n\ndef test_value():\n    assert VALUE == 2\n",
                                  "src/value.py": "VALUE = 1\n"})
        path = os.path.join(root, "src/value.py")
        subprocess.run([PY, "-c", "import src.value"], cwd=root, check=True)  # primes src/__pycache__
        st = os.stat(path)
        with open(path, "w") as f:
            f.write("VALUE = 2\n")
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.tool_json("run", "tests/test_value.py", cwd=root, expect=0)


class LocTotalsTest(FixtureCase):
    def test_totals_count_tracked_and_untracked_files_in_scope(self):
        root = self.repo({"pkg/src/a.py": "a = 1\nb = 2\n", "pkg/tests/test_a.py": "def test_a():\n    pass\n",
                          "pkg/README.md": "hi\n", "pkg/uv.lock": "x\n", "other/b.py": "c = 3\n"})
        write(root, {"pkg/src/new.py": "d = 4\ne = 5\nf = 6\n", "pkg/logo.png": b"\x89PNG\x00\x00"})
        res = self.tool_json("loc", "--totals", "pkg", cwd=root, expect=0)["result"]
        by = res["by_class"]
        self.assertEqual(sorted(by), ["config", "docs", "generated", "other", "production", "support", "test"])
        self.assertEqual(by["production"], {"files": 2, "lines": 5})
        self.assertEqual(by["test"], {"files": 1, "lines": 2})
        self.assertEqual(by["docs"], {"files": 1, "lines": 1})
        self.assertEqual(by["generated"], {"files": 1, "lines": 1})
        self.assertEqual(by["other"], {"files": 1, "lines": 0})
        self.assertEqual(res["binary"], ["pkg/logo.png"])
        self.assertEqual(res["totals"], {"files": 6, "lines": 9})
        diff = self.tool_json("loc", "pkg", cwd=root, expect=0)["result"]
        self.assertEqual([f["path"] for f in diff["files"]], ["pkg/logo.png", "pkg/src/new.py"])

    def test_keeper_list_after_one_test_flag(self):
        root = calc_repo(self, **{"tests/test_more.py": "def test_more():\n    pass\n"})
        res = self.tool_json("--dry-run", "mutate", "--file", "src/calc.py", "--replace", "a + b", "--with", "a * b",
                             "--test", "tests/test_add.py", "tests/test_more.py", cwd=root, expect=0)["result"]
        self.assertEqual(res["tests"], ["tests/test_add.py", "tests/test_more.py"])


class HermeticGitTest(FixtureCase):
    def test_fixture_git_ignores_hostile_host_config(self):
        hostile = os.path.join(self._tmp, "host.gitconfig")
        hooks = self.tmpdir("hooks")
        write(hooks, {"pre-commit": "#!/bin/sh\nexit 1\n"})
        os.chmod(os.path.join(hooks, "pre-commit"), 0o755)
        write(self._tmp, {"host.gitconfig": "[commit]\n gpgSign = true\n[gpg]\n program = /nonexistent/gpg\n[core]\n hooksPath = %s\n" % hooks})
        env = hermetic_git_env(dict(os.environ, GIT_CONFIG_GLOBAL=hostile, GIT_DIR="/nonexistent"))
        root = os.path.realpath(self.tmpdir("h"))
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
            p = subprocess.run(["git"] + args, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p.returncode, 0, p.stderr.decode())
