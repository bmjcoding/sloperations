"""deadcode: missing analyzers never pass silently; base worktree is always removed."""

import json

from ta_helpers import FixtureCase, git, write


class DeadcodeTest(FixtureCase):
    def _worktrees(self, root):
        return [l for l in git(root, "worktree", "list", "--porcelain").splitlines() if l.startswith("worktree ")]

    def test_python_analyzers_without_uvx_are_unavailable(self):
        root = self.repo({"pyproject.toml": '[project]\nname = "p"\n', "src/p.py": "def f():\n    return 1\n"})
        write(root, {"src/p.py": "def f():\n    return 2\n"})
        data = self.tool_json("deadcode", cwd=root, env={"PATH": self.safe_path()}, expect=2)
        self.assertEqual([(a["name"], a["status"]) for a in data["result"]["analyzers"]],
                         [("vulture", "unavailable"), ("deptry", "unavailable")])
        self.assertEqual(len(self._worktrees(root)), 1)

    def test_js_without_knip_or_package_runner_is_unavailable(self):
        root = self.repo({"package.json": json.dumps({"name": "x"}), "src/a.ts": "export const a = 1;\n"})
        data = self.tool_json("deadcode", "--all", cwd=root, env={"PATH": self.safe_path()}, expect=2)
        self.assertEqual([(a["name"], a["status"]) for a in data["result"]["analyzers"]], [("knip", "unavailable")])

    def test_dry_run_lists_commands_and_leaves_no_worktree(self):
        root = self.repo({"package.json": json.dumps({"name": "x"}), "src/a.ts": "export const a = 1;\n"})
        marker = self._tmp + "/npx-calls"
        shims = self.shims(["npx"], marker)
        code, out, _ = self.tool("--dry-run", "deadcode", cwd=root, env={"PATH": self.safe_path([shims])})
        self.assertEqual(code, 0)
        self.assertIn("git worktree add --detach", out)
        self.assertIn("knip@6 --reporter json", out)
        self.assertEqual(len(self._worktrees(root)), 1)
