"""Campaign helpers: baseline, ledger, mutate."""

import hashlib
import json
import os
import signal
import subprocess
import time

from ta_helpers import PY, RUNNER, TOOL, FixtureCase, git, hermetic_git_env, runner_config, write

CALC = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
TEST_ADD = "from src.calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def calc_repo(case, **extra):
    files = {"src/calc.py": CALC, "runner.py": RUNNER, "tests/test_add.py": TEST_ADD,
             ".test-audit.json": json.dumps(runner_config())}
    files.update(extra)
    return case.repo(files)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class BaselineTest(FixtureCase):
    def test_records_head_results_failures_and_line_counts(self):
        root = calc_repo(self, **{
            "tests/test_bug.py": "from src.calc import sub\n\ndef test_sub():\n    assert sub(3, 1) == 3\n",
            "tests/helpers.py": "X = 1\nY = 2\n",
            "other/test_elsewhere.py": "def test_x():\n    pass\n",
        })
        write(root, {"scratch.txt": "dirty\n"})
        self.assertEqual(self.tool("baseline", "--out", os.path.join(self._tmp, "b.json"), cwd=root)[0], 2)
        out = os.path.join(self._tmp, "b.json")
        code, _, _ = self.tool("baseline", "tests", "src", "--allow-dirty", "--out", out, cwd=root)
        self.assertEqual(code, 1)
        with open(out) as f:
            doc = json.load(f)
        self.assertEqual(doc["head"], git(root, "rev-parse", "HEAD").strip())
        self.assertTrue(doc["dirty"])
        self.assertEqual({r["file"]: r["status"] for r in doc["files"]},
                         {"tests/test_add.py": "passed", "tests/test_bug.py": "failed"})
        self.assertEqual([f["file"] for f in doc["failing"]], ["tests/test_bug.py"])
        self.assertIn("product bugs", doc["failing_note"])
        zero = {"files": 0, "lines": 0}
        self.assertEqual(doc["loc"], {"test": {"files": 2, "lines": 8}, "support": {"files": 1, "lines": 2},
                                      "production": {"files": 1, "lines": 6}, "docs": zero, "config": zero,
                                      "generated": zero, "other": zero})
        totals = self.tool_json("loc", "--totals", "tests", "src", cwd=root, expect=0)["result"]
        self.assertEqual(totals["by_class"], doc["loc"])


LEDGER_TS = r"""
import { describe, it, test, expect } from "vitest";
// it("commented out", () => {})
/* test("block comment", () => {}) */
const s = "it('inside a string', () => {})";
const re = /it\("in a regex'/;
const t = `test("in a template ${s}")`;
describe("outer", () => {
  it("plain", () => { expect(re.test("x")).toBe(false); });
  it.each([[1, 2], [3, 4], [5, 6]])("adds %i", (a, b) => {});
  test.each`
    a    | b
    ${1} | ${2}
    ${3} | ${4}
  `("table $a", ({ a, b }) => {});
  describe.skip("inner", () => {
    test.concurrent("deep", async () => {});
  });
  it.todo("later");
});
describe.each([["x"], ["y"]])("suite %s", (v) => {
  it.only("in each suite", () => {});
});
test.skipIf(process.env.CI)("conditional", () => {});
"""

LEDGER_PY = '''
import unittest
import pytest


def helper():
    pass


def test_plain():
    pass


@pytest.mark.parametrize("a,b", [(1, 2), pytest.param(3, 4, id="three")], ids=["one", "ignored"])
def test_ids(a, b):
    pass


@pytest.mark.parametrize("x", [0, 1])
@pytest.mark.parametrize("y", ["a", "b", "c"])
def test_stacked(x, y):
    pass


@pytest.mark.parametrize("z", CASES)
def test_dynamic(z):
    pass


class TestThing:
    @pytest.mark.skip
    def test_method(self):
        pass

    def not_a_test(self):
        pass


class NotCollected:
    def test_ignored(self):
        pass


class TestHasInit:
    def __init__(self):
        pass

    def test_ignored(self):
        pass


class Legacy(unittest.TestCase):
    def test_legacy(self):
        pass
'''


class LedgerTest(FixtureCase):
    def test_js_declarations_ignore_strings_comments_and_regexes(self):
        root = self.repo({"package.json": "{}", "src/a.test.ts": LEDGER_TS})
        rows = self.tool_json("ledger", cwd=root, expect=0)["result"]["declarations"]
        self.assertEqual([(r["line"], r["name"], r["kind"], r["rows"], r["modifiers"]) for r in rows], [
            (9, "outer > plain", "test", None, []),
            (10, "outer > adds %i", "each", 3, ["each"]),
            (11, "outer > table $a", "each", 2, ["each"]),
            (17, "outer > inner > deep", "test", None, ["concurrent", "skip"]),
            (19, "outer > later", "test", None, ["todo"]),
            (22, "suite %s > in each suite", "test", None, ["only"]),
            (24, "conditional", "test", None, ["skipIf"]),
        ])
        self.assertEqual(rows[5]["suite_rows"], [2])
        self.assertTrue(all(r["mark"] == "" and r["evidence"] == "" for r in rows))

    def test_python_declarations_with_parametrize_rows_and_ids(self):
        root = self.repo({"tests/test_mod.py": LEDGER_PY})
        rows = self.tool_json("ledger", "--expand-rows", cwd=root, expect=0)["result"]["declarations"]
        self.assertEqual([(r["name"], r["kind"], r["rows"]) for r in rows], [
            ("test_plain", "test", None),
            ("test_ids[one]", "row", 2), ("test_ids[three]", "row", 2),
            ("test_stacked[a-0]", "row", 6), ("test_stacked[a-1]", "row", 6),
            ("test_stacked[b-0]", "row", 6), ("test_stacked[b-1]", "row", 6),
            ("test_stacked[c-0]", "row", 6), ("test_stacked[c-1]", "row", 6),
            ("test_dynamic", "each", None),
            ("TestThing::test_method", "test", None),
            ("Legacy::test_legacy", "test", None),
        ])
        self.assertEqual(rows[10]["modifiers"], ["skip"])

    def test_formats_are_deterministic(self):
        root = self.repo({"package.json": "{}", "b.test.ts": LEDGER_TS, "tests/test_mod.py": LEDGER_PY})
        first = self.tool("ledger", "--format", "csv", cwd=root)
        second = self.tool("ledger", "--format", "csv", cwd=root)
        self.assertEqual(first, second)
        lines = first[1].splitlines()
        self.assertEqual(lines[0], "file,line,name,kind,rows,modifiers,mark,evidence")
        self.assertTrue(lines[1].startswith("b.test.ts,9,"))
        md = self.tool("ledger", cwd=root)[1].splitlines()
        self.assertEqual(md[0], "| file | line | name | kind | rows | modifiers | mark | evidence |")
        self.assertEqual(len(md), 2 + 7 + 6)


