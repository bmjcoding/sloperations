"""Round-3 review regressions: escaped/`$`/backtick template placeholders, repo paths
in embedded template placeholders, owner tests outside testpaths, pytest's
testpaths fallback, runner results before the 128+N heuristic, NUL bytes in
config, and an in-repo TMPDIR."""

import json
import os

from ta_helpers import FixtureCase, git, write
from test_hardening import calc_repo

FAKE_JEST = r"""#!/bin/sh
case " $* " in *" --help "*) echo '  -i, --runInBand'; exit 0;; esac
case " $* " in
  *fail.test.js*) echo 'Tests:       1 failed, 1 total'; exit 130;;
  *killed.test.js*) echo 'something died'; exit 137;;
esac
echo 'Tests:       1 passed, 1 total'; exit 0
"""


class EscapedPlaceholderTest(FixtureCase):
    PKG = "packages/x;touch PWN;#"

    def _workspace(self, test_command="true {file}", pkg=None, name="repo"):
        pkg = pkg or self.PKG
        return self.repo(name=name, files={
            "package.json": json.dumps({"private": True, "workspaces": ["packages/*"]}),
            pkg + "/package.json": json.dumps({"name": "x"}),
            pkg + "/a.test.ts": "test('a', () => {});\n",
            ".test-audit.json": json.dumps({"testCommand": test_command}),
        })

    def _pwned(self, root):
        return [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f == "PWN"]

    def test_escaped_dollar_and_backtick_placeholders_are_refused_before_running(self):
        for i, template in enumerate((r"sh -c 'printf %s \{root}; test -e {file}'", "sh -c 'echo ${root}; test -e {file}'",
                                      "sh -c 'echo `{root}`; test -e {file}'", r"sh -c 'test -e \{file}'")):
            root = self._workspace(template, name="r%d" % i)
            data = self.tool_json("run", self.PKG + "/a.test.ts", cwd=root, expect=2)
            self.assertEqual(data["error"]["kind"], "usage", template)
            self.assertEqual(self._pwned(root), [], template)
        # an escaped backslash leaves the placeholder unquoted and unescaped:
        # the quoted value stays one word, so this form is allowed and safe
        root = self._workspace(r"sh -c 'printf %s \\{root} >/dev/null; test -e {file}'", name="ok")
        res = self.tool_json("run", self.PKG + "/a.test.ts", cwd=root, expect=0)["result"]["results"][0]
        self.assertEqual(res["status"], "passed")
        self.assertEqual(self._pwned(root), [])

    def test_repo_paths_in_embedded_template_placeholders_stay_literal(self):
        root = self._workspace("sh -c 'test -e {file} && test -n {root}'")
        res = self.tool_json("run", self.PKG + "/a.test.ts", cwd=root, expect=0)["result"]["results"][0]
        self.assertEqual(res["status"], "passed")
        self.assertEqual(self._pwned(root), [])

    def test_quoted_embedded_template_placeholders_are_config_errors(self):
        root = self._workspace("sh -c 'echo \"{file}\"'")
        err = self.tool_json("run", self.PKG + "/a.test.ts", cwd=root, expect=2)["error"]
        self.assertEqual(err["kind"], "usage")
        self.assertIn("double quotes", err["message"])
        self.assertEqual(self._pwned(root), [])


class TestpathsTest(FixtureCase):
    CUSTOM = json.dumps({"testCommand": "true {file}"})

    def test_changed_maps_owner_tests_outside_testpaths(self):
        root = self.repo({"pytest.ini": "[pytest]\ntestpaths = tests\n", "tests/test_inside.py": "def test_inside():\n    pass\n",
                          "other/a.py": "VALUE = 1\n", "other/test_a.py": "def test_a():\n    pass\n", ".test-audit.json": self.CUSTOM})
        write(root, {"other/a.py": "VALUE = 2\n"})
        res = self.tool_json("changed", cwd=root, expect=0)["result"]
        self.assertEqual(res["owners"], {"other/a.py": [{"test": "other/test_a.py", "rule": "sibling"}]})
        self.assertEqual([t["file"] for t in res["plan"]["tests"]], ["other/test_a.py"])

    def test_unmatched_testpaths_fall_back_to_recursive_discovery_like_pytest(self):
        root = self.repo({"pytest.ini": "[pytest]\ntestpaths = missing\n", "test_a.py": "def test_a():\n    pass\n",
                          "sub/test_b.py": "def test_b():\n    pass\n", ".test-audit.json": self.CUSTOM})
        self.assertEqual(self.tool_json("ledger", cwd=root, expect=0)["result"]["files"], ["sub/test_b.py", "test_a.py"])
        base = self.tool_json("--dry-run", "baseline", cwd=root, expect=0)["result"]
        self.assertEqual(base["summary"]["total"], 2)

    def test_globbed_testpaths_that_match_still_filter(self):
        root = self.repo({"pytest.ini": "[pytest]\ntestpaths = tests* missing\n", "tests_unit/test_a.py": "def test_a():\n    pass\n",
                          "other/test_b.py": "def test_b():\n    pass\n", ".test-audit.json": self.CUSTOM})
        self.assertEqual(self.tool_json("ledger", cwd=root, expect=0)["result"]["files"], ["tests_unit/test_a.py"])


class RunnerResultFirstTest(FixtureCase):
    def test_runner_summary_beats_the_128_plus_n_heuristic(self):
        root = self.repo({"package.json": json.dumps({"devDependencies": {"jest": "*"}}), "fail.test.js": "", "killed.test.js": "",
                          ".gitignore": "node_modules\n"})
        write(root, {"node_modules/.bin/jest": FAKE_JEST})
        os.chmod(os.path.join(root, "node_modules/.bin/jest"), 0o755)
        res = self.tool_json("run", "fail.test.js", "killed.test.js", cwd=root, expect=1)["result"]["results"]
        self.assertEqual((res[0]["status"], res[0]["exit_code"]), ("failed", 130))
        self.assertEqual(res[1]["status"], "error")
        self.assertIn("128+9", res[1]["reason"])


class ConfigBytesTest(FixtureCase):
    def test_nul_bytes_in_config_strings_are_usage_errors(self):
        root = self.repo({"test_a.py": "def test_a():\n    pass\n"})
        for cfg in ({"testCommand": "true \0 {file}"}, {"checks": {"x": "true\0"}}, {"testGlobs": ["a\0"]}, {"roots": {"a\0": {}}}):
            write(root, {".test-audit.json": json.dumps(cfg)})
            code, out, err = self.tool("--json", "run", "test_a.py", cwd=root)
            self.assertEqual(code, 2, cfg)
            self.assertNotIn("Traceback", err)
            self.assertIn("NUL", json.loads(out)["error"]["message"])


class TempDirTest(FixtureCase):
    def test_in_repo_tmpdir_is_refused_without_creating_anything(self):
        root = calc_repo(self)
        for args in (["run", "tests/test_add.py"], ["mutate", "--file", "src/calc.py", "--replace", "a + b", "--with", "a * b",
                                                  "--test", "tests/test_add.py", "--skip-precheck"]):
            data = self.tool_json(*args, cwd=root, env={"TMPDIR": root}, expect=2)
            self.assertIn("TMPDIR", data["error"]["message"])
        self.assertEqual(sorted(n for n in os.listdir(root) if n != ".git"), [".test-audit.json", "runner.py", "src", "tests"])
        self.assertEqual(git(root, "status", "--porcelain", "--ignored"), "")
