"""Round-2 review regressions: placeholder rescanning, parent-dir swaps,
interrupt cleanup, shell-reported signal deaths, check environment errors,
total config/argument validation, Vite temp files, pytest config discovery,
vulture paths, hermetic suite git."""

import json
import os
import shlex
import signal
import subprocess
import sys
import time

from ta_helpers import HERE, PY, TOOL, FixtureCase, git, hermetic_git_env, write
from test_hardening import CALC, calc_repo, keeper_cmd


class PlaceholderTest(FixtureCase):
    def _workspace(self, pkg):
        return self.repo({
            "package.json": json.dumps({"private": True, "workspaces": ["packages/*"]}),
            pkg + "/package.json": json.dumps({"name": "x"}),
            pkg + "/a.test.ts": "test('a', () => {});\n",
            ".test-audit.json": json.dumps({"testCommand": "true {file}"}),
        })

    def _pwned(self, root):
        return [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f == "PWN"]

    def test_placeholders_inside_substituted_paths_stay_literal(self):
        for pkg in ("packages/x{cmd};touch PWN;#", "packages/y{root}{cmd} ;touch PWN;#", "packages/z{cmd}';touch PWN;#"):
            root = self._workspace(pkg)
            res = self.tool_json("--via", "sh -c 'printf %s {root} >/dev/null; {cmd}'", "run", pkg + "/a.test.ts",
                                 cwd=root, expect=0)["result"]["results"][0]
            self.assertEqual(res["status"], "passed", pkg)
            self.assertEqual(self._pwned(root), [], pkg)
            git(root, "clean", "-qfdx")

    def test_placeholders_inside_quotes_are_refused(self):
        pkg = 'packages/x";touch PWN;#'
        root = self._workspace(pkg)
        for via in ('sh -c \'printf %s "{root}"; {cmd}\'', "sh -c \"echo '{cmd}'\""):
            err = self.tool_json("--via", via, "run", pkg + "/a.test.ts", cwd=root, expect=2)["error"]
            self.assertEqual(err["kind"], "usage")
            self.assertRegex(err["message"], r"is inside (single|double) quotes")
        self.assertEqual(self._pwned(root), [])


class ParentSwapTest(FixtureCase):
    def test_restore_writes_nothing_through_a_swapped_parent_directory(self):
        root = self.repo({"src/nested/calc.py": CALC, "tests/test_add.py": "def test_add():\n    pass\n"})
        outside = self.tmpdir("outside")
        keeper = os.path.join(self._tmp, "keeper.py")
        write(self._tmp, {"keeper.py": "import shutil, os\nif '*' in open('src/nested/calc.py').read():\n"
                                       "    shutil.rmtree('src')\n    os.symlink(%r, 'src')\n" % outside})
        res = self.tool_json("mutate", "--file", "src/nested/calc.py", "--replace", "a + b", "--with", "a * b",
                             "--test", "tests/test_add.py", "--test-cmd", keeper_cmd(keeper), cwd=root, expect=2)["result"]
        self.assertEqual(res["verdict"], "ERROR")
        self.assertFalse(res["restored"])
        self.assertEqual(os.listdir(outside), [])
        with open(res["backup"]) as f:
            self.assertEqual(f.read(), CALC)
        self.assertIn(res["backup"], res["reason"])
        os.unlink(res["backup"])


