"""detect: roots, package managers, runners, checks, config, ambiguity."""

import json
import os

from ta_helpers import FixtureCase, write


def _roots(data):
    return {(r["path"], r["ecosystem"]): r for r in data["result"]["roots"]}


FAKE_BIN = "#!/bin/sh\ncase \" $* \" in *' --help '*) echo '  --maxWorkers <n>'; echo '  --runInBand'; exit 0;; esac\nexit 0\n"


class DetectTest(FixtureCase):
    def test_pnpm_workspace_roots_runners_and_manager(self):
        root = self.repo({
            "package.json": json.dumps({"private": True, "packageManager": "pnpm@9.1.0", "devDependencies": {"vitest": "^3"}}),
            "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n  - '!packages/skip'\n",
            "packages/a/package.json": json.dumps({"name": "a", "devDependencies": {"vitest": "^3"}}),
            "packages/a/vitest.config.ts": "export default {}\n",
            "packages/b/package.json": json.dumps({"name": "b", "scripts": {"test": "jest"}, "devDependencies": {"jest": "^29"}}),
            "packages/skip/package.json": json.dumps({"name": "skip"}),
            "tests/fixtures/proj/package.json": json.dumps({"name": "fixture-project"}),
            "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
        })
        data = self.tool_json("detect", cwd=os.path.join(root, "packages"), expect=0)
        roots = _roots(data)
        self.assertEqual(sorted(roots), [(".", "js"), ("packages/a", "js"), ("packages/b", "js")])
        self.assertEqual(roots[("packages/a", "js")]["runner"], "vitest")
        self.assertEqual(roots[("packages/b", "js")]["runner"], "jest")
        self.assertEqual(roots[(".", "js")]["package_manager"]["name"], "pnpm")
        self.assertEqual(roots[(".", "js")]["package_manager"]["source"], "packageManager:package.json")
        ws = data["result"]["workspaces"]["js"][0]
        self.assertEqual(ws["members"], ["packages/a", "packages/b"])

    def test_lockfile_decides_manager_and_bun_uses_its_own_runner(self):
        yarn = self.repo({"package.json": json.dumps({"devDependencies": {"jest": "^29"}}), "yarn.lock": "", "a.test.js": "test('x',()=>{})\n"}, name="y")
        r = _roots(self.tool_json("detect", cwd=yarn, expect=0))[(".", "js")]
        self.assertEqual((r["package_manager"]["name"], r["runner"]), ("yarn", "jest"))
        bun = self.repo({"package.json": json.dumps({"name": "b"}), "bun.lock": "{}", "a.test.ts": "test('x',()=>{})\n"}, name="b")
        r = _roots(self.tool_json("detect", cwd=bun, expect=0))[(".", "js")]
        self.assertEqual((r["package_manager"]["name"], r["runner"]), ("bun", "bun"))
        self.assertEqual(r["test_command"]["command"][:2], ["bun", "test"])

    def test_vite_plus_project_runs_vp_test(self):
        root = self.repo({
            "package.json": json.dumps({"devDependencies": {"vite-plus": "^1"}}),
            "vite.config.ts": "import { defineConfig } from 'vite-plus';\nexport default defineConfig({ test: {} });\n",
        })
        r = _roots(self.tool_json("detect", cwd=root, expect=0))[(".", "js")]
        self.assertEqual(r["runner"], "vp")
        self.assertIn("vp check", [c["name"] for c in r["checks"]])

    def test_uv_workspace_members_and_python_checks(self):
        root = self.repo({
            "pyproject.toml": '[project]\nname = "top"\n\n[tool.uv.workspace]\nmembers = ["packages/*"]\nexclude = ["packages/skip"]\n\n[tool.ruff]\nline-length = 100\n',
            "uv.lock": "version = 1\n",
            "packages/p1/pyproject.toml": '[project]\nname = "p1"\ndependencies = ["pytest>=8"]\n',
            "packages/skip/pyproject.toml": '[project]\nname = "skip"\n',
        })
        data = self.tool_json("detect", cwd=root, expect=0)
        roots = _roots(data)
        self.assertEqual(sorted(roots), [(".", "python"), ("packages/p1", "python")])
        self.assertEqual(roots[(".", "python")]["package_manager"]["name"], "uv")
        self.assertEqual(roots[("packages/p1", "python")]["name"], "p1")
        self.assertEqual([c["name"] for c in roots[(".", "python")]["checks"]], ["ruff check", "ruff format"])
        self.assertEqual(data["result"]["ecosystems"], ["python"])

    def test_ambiguous_runners_resolve_by_nearest_config_or_fail_loudly(self):
        root = self.repo({
            "package.json": json.dumps({"devDependencies": {"jest": "^29", "vitest": "^3"}}),
            "jest.config.js": "module.exports = {}\n",
            "vitest.config.ts": "export default {}\n",
            "legacy/jest.config.js": "module.exports = {}\n",
            "legacy/old.test.js": "test('x', () => {})\n",
            "src/new.test.ts": "test('x', () => {})\n",
            ".gitignore": "node_modules\n",
        })
        write(root, {"node_modules/.bin/jest": FAKE_BIN, "node_modules/.bin/vitest": FAKE_BIN})
        for b in ("jest", "vitest"):
            os.chmod(os.path.join(root, "node_modules", ".bin", b), 0o755)
        r = _roots(self.tool_json("detect", cwd=root, expect=0))[(".", "js")]
        self.assertTrue(r["ambiguous"])
        self.assertEqual(sorted(x["runner"] for x in r["runners_detected"]), ["jest", "vitest"])
        legacy = self.tool_json("--dry-run", "run", "legacy/old.test.js", cwd=root, expect=0)["result"]["results"][0]
        self.assertEqual(legacy["runner"], "jest")
        # the config that decided the runner is the one the runner loads
        self.assertEqual(legacy["command"][legacy["command"].index("--config") + 1], "legacy/jest.config.js")
        unresolved = self.tool_json("--dry-run", "run", "src/new.test.ts", cwd=root, expect=2)["result"]["results"][0]
        self.assertIn("ambiguous", unresolved["reason"])
        write(root, {".test-audit.json": json.dumps({"runners": [{"glob": "src/**", "runner": "vitest"}]})})
        settled = self.tool_json("--dry-run", "run", "src/new.test.ts", cwd=root, expect=0)["result"]["results"][0]
        self.assertEqual(settled["runner"], "vitest")

    def test_config_overrides_are_reported_and_validated(self):
        root = self.repo({
            "package.json": json.dumps({"devDependencies": {"vitest": "^3"}}),
            ".test-audit.json": json.dumps({"roots": {".": {"testCommand": "make test FILE={file}"}}, "baseBranch": "main"}),
        })
        data = self.tool_json("detect", cwd=root, expect=0)
        self.assertEqual(data["result"]["config"], {"path": ".test-audit.json", "applied": ["baseBranch", "roots"]})
        r = _roots(data)[(".", "js")]
        self.assertEqual(r["test_command"]["command"], ["make", "test", "FILE={file}"])
        write(root, {".test-audit.json": json.dumps({"testComand": "typo"})})
        bad = self.tool_json("detect", cwd=root, expect=2)
        self.assertEqual(bad["error"]["kind"], "usage")
        self.assertIn("testComand", bad["error"]["message"])