class MutateTest(FixtureCase):
    def _mutate(self, root, *args, expect):
        return self.tool_json("mutate", "--file", "src/calc.py", *args, cwd=root, expect=expect)["result"]

    def test_caught_missed_and_error_restore_exact_bytes(self):
        root = calc_repo(self)
        path = os.path.join(root, "src/calc.py")
        before = sha(path)
        mtime = os.stat(path).st_mtime_ns
        caught = self._mutate(root, "--replace", "a + b", "--with", "a * b", "--test", "tests/test_add.py", expect=0)
        self.assertEqual(caught["verdict"], "CAUGHT")
        missed = self._mutate(root, "--replace", "- b", "--with", "+ b", "--test", "tests/test_add.py", expect=1)
        self.assertEqual(missed["verdict"], "MISSED")
        error = self._mutate(root, "--replace", "def add(", "--with", "def plus(", "--test", "tests/test_add.py", expect=2)
        self.assertEqual(error["verdict"], "ERROR")
        self.assertEqual(sha(path), before)
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)
        self.assertEqual(git(root, "status", "--porcelain"), "")

    def test_ambiguous_match_needs_occurrence(self):
        root = calc_repo(self)
        before = sha(os.path.join(root, "src/calc.py"))
        code, _, err = self.tool("mutate", "--file", "src/calc.py", "--replace", "return a", "--with", "return -a", "--test", "tests/test_add.py", cwd=root)
        self.assertEqual(code, 2)
        self.assertIn("matches 2 times", err)
        self.assertEqual(sha(os.path.join(root, "src/calc.py")), before)
        second = self._mutate(root, "--replace", "return a", "--with", "return -a", "--occurrence", "2", "--test", "tests/test_add.py", expect=1)
        self.assertEqual(second["line"], 6)
        first = self._mutate(root, "--replace", "return a", "--with", "return -a", "--occurrence", "1", "--test", "tests/test_add.py", expect=0)
        self.assertEqual(first["line"], 2)

    def test_red_keeper_before_mutation_is_an_error_not_a_catch(self):
        root = calc_repo(self, **{"tests/test_red.py": "def test_red():\n    assert False\n"})
        res = self._mutate(root, "--replace", "a + b", "--with", "a * b", "--test", "tests/test_red.py", expect=2)
        self.assertEqual(res["verdict"], "ERROR")
        self.assertIn("before mutation", res["reason"])

    def test_refuses_paths_outside_the_repo_and_foreign_patches(self):
        root = calc_repo(self)
        outside = os.path.join(self._tmp, "outside.py")
        write(self._tmp, {"outside.py": "x = 1\n"})
        code, _, err = self.tool("mutate", "--file", outside, "--replace", "1", "--with", "2", "--test", "tests/test_add.py", cwd=root)
        self.assertEqual(code, 2)
        self.assertIn("outside the repository", err)
        patch = os.path.join(self._tmp, "p.diff")
        write(self._tmp, {"p.diff": "--- a/runner.py\n+++ b/runner.py\n@@ -1 +1 @@\n-x\n+y\n"})
        code, _, err = self.tool("mutate", "--file", "src/calc.py", "--patch", patch, "--test", "tests/test_add.py", cwd=root)
        self.assertEqual(code, 2)
        self.assertIn("must touch only src/calc.py", err)
        write(self._tmp, {"p.diff": "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,3 +1,3 @@\n def add(a, b):\n-    return a + b\n+    return a + b + 1\n \n"})
        self.assertEqual(self._mutate(root, "--patch", patch, "--test", "tests/test_add.py", expect=0)["verdict"], "CAUGHT")

    def test_sigterm_during_keeper_run_restores_the_file(self):
        root = calc_repo(self, **{"tests/test_slow.py": "import time\n\ndef test_slow():\n    time.sleep(60)\n"})
        path = os.path.join(root, "src/calc.py")
        with open(path, "rb") as f:
            original = f.read()
        env = dict(hermetic_git_env(), TEST_AUDIT_PYTHON=PY)
        proc = subprocess.Popen([TOOL, "mutate", "--file", "src/calc.py", "--replace", "a + b", "--with", "a * b",
                                 "--test", "tests/test_slow.py", "--skip-precheck"], cwd=root, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            with open(path, "rb") as f:
                if f.read() != original:
                    break
            time.sleep(0.05)
        else:
            proc.kill()
            self.fail("mutation never applied")
        time.sleep(0.3)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=30), 143)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), original)
