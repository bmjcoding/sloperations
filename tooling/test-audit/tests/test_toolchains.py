"""Opt-in end-to-end checks against real installs (network, ~2-4 minutes).

    TEST_AUDIT_TOOLCHAINS=1 python3 -m unittest test_toolchains

Each case installs a throwaway project with its real package manager, then
drives detect / run / changed --run / deadcode / mutate through the CLI.
A missing toolchain skips its case; it never passes silently.
"""

import json
import os
import shutil
import signal
import subprocess
import time
import unittest

from ta_helpers import HERE, PY, TOOL, FixtureCase, git, hermetic_git_env, write

ENABLED = os.environ.get("TEST_AUDIT_TOOLCHAINS") == "1"


def sh(cwd, *argv):
    p = subprocess.run(list(argv), cwd=cwd, env=hermetic_git_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
    if p.returncode != 0:
        raise AssertionError("%s failed:\n%s" % (" ".join(argv), p.stdout.decode()[-3000:]))


@unittest.skipUnless(ENABLED, "set TEST_AUDIT_TOOLCHAINS=1 to install real toolchains")
class ToolchainTest(FixtureCase):
    def _need(self, *tools):
        for t in tools:
            if not shutil.which(t):
                self.skipTest("%s not on PATH" % t)

    def _project(self, files, install):
        root = self.repo(files, commit=False)
        sh(root, *install)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "fixture")
        return root

    def _exercise(self, root, runner, test, prod, dead_line, symbol, mutation):
        det = self.tool_json("detect", cwd=root, expect=0)
        owners = [r for r in det["result"]["roots"] if test.startswith(r["path"] if r["path"] != "." else "")]
        self.assertIn(runner, {r["runner"] for r in owners})
        run = self.tool_json("run", test, cwd=root, expect=0)["result"]["results"][0]
        self.assertEqual(run["status"], "passed", run)
        with open(os.path.join(root, prod), "a") as f:
            f.write(dead_line)
        changed = self.tool_json("changed", "--run", cwd=root)["result"]
        self.assertIn(test, [t["file"] for t in changed["plan"]["tests"]])
        self.assertEqual([t["status"] for t in changed["results"]["tests"] if t["file"] == test], ["passed"])
        dc = self.tool_json("deadcode", cwd=root, expect=1, timeout=900)["result"]
        self.assertTrue(any(symbol in str(f["name"]) for f in dc["findings"]), dc)
        self.assertEqual(len(git(root, "worktree", "list").splitlines()), 1)
        m = self.tool_json("mutate", "--file", prod, "--replace", mutation[0], "--with", mutation[1], "--test", test, cwd=root, expect=0)
        self.assertEqual(m["result"]["verdict"], "CAUGHT")
        tracked = [l for l in git(root, "status", "--porcelain").splitlines() if not l.startswith("??")]
        self.assertEqual(tracked, [" M " + prod])

    def test_pnpm_vitest_workspace_with_installed_knip(self):
        self._need("pnpm")
        root = self._project({
            "package.json": json.dumps({"private": True, "devDependencies": {"vitest": "*", "knip": "*"}}),
            "pnpm-workspace.yaml": "packages:\n  - packages/*\n",
            "packages/a/package.json": json.dumps({"name": "a", "type": "module", "main": "src/index.ts", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "*"}}),
            "packages/a/src/index.ts": "export { add } from './math';\n",
            "packages/a/src/math.ts": "export function add(a: number, b: number) { return a + b; }\n",
            "packages/a/src/math.test.ts": "import { it, expect } from 'vitest';\nimport { add } from './math';\nit('adds', () => { expect(add(1, 2)).toBe(3); });\n",
            ".gitignore": "node_modules\n",
        }, ["pnpm", "install", "--silent"])
        self._exercise(root, "vitest", "packages/a/src/math.test.ts", "packages/a/src/math.ts",
                       "export const deadPnpm = 1;\n", "deadPnpm", ("a + b", "a - b"))

    def test_vite_plus(self):
        self._need("pnpm")
        root = self._project({
            "package.json": json.dumps({"private": True, "type": "module", "main": "src/index.ts", "scripts": {"test": "vp test"}, "devDependencies": {"vite-plus": "*"}}),
            "vite.config.ts": "import { defineConfig } from 'vite-plus';\nexport default defineConfig({ test: {} });\n",
            "src/index.ts": "export { mul } from './mul';\n",
            "src/mul.ts": "export const mul = (a: number, b: number) => a * b;\n",
            "src/mul.test.ts": "import { test, expect } from 'vite-plus/test';\nimport { mul } from './mul';\ntest('mul', () => { expect(mul(2, 3)).toBe(6); });\n",
            ".gitignore": "node_modules\n",
        }, ["pnpm", "install", "--silent"])
        self._exercise(root, "vp", "src/mul.test.ts", "src/mul.ts", "export const deadVp = 1;\n", "deadVp", ("a * b", "a + b"))

    def test_npm_jest_with_ephemeral_knip(self):
        self._need("npm", "npx")
        root = self._project({
            "package.json": json.dumps({"private": True, "main": "src/index.js", "scripts": {"test": "jest"}, "devDependencies": {"jest": "*"}}),
            "src/index.js": "module.exports = require('./sub');\n",
            "src/sub.js": "exports.sub = (a, b) => a - b;\n",
            "src/__tests__/sub.test.js": "const { sub } = require('../sub');\ntest('sub', () => { expect(sub(3, 1)).toBe(2); });\n",
            ".gitignore": "node_modules\n",
        }, ["npm", "install", "--no-audit", "--no-fund", "--silent"])
        write(root, {"src/orphan.js": "module.exports = 1;\n"})
        self._exercise(root, "jest", "src/__tests__/sub.test.js", "src/sub.js", "\n", "orphan.js", ("a - b", "a + b"))

    def test_yarn_berry_pnp_vitest(self):
        self._need("yarn")
        root = self._project({
            "package.json": json.dumps({"private": True, "type": "module", "main": "src/index.ts", "packageManager": "yarn@4.18.1", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "*", "vite": "*"}}),
            "src/index.ts": "export { neg } from './neg';\n",
            "src/neg.ts": "export const neg = (a: number) => -a;\n",
            "src/neg.test.ts": "import { test, expect } from 'vitest';\nimport { neg } from './neg';\ntest('neg', () => { expect(neg(2)).toBe(-2); });\n",
            ".gitignore": "node_modules\n.yarn/*\n.pnp.*\n",
        }, ["yarn", "install"])
        self._exercise(root, "vitest", "src/neg.test.ts", "src/neg.ts", "export const deadYarn = 1;\n", "deadYarn", ("-a", "a"))

    def test_bun_test(self):
        self._need("bun", "bunx")
        root = self._project({
            "package.json": json.dumps({"private": True, "type": "module", "module": "src/index.ts", "scripts": {"test": "bun test"}, "devDependencies": {"@types/bun": "*"}}),
            "src/index.ts": "export { sq } from './sq';\n",
            "src/sq.ts": "export const sq = (a: number) => a * a;\n",
            "src/sq.test.ts": "import { test, expect } from 'bun:test';\nimport { sq } from './sq';\ntest('sq', () => { expect(sq(3)).toBe(9); });\n",
            ".gitignore": "node_modules\n",
        }, ["bun", "install", "--silent"])
        self._exercise(root, "bun", "src/sq.test.ts", "src/sq.ts", "export const deadBun = 1;\n", "deadBun", ("a * a", "a + a"))

    def _uv(self, deps, addopts=""):
        self._need("uv", "uvx")
        pyproject = ('[project]\nname = "calc"\nversion = "0"\nrequires-python = ">=3.9"\n\n'
                     '[dependency-groups]\ndev = [%s]\n\n[tool.pytest.ini_options]\npythonpath = ["src"]\n%s'
                     % (", ".join('"%s"' % d for d in deps), addopts))
        return self._project({
            "pyproject.toml": pyproject,
            "src/calc/__init__.py": "",
            "src/calc/core.py": "def double(x):\n    return x * 2\n",
            "tests/test_core.py": "from calc.core import double\n\n\ndef test_double():\n    assert double(2) == 4\n",
            ".gitignore": ".venv\n",
        }, ["uv", "sync", "--quiet"])

    def test_uv_pytest(self):
        root = self._uv(["pytest"])
        self._exercise(root, "pytest", "tests/test_core.py", "src/calc/core.py",
                       "\n\ndef dead_uv():\n    return 1\n", "dead_uv", ("x * 2", "x * 3"))

    def test_uv_pytest_xdist(self):
        root = self._uv(["pytest", "pytest-xdist"], 'addopts = "-n 2"\n')
        cmd = self.tool_json("run", "tests/test_core.py", cwd=root, expect=0)["result"]["results"][0]["command"]
        self.assertEqual(cmd[-2:], ["-n", "0"])
        self._exercise(root, "pytest", "tests/test_core.py", "src/calc/core.py",
                       "\n\ndef dead_xdist():\n    return 1\n", "dead_xdist", ("x * 2", "x * 3"))


