"""Ecosystem isolation and no writes into the target repo."""

import json
import os

from ta_helpers import FixtureCase, git, write

PY_TOOLS = ["python", "python3", "uv", "uvx", "pytest", "pip", "ruff", "mypy", "pyright", "vulture", "deptry"]
NODE_TOOLS = ["node", "npm", "npx", "pnpm", "yarn", "bun", "bunx", "vp", "tsc", "knip", "vitest", "jest"]
SH_RUNNER = "#!/bin/sh\ngrep -q FAIL \"$1\" && exit 1\nexit 0\n"


class IsolationTest(FixtureCase):
    def _exercise(self, root, path):
        for args in (["detect"], ["changed", "--run"], ["deadcode"], ["loc"], ["ledger"]):
            self.tool("--json", *args, cwd=root, env={"PATH": path})

    def test_typescript_repo_runs_nothing_python_specific(self):
        marker = os.path.join(self._tmp, "calls")
        root = self.repo({
            "package.json": json.dumps({"devDependencies": {"vitest": "*", "typescript": "*", "prettier": "*"}}),
            "tsconfig.json": "{}", "src/a.ts": "export const a = 1;\n", "src/a.test.ts": "it('a', () => {})\n",
            "check.sh": SH_RUNNER, ".test-audit.json": json.dumps({"testCommand": "sh check.sh {file}"}),
        })
        write(root, {"src/a.ts": "export const a = 2;\n"})
        self._exercise(root, self.safe_path([self.shims(PY_TOOLS, marker)]))
        self.assertFalse(os.path.exists(marker), open(marker).read() if os.path.exists(marker) else "")

    def test_python_repo_runs_nothing_node_specific(self):
        marker = os.path.join(self._tmp, "calls")
        root = self.repo({
            "pyproject.toml": '[project]\nname = "p"\n\n[tool.ruff]\n', "src/p.py": "X = 1\n",
            "tests/test_p.py": "def test_p():\n    pass\n",
            "check.sh": SH_RUNNER, ".test-audit.json": json.dumps({"testCommand": "sh check.sh {file}"}),
        })
        write(root, {"src/p.py": "X = 2\n"})
        self._exercise(root, self.safe_path([self.shims(NODE_TOOLS, marker)]))
        self.assertFalse(os.path.exists(marker), open(marker).read() if os.path.exists(marker) else "")

    def test_read_only_commands_leave_the_repo_untouched(self):
        root = self.repo({
            "package.json": json.dumps({"name": "x"}), "pyproject.toml": '[project]\nname = "p"\n',
            "src/a.ts": "export const a = 1;\n", "src/a.test.ts": "it('a', () => {})\n",
            "src/p.py": "X = 1\n", "tests/test_p.py": "def test_p():\n    pass\n",
        })
        write(root, {"src/p.py": "X = 2\n"})
        before = git(root, "status", "--porcelain", "--ignored")
        for args in (["detect"], ["changed"], ["loc"], ["ledger"], ["--dry-run", "deadcode"], ["--dry-run", "run", "tests/test_p.py"]):
            self.tool("--json", *args, cwd=root)
        self.assertEqual(git(root, "status", "--porcelain", "--ignored"), before)
