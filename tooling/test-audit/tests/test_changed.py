"""changed (classification, owner tests, plan, --run) and loc."""

import json
import os

from ta_helpers import RUNNER, FixtureCase, git, runner_config, write

BASE = {
    "pyproject.toml": '[project]\nname = "app"\n',
    "uv.lock": "version = 1\n",
    "package.json": json.dumps({"name": "app"}),
    "runner.py": RUNNER,
    "src/calc.py": "def add(a, b):\n    return a + b\n",
    "tests/test_calc.py": "from src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
    "lib/fmt.ts": "export const fmt = (x: number) => String(x);\n",
    "lib/fmt.test.ts": "",
    "lib/parse.ts": "export const parse = (s: string) => Number(s);\n",
    "lib/__tests__/parse.test.ts": "",
    "conftest.py": "",
    "tests/fixtures/data.json": "{}\n",
    "docs/guide.md": "# Guide\n",
    "old.py": "OLD = 1\n",
    ".gitignore": "ignored/\n",
}


class ChangedTest(FixtureCase):
    def _feature_repo(self, config):
        files = dict(BASE)
        files[".test-audit.json"] = json.dumps(config)
        root = self.repo(files)
        git(root, "checkout", "-q", "-b", "feature")
        write(root, {"src/calc.py": "def add(a, b):\n    return b + a\n", "docs/guide.md": "# Guide\nmore\n"})
        git(root, "rm", "-q", "old.py")
        git(root, "commit", "-qam", "committed change")
        write(root, {"lib/fmt.ts": "export const fmt = (x: number) => `${x}`;\n"})
        git(root, "add", "lib/fmt.ts")  # staged
        write(root, {"lib/parse.ts": "export const parse = (s: string) => +s;\n", "conftest.py": "X = 1\n"})  # unstaged
        write(root, {"src/new_mod.py": "Y = 2\n", "ignored/skip.py": "Z = 3\n"})  # untracked + ignored
        return root

    def test_classifies_all_change_kinds_and_maps_owner_tests(self):
        root = self._feature_repo(runner_config())
        res = self.tool_json("changed", cwd=root, expect=0)["result"]
        self.assertEqual(res["base"], git(root, "rev-parse", "main").strip())
        self.assertEqual({r["path"]: (r["class"], r["status"]) for r in res["files"]}, {
            "conftest.py": ("support", "modified"),
            "docs/guide.md": ("docs", "modified"),
            "lib/fmt.ts": ("production", "modified"),
            "lib/parse.ts": ("production", "modified"),
            "src/calc.py": ("production", "modified"),
            "src/new_mod.py": ("production", "untracked"),
        })
        self.assertEqual(res["deleted"], [{"path": "old.py", "class": "production", "sources": ["committed"]}])
        self.assertEqual({p: [o["test"] for o in v] for p, v in res["owners"].items()}, {
            "lib/fmt.ts": ["lib/fmt.test.ts"],
            "lib/parse.ts": ["lib/__tests__/parse.test.ts"],
            "src/calc.py": ["tests/test_calc.py"],
        })
        self.assertEqual(res["unmapped_production"], ["src/new_mod.py"])
        self.assertEqual([t["file"] for t in res["plan"]["tests"]], ["lib/__tests__/parse.test.ts", "lib/fmt.test.ts", "tests/test_calc.py"])

    def test_run_executes_plan_and_reports_failures_and_unavailable_checks(self):
        cfg = runner_config(checks={"ok-lint": "sh -c 'exit 0' lint {files}"})
        root = self.repo({"runner.py": RUNNER, "src/calc.py": "def add(a, b):\n    return a + b\n",
                          "tests/test_calc.py": "from src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                          ".test-audit.json": json.dumps(cfg)})
        write(root, {"src/calc.py": "def add(a, b):\n    return a + b + 0\n"})
        ok = self.tool_json("changed", "--run", cwd=root, expect=0)["result"]["results"]
        self.assertEqual([c["status"] for c in ok["checks"]] + [t["status"] for t in ok["tests"]], ["passed", "passed"])
        write(root, {"src/calc.py": "def add(a, b):\n    return a - b\n"})
        self.tool_json("changed", "--run", cwd=root, expect=1)
        write(root, {"src/calc.py": "def add(a, b):\n    return a + b + 0\n",
                     ".test-audit.json": json.dumps(runner_config(checks={"gone": "definitely-not-installed-tool {files}"}))})
        missing = self.tool_json("changed", "--run", cwd=root, expect=2)["result"]["results"]["checks"]
        self.assertEqual(missing[0]["status"], "unavailable")

    def test_base_falls_back_to_master_and_fails_without_any_default(self):
        root = self.repo({"a.py": "A = 1\n"}, branch="master")
        write(root, {"a.py": "A = 2\n"})
        res = self.tool_json("changed", cwd=root, expect=0)["result"]
        self.assertEqual(res["base_ref"], "merge-base(HEAD, master)")
        trunk = self.repo({"a.py": "A = 1\n"}, name="trunk", branch="trunk")
        err = self.tool_json("changed", cwd=trunk, expect=2)["error"]
        self.assertIn("--base", err["message"])
        self.tool_json("changed", "--base", "trunk", cwd=trunk, expect=0)


class LocTest(FixtureCase):
    def test_counts_by_class_with_untracked_and_binary_files(self):
        root = self.repo({
            "src/app.py": "a = 1\nb = 2\nc = 3\n",
            "README.md": "one\ntwo\n",
            "tests/test_app.py": "def test_a():\n    pass\n",
        })
        write(root, {
            "src/app.py": "a = 1\nb = 20\nc = 3\nd = 4\n",  # +2 -1
            "tests/test_new.py": "def test_b():\n    pass\n\n",  # untracked, 3 lines
            "tests/fixtures/img.png": b"\x89PNG\r\n\x00\x00binary",
        })
        os.remove(os.path.join(root, "README.md"))  # docs -2
        res = self.tool_json("loc", cwd=root, expect=0)["result"]
        by = res["by_class"]
        self.assertEqual(by["production"], {"files": 1, "added": 2, "removed": 1, "net": 1})
        self.assertEqual(by["test"], {"files": 1, "added": 3, "removed": 0, "net": 3})
        self.assertEqual(by["docs"], {"files": 1, "added": 0, "removed": 2, "net": -2})
        self.assertEqual(by["support"]["files"], 1)
        self.assertEqual(res["binary"], ["tests/fixtures/img.png"])
        self.assertEqual(res["totals"], {"files": 4, "added": 5, "removed": 3, "net": 2})