def _tree(root):
    out = set()
    for d, dirs, files in os.walk(root):
        if ".git" in dirs:
            dirs.remove(".git")
        for name in dirs + files:
            out.add(os.path.relpath(os.path.join(d, name), root))
    return out


@unittest.skipUnless(ENABLED, "set TEST_AUDIT_TOOLCHAINS=1 to install real toolchains")
class ReadOnlyToolchainTest(FixtureCase):
    def test_vitest_with_repo_cache_dir_writes_nothing(self):
        if not shutil.which("pnpm"):
            self.skipTest("pnpm not on PATH")
        root = self.repo({
            "package.json": json.dumps({"private": True, "type": "module", "devDependencies": {"vitest": "*"}}),
            "vitest.config.ts": "export default { cacheDir: '.cache/vite', test: {} };\n",
            "a.test.ts": "import { test, expect } from 'vitest';\ntest('one', () => expect(1).toBe(1));\n",
            ".gitignore": "node_modules\n",
        }, commit=False)
        sh(root, "pnpm", "install", "--silent")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "fixture")
        before = _tree(root)
        self.tool_json("run", "a.test.ts", cwd=root, expect=0)
        self.assertEqual(sorted(_tree(root) - before), [])

    def test_jest_runs_with_the_nearest_config_that_selected_it(self):
        if not shutil.which("npm"):
            self.skipTest("npm not on PATH")
        root = self.repo({
            "package.json": json.dumps({"private": True, "scripts": {"test": "vitest"}, "devDependencies": {"jest": "*"}}),
            "jest.config.js": "module.exports = { testMatch: ['**/modern/*.test.js'] };\n",
            "legacy/jest.config.js": "module.exports = { testMatch: ['**/*.test.js'] };\n",
            "legacy/a.test.js": "test('a', () => { expect(1).toBe(1); });\n",
            ".gitignore": "node_modules\n",
        }, commit=False)
        sh(root, "npm", "install", "--no-audit", "--no-fund", "--silent")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "fixture")
        r = self.tool_json("run", "legacy/a.test.js", cwd=root, expect=0)["result"]["results"][0]
        self.assertEqual(r["status"], "passed", r)

    def _uv(self, files, deps):
        if not (shutil.which("uv") and shutil.which("uvx")):
            self.skipTest("uv not on PATH")
        root = self.repo(files, commit=False)
        if deps:
            sh(root, "uv", "add", "--quiet", "--dev", *deps)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "fixture")
        return root

    def test_python_checks_never_fix_or_cache_in_the_repo(self):
        root = self._uv({
            "pyproject.toml": '[project]\nname = "ro"\nversion = "0"\nrequires-python = ">=3.9"\n\n[tool.ruff]\nfix = true\n\n[tool.mypy]\n',
            ".gitignore": ".venv\n",
            "a.py": "VALUE = 1\n",
        }, ["ruff", "mypy"])
        write(root, {"a.py": "import os\nVALUE = 2\n"})
        before = _tree(root)
        res = self.tool_json("changed", "--run", cwd=root, expect=1)["result"]["results"]["checks"]
        self.assertEqual({c["name"]: c["status"] for c in res}["ruff check"], "failed")
        with open(os.path.join(root, "a.py")) as f:
            self.assertEqual(f.read(), "import os\nVALUE = 2\n")
        self.assertEqual(sorted(_tree(root) - before), [])

    def test_deadcode_compares_new_roots_and_rejects_partial_vulture_runs(self):
        root = self._uv({"README.md": "base\n", ".test-audit.json": json.dumps({"deadcode": {"disable": ["deptry"]}})}, [])
        write(root, {"service/pyproject.toml": '[project]\nname = "svc"\nversion = "0"\n',
                     "service/dead.py": "def new_dead():\n    return 1\n"})
        dc = self.tool_json("deadcode", cwd=root, expect=1, timeout=900)["result"]
        self.assertEqual([f["name"] for f in dc["findings"]], ["unused function 'new_dead'"])
        write(root, {"service/broken.py": "def broken(:\n"})
        bad = self.tool_json("deadcode", cwd=root, expect=2, timeout=900)["result"]["analyzers"][0]
        self.assertEqual(bad["status"], "error")
        self.assertTrue(any("broken.py" in d for d in bad["diagnostics"]))