class InterruptTest(FixtureCase):
    def test_sigint_tears_down_a_sigterm_ignoring_grandchild_and_restores(self):
        root = calc_repo(self)
        ready = os.path.join(self._tmp, "ready")
        child = os.path.join(self._tmp, "child.py")
        write(self._tmp, {"child.py": "import os, signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                      "open(%r, 'w').write(str(os.getpid()))\ntime.sleep(10)\n"
                                      "open('src/calc.py', 'w').write('CLOBBERED\\n')\n" % ready})
        keeper = os.path.join(self._tmp, "keeper.py")
        write(self._tmp, {"keeper.py": "import subprocess, sys, time\nsubprocess.Popen([sys.executable, %r], "
                                       "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\ntime.sleep(60)\n" % child})
        proc = subprocess.Popen([TOOL, "mutate", "--file", "src/calc.py", "--replace", "a + b", "--with", "a * b",
                                 "--test", "tests/test_add.py", "--test-cmd", keeper_cmd(keeper), "--skip-precheck"],
                                cwd=root, env=dict(hermetic_git_env(), TEST_AUDIT_PYTHON=PY),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while not (os.path.exists(ready) and open(ready).read()):
            self.assertLess(time.time(), deadline, "grandchild never started")
            time.sleep(0.05)
        pid = int(open(ready).read())
        start = time.monotonic()
        proc.send_signal(signal.SIGINT)
        self.assertEqual(proc.wait(timeout=15), 130)
        self.assertLess(time.monotonic() - start, 8)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        with open(os.path.join(root, "src/calc.py")) as f:
            self.assertEqual(f.read(), CALC)


class SignalExitTest(FixtureCase):
    def test_shell_reported_signal_death_of_a_keeper_is_an_error(self):
        root = calc_repo(self)
        keeper = os.path.join(self._tmp, "keeper.py")
        write(self._tmp, {"keeper.py": "import os, signal\nif '*' in open('src/calc.py').read():\n"
                                       "    os.kill(os.getpid(), signal.SIGKILL)\n"})
        cmd = "sh -c " + shlex.quote(keeper_cmd(keeper) + '; s=$?; exit "$s"')
        res = self.tool_json("mutate", "--file", "src/calc.py", "--replace", "a + b", "--with", "a * b",
                             "--test", "tests/test_add.py", "--test-cmd", cmd, cwd=root, expect=2)["result"]
        self.assertEqual(res["verdict"], "ERROR")
        self.assertIn("128+9", res["results"][0]["reason"])

    def test_killed_or_unreachable_checks_are_errors_not_failures(self):
        kill = " ".join(shlex.quote(x) for x in (PY, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"))
        root = self.repo({"src/a.py": "A = 1\n", ".test-audit.json": json.dumps({"checks": {"proof": kill}})})
        write(root, {"src/a.py": "A = 2\n"})
        check = self.tool_json("changed", "--run", cwd=root, expect=2)["result"]["results"]["checks"][0]
        self.assertEqual((check["status"], check["reason"]), ("error", "killed by signal 15"))
        write(root, {".test-audit.json": json.dumps({"checks": {"proof": "true {files}"}})})
        check = self.tool_json("--via", 'sh -c "exit 255" ignored', "changed", "--run", cwd=root, expect=2)["result"]["results"]["checks"][0]
        self.assertEqual(check["status"], "error")
        self.assertIn("transport", check["reason"])


class ValidationTest(FixtureCase):
    def test_every_malformed_config_or_argument_is_a_json_usage_error(self):
        root = self.repo({"package.json": "{}", "a.test.ts": "test('a', () => {});\n"})
        cases = [{"roots": None}, {"roots": 2}, {"roots": [{}]}, {"checks": None}, {"checks": 2},
                 {"testGlobs": ["[z-a]"]}, {"runners": [{"glob": "[z-a]", "runner": "bun"}]},
                 {"classify": {"test": ["[z-a]"]}}, {"roots": {".": {"$note": 1}}}]
        for cfg in cases:
            write(root, {".test-audit.json": json.dumps(cfg)})
            code, out, err = self.tool("--json", "run", "a.test.ts", "--dry-run", cwd=root)
            self.assertEqual(code, 2, cfg)
            self.assertNotIn("Traceback", err, cfg)
            self.assertEqual(json.loads(out)["error"]["kind"], "usage", cfg)
        write(root, {".test-audit.json": json.dumps({"$schema": "x", "$comment": ["anything"]})})
        self.tool_json("detect", cwd=root, expect=0)
        for args in (["--via", "'", "run", "a.test.ts", "--dry-run"],
                     ["mutate", "--file", "a.test.ts", "--replace", "a", "--with", "b", "--test", "a.test.ts", "--test-cmd", "'", "--dry-run"]):
            code, out, err = self.tool("--json", *args, cwd=root)
            self.assertEqual(code, 2, args)
            self.assertNotIn("Traceback", err)
            self.assertEqual(json.loads(out)["error"]["kind"], "usage")


FAKE_VITEST = r"""#!/bin/sh
case " $* " in *" --help "*) echo '  --maxWorkers <n>'; exit 0;; esac
mkdir -p node_modules/.vite-temp
ms=$(python3 -c 'import time; print(int(time.time()*1000))')
: > "node_modules/.vite-temp/vitest.config.ts.timestamp-$ms-0123abcd.mjs"
echo started > vitest-started
sleep 30
"""


class ViteTempTest(FixtureCase):
    def _repo(self, fake):
        root = self.repo({"package.json": json.dumps({"devDependencies": {"vitest": "*"}}), "a.test.ts": "",
                          "vitest.config.ts": "export default {}\n", ".gitignore": "node_modules\nvitest-started\n"})
        write(root, {"node_modules/.bin/vitest": fake})
        os.chmod(os.path.join(root, "node_modules/.bin/vitest"), 0o755)
        return root

    def _interrupt(self, root):
        proc = subprocess.Popen([TOOL, "run", "a.test.ts"], cwd=root, env=dict(hermetic_git_env(), TEST_AUDIT_PYTHON=PY),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 20
        while not os.path.exists(os.path.join(root, "vitest-started")):
            self.assertLess(time.time(), deadline)
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=20), 143)

    def test_files_in_the_shared_temp_dir_are_never_deleted(self):
        # ownership of a file in .vite-temp cannot be proven, so an interrupted
        # load's bundle is left behind (documented), and so is anyone else's
        root = self._repo(FAKE_VITEST)
        other = "node_modules/.vite-temp/vite.config.ts.timestamp-1000-ffff.mjs"
        write(root, {other: ""})
        self._interrupt(root)
        names = sorted(os.listdir(os.path.join(root, "node_modules/.vite-temp")))
        self.assertIn(os.path.basename(other), names)
        self.assertEqual(len(names), 2)

    def test_empty_temp_dir_created_by_the_run_is_removed(self):
        root = self._repo(FAKE_VITEST.replace(': > "node_modules/.vite-temp/', ': > "/dev/null"; : "'))
        self._interrupt(root)
        self.assertFalse(os.path.exists(os.path.join(root, "node_modules/.vite-temp")))


CUSTOM = json.dumps({"testCommand": "true {file}"})  # classification under test, not the runner


class PytestDiscoveryTest(FixtureCase):
    def test_testpaths_scope_discovery_but_not_explicit_files(self):
        root = self.repo({"pytest.ini": "[pytest]\ntestpaths = tests\n", "tests/test_a.py": "def test_a():\n    pass\n",
                          "other/test_b.py": "def test_b():\n    pass\n", ".test-audit.json": CUSTOM})
        self.tool_json("--dry-run", "run", "other/test_b.py", cwd=root, expect=0)
        self.assertEqual(self.tool_json("ledger", cwd=root, expect=0)["result"]["files"], ["tests/test_a.py"])
        self.assertEqual(self.tool_json("ledger", "other", cwd=root, expect=0)["result"]["files"], ["other/test_b.py"])

    def test_every_config_file_pytest_honors_drives_run_ledger_and_baseline(self):
        for name, text in ((".pytest.ini", "[pytest]\npython_files = check_*.py\n"),
                           ("pytest.toml", '[pytest]\npython_files = ["check_*.py"]\n'),
                           (".pytest.toml", '[pytest]\npython_files = ["check_*.py"]\n')):
            root = self.repo({name: text, "check_a.py": "def test_a():\n    pass\n", ".test-audit.json": CUSTOM}, name=name.strip("."))
            self.tool_json("--dry-run", "run", "check_a.py", cwd=root, expect=0)
            self.assertEqual(self.tool_json("ledger", cwd=root, expect=0)["result"]["counts"]["declarations"], 1, name)
            base = self.tool_json("--dry-run", "baseline", cwd=root, expect=0)["result"]
            self.assertEqual([f["file"] for f in base["files"]], ["check_a.py"], name)

    def test_empty_pattern_lists_collect_nothing(self):
        root = self.repo({"pyproject.toml": "[tool.pytest.ini_options]\npython_functions = []\npython_classes = []\n",
                          "test_a.py": "def test_a():\n    pass\n\n\nclass TestB:\n    def test_b(self):\n        pass\n"})
        self.assertEqual(self.tool_json("ledger", cwd=root, expect=0)["result"]["counts"]["declarations"], 0)


class VulturePathsTest(FixtureCase):
    def test_whitelist_is_added_to_configured_paths_not_substituted(self):
        root = self.repo({"pyproject.toml": '[project]\nname = "p"\nversion = "0"\n[tool.vulture]\npaths = ["src"]\n',
                          "src/a.py": "def f():\n    return 1\n", "whitelist.py": "",
                          ".test-audit.json": json.dumps({"deadcode": {"disable": ["deptry"]}})})
        shims = self.shims(["uvx"], os.path.join(self._tmp, "uvx-calls"))
        res = self.tool_json("--dry-run", "deadcode", "--all", cwd=root, env={"PATH": self.safe_path([shims])}, expect=0)["result"]
        self.assertEqual(res["analyzers"][0]["command"], ["uvx", "--quiet", "vulture", "src", "whitelist.py"])
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "uvx-calls")))


class SuiteHermeticTest(FixtureCase):
    def test_host_fsmonitor_hook_never_runs_during_the_suite(self):
        marker = os.path.join(self._tmp, "host-hook-ran")
        hook = os.path.join(self._tmp, "fsmonitor")
        write(self._tmp, {"fsmonitor": "#!/bin/sh\ntouch %s\nprintf 'token\\0'\n" % shlex.quote(marker),
                          "host.gitconfig": "[core]\n fsmonitor = %s\n" % hook})
        os.chmod(hook, 0o755)
        repo = self.tmpdir("plain")
        # a child that only imports the suite helpers, then runs git the way a
        # careless test would (inherited env, no git() wrapper)
        script = ("import subprocess, sys; sys.path.insert(0, %r); import ta_helpers; "
                  "subprocess.run(['git', 'init', '-q'], cwd=%r, check=True); "
                  "subprocess.run(['git', 'status', '--porcelain'], cwd=%r, check=True)" % (HERE, repo, repo))
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.path.join(self._tmp, "host.gitconfig"))
        p = subprocess.run([sys.executable, "-B", "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(p.returncode, 0, p.stderr.decode())
        self.assertFalse(os.path.exists(marker))