@unittest.skipUnless(ENABLED, "set TEST_AUDIT_TOOLCHAINS=1 to install real toolchains")
class Round2ToolchainTest(FixtureCase):
    def _installed(self, files, install):
        root = self.repo(files, commit=False)
        sh(root, *install)
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "fixture")
        return root

    def test_tsc_extends_chain_with_incremental_leaves_no_buildinfo(self):
        if not shutil.which("npm"):
            self.skipTest("npm not on PATH")
        root = self._installed({
            "package.json": json.dumps({"private": True, "devDependencies": {"typescript": "*"}}),
            "tsconfig.json": json.dumps({"extends": "./tsconfig.base.json", "include": ["src"]}),
            "tsconfig.base.json": json.dumps({"compilerOptions": {"incremental": True, "target": "es2022"}}),
            "src/a.ts": "export const a = 1;\n", ".gitignore": "node_modules\n",
        }, ["npm", "install", "--silent", "--no-audit", "--no-fund"])
        write(root, {"src/a.ts": "export const a = 2;\n"})
        before = _tree(root)
        checks = self.tool_json("changed", "--run", cwd=root, expect=0)["result"]["results"]["checks"]
        self.assertEqual({c["name"]: c["status"] for c in checks}["tsc"], "passed")
        self.assertEqual(sorted(_tree(root) - before), [])

    def _vitest_loading(self):
        if not shutil.which("pnpm"):
            self.skipTest("pnpm not on PATH")
        root = self._installed({
            "package.json": json.dumps({"private": True, "type": "module", "devDependencies": {"vitest": "*"}}),
            "vitest.config.ts": "await new Promise((r) => setTimeout(r, 30000));\nexport default { test: {} };\n",
            "a.test.ts": "import { test, expect } from 'vitest';\ntest('one', () => expect(1).toBe(1));\n",
            ".gitignore": "node_modules\n",
        }, ["pnpm", "install", "--silent"])
        vt = os.path.join(root, "node_modules", ".vite-temp")
        before = _tree(root)
        proc = subprocess.Popen([TOOL, "run", "a.test.ts"], cwd=root, env=dict(hermetic_git_env(), TEST_AUDIT_PYTHON=PY),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while not (os.path.isdir(vt) and [n for n in os.listdir(vt) if n.endswith(".mjs")]):
            self.assertLess(time.time(), deadline, "vitest never bundled its config")
            time.sleep(0.05)
        return root, vt, before, proc

    def test_sigterm_while_vitest_loads_leaves_only_vites_bundle_in_node_modules(self):
        # documented limit: the interrupted load's compiled config stays in
        # node_modules/.vite-temp (ownership of files there cannot be proven)
        root, vt, before, proc = self._vitest_loading()
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=30), 143)
        left = sorted(_tree(root) - before)
        self.assertTrue(left, "expected Vite's interrupted bundle to remain")
        self.assertTrue(all(p.startswith(os.path.join("node_modules", ".vite-temp")) for p in left), left)

    def test_interrupted_run_never_deletes_another_process_bundle(self):
        root, vt, before, proc = self._vitest_loading()
        unrelated = os.path.join(vt, "other.config.ts.timestamp-%d-123abcd.mjs" % int(time.time() * 1000))
        with open(unrelated, "w") as f:
            f.write("unrelated live process bundle")
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=30), 143)
        self.assertTrue(os.path.exists(unrelated))

    def _ts_project(self, version, incremental_via_extends):
        if not shutil.which("npm"):
            self.skipTest("npm not on PATH")
        base = {"compilerOptions": {"incremental": True, "target": "es2022"}} if incremental_via_extends else {"compilerOptions": {"target": "es2022"}}
        root = self.repo({
            "package.json": json.dumps({"private": True, "devDependencies": {"typescript": version}}),
            "tsconfig.json": json.dumps({"extends": "./tsconfig.base.json", "include": ["src"]}),
            "tsconfig.base.json": json.dumps(base), "src/a.ts": "export const a = 1;\n", ".gitignore": "node_modules\n",
        }, name="ts-%s-%s" % (version, incremental_via_extends), commit=False)
        sh(root, "npm", "install", "--silent", "--no-audit", "--no-fund")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "fixture")
        write(root, {"src/a.ts": "export const a = 2;\n"})
        return root

    def test_tsc_build_info_redirect_only_when_effectively_incremental(self):
        for version in ("5.4.5", "5.9.3", "7.0.2"):
            for incremental in (False, True):
                root = self._ts_project(version, incremental)
                before = _tree(root)
                checks = self.tool_json("changed", "--run", cwd=root, expect=0)["result"]
                tsc = [c for c in checks["results"]["checks"] if c["name"] == "tsc"][0]
                self.assertEqual(tsc["status"], "passed", (version, incremental, tsc))
                self.assertEqual("--tsBuildInfoFile" in tsc["command"], incremental, (version, incremental))
                self.assertEqual(sorted(_tree(root) - before), [], (version, incremental))

    def test_jest_failure_exit_code_130_is_caught_not_a_signal(self):
        if not shutil.which("npm"):
            self.skipTest("npm not on PATH")
        root = self._installed({
            "package.json": json.dumps({"private": True, "devDependencies": {"jest": "*"}, "jest": {"testFailureExitCode": 130}}),
            "owner.js": "exports.VALUE = 1;\n",
            "a.test.js": "const { VALUE } = require('./owner');\ntest('one', () => expect(VALUE).toBe(1));\n",
            ".gitignore": "node_modules\n",
        }, ["npm", "install", "--silent", "--no-audit", "--no-fund"])
        res = self.tool_json("mutate", "--file", "owner.js", "--replace", "1", "--with", "2", "--test", "a.test.js",
                             cwd=root, expect=0)["result"]
        self.assertEqual(res["verdict"], "CAUGHT")
        self.assertEqual(res["results"][0]["exit_code"], 130)

    def _uv_repo(self, files):
        if not (shutil.which("uv") and shutil.which("uvx")):
            self.skipTest("uv not on PATH")
        return self.repo(files)

    def test_deadcode_scans_a_dir_that_only_becomes_a_root_at_head(self):
        root = self._uv_repo({"service/setup.py": "from setuptools import setup\nsetup(name='svc', version='0')\n",
                              "service/a.py": "def unused_before():\n    return 1\n",
                              ".test-audit.json": json.dumps({"deadcode": {"disable": ["deptry"]}})})
        write(root, {"service/pyproject.toml": '[project]\nname = "svc"\nversion = "0"\n'})
        res = self.tool_json("deadcode", cwd=root, expect=0, timeout=900)["result"]
        self.assertEqual(res["findings"], [])
        self.assertEqual(res["analyzers"][0]["base_findings"], 1)

    def test_vulture_whitelist_keeps_configured_paths(self):
        root = self._uv_repo({"pyproject.toml": '[project]\nname = "p"\nversion = "0"\n[tool.vulture]\npaths = ["src"]\nmin_confidence = 60\n',
                              "src/unused.py": "def orphan():\n    return 1\n", "whitelist.py": "# nothing whitelisted\n",
                              ".test-audit.json": json.dumps({"deadcode": {"disable": ["deptry"]}})})
        res = self.tool_json("deadcode", "--all", cwd=root, expect=1, timeout=900)["result"]
        self.assertEqual([f["name"] for f in res["findings"]], ["unused function 'orphan'"])

    def test_linux_zombie_descendants_count_as_dead(self):
        if not shutil.which("docker") or subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            self.skipTest("docker not available")
        scripts = os.path.abspath(os.path.join(HERE, "..", "..", "..", "skills", "test-audit", "scripts"))
        scratch = self.tmpdir("linux")
        probe = (
            "import json, os, subprocess, sys, time\n"
            "root = '/scratch/repo'; os.makedirs(root)\n"
            "env = dict(os.environ, TEST_AUDIT_PYTHON=sys.executable)\n"
            "open(root + '/test_a.py', 'w').write('def test_a(): pass\\n')\n"
            "open(root + '/keeper.py', 'w').write(\"import subprocess, sys\\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\\n\")\n"
            "open(root + '/.test-audit.json', 'w').write(json.dumps({'testCommand': 'python keeper.py {file}'}))\n"
            "for a in (['init', '-q'], ['add', '.'], ['commit', '-qm', 'base']): subprocess.run(['git'] + a, cwd=root, env=env, check=True)\n"
            "t = time.monotonic()\n"
            "p = subprocess.run(['/tool/test-audit', '--json', 'run', 'test_a.py'], cwd=root, env=env, capture_output=True, text=True, timeout=60)\n"
            "print(json.dumps({'exit': p.returncode, 'seconds': time.monotonic() - t, 'out': p.stdout, 'err': p.stderr}))\n"
        )
        # the suite's hermetic git settings (fixed fixture identity, no host config)
        git_env = [x for k, v in sorted(hermetic_git_env().items()) if k.startswith("GIT_") for x in ("-e", "%s=%s" % (k, v))]
        # python is PID 1 and never reaps orphans, so killed descendants stay zombies
        p = subprocess.run(["docker", "run", "--rm", "-v", scripts + ":/tool:ro", "-v", scratch + ":/scratch"] + git_env
                           + ["python:3.12", "python", "-c", probe], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
        self.assertEqual(p.returncode, 0, p.stderr.decode())
        res = json.loads(p.stdout.decode().strip().splitlines()[-1])
        self.assertEqual(res["exit"], 0, res)
        self.assertLess(res["seconds"], 10, res)
        self.assertEqual(json.loads(res["out"])["result"]["results"][0]["status"], "passed")
