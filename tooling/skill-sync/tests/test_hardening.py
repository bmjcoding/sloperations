"""Regression tests for the review findings, still exercised through the CLI with local fixtures."""
from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import time
import unittest
from pathlib import Path

from support import BASE_FILES, SANDBOX, VERIFY_FLAGS, CliTest

AUTOREVIEW = Path(__file__).resolve().parents[3] / "upstream" / "autoreview.json"
DROP_BRAND = {"id": "brand/hosting", "file": "skills/demo/SKILL.md", "anchor": "Use BrandCo hosting.",
              "replacement": "Use any host.", "reason": "brand"}


def py(code: str) -> dict:
    return {"name": "probe", "argv": ["{python}", "-c", code]}


class VerifySandbox(CliTest):
    """Finding 1: verify commands run upstream code; they must not reach credentials or the repository."""

    @unittest.skipUnless(SANDBOX, "needs sandbox-exec (macOS) or a working bwrap (Linux)")
    def test_reads_and_writes_outside_the_stage_are_denied(self):
        secret = self.home / "credential"
        secret.write_text("dummy-secret\n")
        target = self.root / "owned-by-upstream"
        code = (
            "import os, sys, pathlib\n"
            "def denied(fn):\n"
            "    try:\n"
            "        fn()\n"
            "    except (PermissionError, FileNotFoundError):  # sandbox-exec denies; bwrap doesn't mount\n"
            "        return True\n"
            "    return False\n"
            f"assert denied(lambda: open({str(secret)!r}).read()), 'read a HOME credential'\n"
            f"assert denied(lambda: open({str(target)!r}, 'w').write('x')), 'wrote into the repository'\n"
            f"assert denied(lambda: os.listdir({str(self.cache)!r})), 'listed the cache'\n"
            "pathlib.Path('stage-ok').write_text('x')\n"
            "pathlib.Path(os.environ['TMPDIR'], 'tmp-ok').write_text('x')\n"
            "(pathlib.Path.home() / 'home-ok').write_text('x')\n"
        )
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, verify=[py(code)]))
        self.upstream.commit({"skills/demo/new.md": "candidate\n"})
        output = self.assertExit(0, self.run_cli("check", "demo"))
        self.assertIn(f"({SANDBOX})", output)
        self.assertFalse(target.exists())

    @unittest.skipUnless(SANDBOX, "needs sandbox-exec (macOS) or a working bwrap (Linux)")
    def test_only_declared_prefixes_reach_the_shared_temp_folders(self):
        code = (
            "import sys, tempfile\n"
            "with tempfile.TemporaryDirectory(prefix='skill-sync-scratch-x.', dir='/tmp') as t:\n"
            "    open(t + '/f', 'w').write('x')\n"
            "if sys.platform == 'darwin':  # bwrap gives the sandbox its own private /tmp instead\n"
            "    try:\n"
            "        tempfile.mkdtemp(prefix='undeclared-', dir='/tmp')\n"
            "        raise SystemExit('created an undeclared folder in shared /tmp')\n"
            "    except PermissionError:\n"
            "        pass\n"
        )
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, [DROP_BRAND], verify=[py(code)], verify_shared_tmp=["skill-sync-scratch-"]))
        self.assertExit(0, self.run_cli("sync", "demo", "--checks"))
        self.write_fork(self.fork(pin, [DROP_BRAND], verify=[py(code)], verify_shared_tmp=["x"]))
        self.assertIn("verify_shared_tmp must list name prefixes", self.assertExit(2, self.run_cli("sync", "demo")))

    def test_environment_is_scrubbed_and_home_is_fresh(self):
        self.env.update(SKILL_SYNC_GITHUB_TOKEN="tok-1", GH_TOKEN="tok-2", AWS_SECRET_ACCESS_KEY="tok-3")
        code = (
            "import os, pathlib\n"
            "leaked = [k for k, v in os.environ.items() if v.startswith('tok-')]\n"
            "assert not leaked, leaked\n"
            f"assert os.environ['HOME'] != {str(self.home)!r}, 'real HOME'\n"
            "assert not (pathlib.Path.home() / 'real-home-marker').exists(), 'HOME is not fresh'\n"
        )
        (self.home / "real-home-marker").write_text("x")
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, [DROP_BRAND], verify=[py(code)]))
        self.assertExit(0, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS))

    def test_refuses_without_a_sandbox_unless_explicitly_unsandboxed(self):
        self.env["SKILL_SYNC_SANDBOX"] = "none"
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, [DROP_BRAND], verify=[py("pass")]))
        output = self.assertExit(4, self.run_cli("sync", "demo", "--checks"))
        self.assertIn("no sandbox is available", output)
        self.assertFalse((self.root / "skills/demo").exists())
        self.upstream.commit({"skills/demo/new.md": "x\n"})
        self.assertIn("no sandbox is available", self.assertExit(4, self.run_cli("check", "demo")))

        output = self.assertExit(0, self.run_cli("sync", "demo", "--checks", "--unsandboxed-verify"))
        self.assertIn("without a sandbox", output)
        # A fork without verify commands never needs a sandbox.
        self.write_fork(self.fork(pin, [DROP_BRAND]))
        self.assertExit(0, self.run_cli("check", "demo"))


class VerifyProcessGroup(CliTest):
    """Finding 7: a timed-out (or finished) verify command leaves nothing running.

    A surviving grandchild reports over TCP, which works inside the sandbox too (a
    file receipt outside the stage would be denied there and prove nothing).
    """

    def setUp(self):
        super().setUp()
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(5)
        self.listener.settimeout(0.2)
        self.addCleanup(self.listener.close)
        self.port = self.listener.getsockname()[1]

    def survivors(self, wait: float) -> int:
        count, deadline = 0, time.monotonic() + wait
        while time.monotonic() < deadline:
            try:
                conn, _ = self.listener.accept()
                conn.close()
                count += 1
            except socket.timeout:
                pass
        return count

    def _fork_with(self, parent_code: str, timeout: int):
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, [DROP_BRAND], verify=[
            {"name": "spawn", "argv": ["{python}", "-c", parent_code], "timeout": timeout}]))

    def _grandchild(self, delay: float) -> str:
        child = (f"import time, socket; time.sleep({delay}); "
                 f"socket.create_connection(('127.0.0.1', {self.port})).close()")
        return f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {child!r}])"

    def test_grandchild_reports_when_not_killed(self):
        # Control: the reporting channel works from inside verify.
        self._fork_with(self._grandchild(0) + "; import time; time.sleep(2)", timeout=60)
        self.assertExit(0, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS))
        self.assertEqual(self.survivors(1), 1)

    def test_timeout_kills_the_whole_process_group(self):
        self._fork_with(self._grandchild(3) + "; import time; time.sleep(60)", timeout=1)
        started = time.monotonic()
        output = self.assertExit(1, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS))
        self.assertLess(time.monotonic() - started, 30)
        self.assertIn("timed out; its process group was killed", output)
        self.assertEqual(self.survivors(5), 0, "a grandchild outlived the timeout")

    def test_background_processes_are_reaped_after_a_normal_exit(self):
        self._fork_with(self._grandchild(2), timeout=60)
        self.assertExit(0, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS))
        self.assertEqual(self.survivors(4), 0, "a background process outlived its verify command")


class BumpTransaction(CliTest):
    """Finding 2: the pin and the outputs move together, or neither does."""

    def _two_outputs(self):
        pin = self.upstream.commit(dict(BASE_FILES, **{"skills/second/SKILL.md": "old\n"}))
        fork = self.write_fork(self.fork(pin, paths=[{"upstream": "skills/demo", "output": "skills/demo"},
                                                     {"upstream": "skills/second", "output": "skills/second"}]))
        self.assertExit(0, self.run_cli("sync", "demo"))
        candidate = self.upstream.commit({"skills/demo/new.md": "new\n", "skills/second/SKILL.md": "new\n"})
        return pin, candidate, fork

    def test_unreplaceable_destination_changes_nothing(self):
        pin, candidate, fork = self._two_outputs()
        shutil.rmtree(self.root / "skills/second")
        (self.root / "skills/second").write_text("obstacle")
        pinned, before = fork.read_bytes(), self.tree()
        output = self.assertExit(4, self.run_cli("bump", "demo", "--to", candidate))
        self.assertIn("nothing was changed", output)
        self.assertEqual(pinned, fork.read_bytes())
        self.assertEqual(before, self.tree())
        self.assertFalse((self.root / "upstream/.skill-sync-txn.json").exists())

    def test_interrupted_transaction_is_rolled_back(self):
        pin, candidate, fork = self._two_outputs()
        old_manifest, before = fork.read_text(), self.tree()
        # Killed mid-way: demo swapped and the pin written, second not yet, never committed.
        skills = self.root / "skills"
        os.rename(skills / "demo", skills / ".demo.skill-sync-old-dead")
        (skills / "demo").mkdir()
        (skills / "demo/SKILL.md").write_text("half-published\n")
        (skills / ".second.skill-sync-new-dead").mkdir()
        fork.write_text(old_manifest.replace(pin, candidate))
        journal = {"state": "prepared", "manifest": {
            "path": "upstream/demo.json", "tmp": "upstream/.demo.json.skill-sync-dead",
            "old_text": old_manifest, "new_text": old_manifest.replace(pin, candidate)},
            "outputs": [
                {"dest": "skills/demo", "existed": True, "new": "skills/.demo.skill-sync-new-dead",
                 "old": "skills/.demo.skill-sync-old-dead"},
                {"dest": "skills/second", "existed": True, "new": "skills/.second.skill-sync-new-dead",
                 "old": "skills/.second.skill-sync-old-dead"}]}
        (self.root / "upstream/.skill-sync-txn.json").write_text(json.dumps(journal))
        self.assertIn("an update was interrupted", self.assertExit(1, self.run_cli("verify")))

        self.assertIn("rolled back an interrupted update", self.assertExit(0, self.run_cli("sync", "demo")))
        self.assertEqual(old_manifest, fork.read_text())
        self.assertEqual(before, self.tree())
        self.assertExit(0, self.run_cli("verify"))

    def test_committed_transaction_is_finished(self):
        pin, candidate, fork = self._two_outputs()
        self.assertExit(0, self.run_cli("bump", "demo", "--to", candidate))
        after = self.tree()
        (self.root / "skills/.demo.skill-sync-old-dead").mkdir()
        journal = {"state": "committed", "manifest": None, "outputs": [
            {"dest": "skills/demo", "existed": True, "new": "skills/.demo.skill-sync-new-dead",
             "old": "skills/.demo.skill-sync-old-dead"}]}
        (self.root / "upstream/.skill-sync-txn.json").write_text(json.dumps(journal))
        self.assertIn("finished an interrupted update", self.assertExit(0, self.run_cli("sync", "demo")))
        self.assertIn(candidate, fork.read_text())
        self.assertEqual(after, self.tree())


class Signatures(CliTest):
    """Finding 3: require_signed gates watch-mode bump exactly as it gates rules mode."""

    def test_watch_bump_refuses_an_unsigned_commit(self):
        github = self.github()
        pin = self.upstream.commit(BASE_FILES)
        unsigned = self.upstream.commit({"skills/demo/SKILL.md": "changed\n"}, "unsigned")
        fork = self.write_fork({"name": "notes", "mode": "watch",
                                "upstream": {"repo": "https://github.com/fixture/upstream.git", "branch": "main",
                                             "sha": pin, "require_signed": True},
                                "files": [{"path": "skills/demo/SKILL.md"}]})
        github.verified[unsigned] = False
        self.assertIn("is not signed", self.assertExit(1, self.run_cli("bump", "notes", "--to", unsigned)))
        self.assertIn(pin, fork.read_text())
        self.assertEqual(len(github.requests), 1)

        github.verified[unsigned] = True
        self.assertExit(0, self.run_cli("bump", "notes", "--to", unsigned))
        self.assertIn(unsigned, fork.read_text())

    def test_rules_check_refuses_an_unsigned_candidate(self):
        github = self.github()
        pin = self.upstream.commit(BASE_FILES)
        candidate = self.upstream.commit({"skills/demo/new.md": "x\n"})
        data = self.fork(pin, [DROP_BRAND])
        data["upstream"].update(repo="https://github.com/fixture/upstream.git", require_signed=True)
        self.write_fork(data)
        github.verified[candidate] = False
        self.assertIn("GitHub verification: unsigned", self.assertExit(1, self.run_cli("check", "demo")))
        github.verified[candidate] = True
        self.assertExit(0, self.run_cli("check", "demo"))


class MergedCandidates(CliTest):
    """Finding 4: a topic branch merged into the tracked branch yields the mainline merge."""

    def test_merge_commit_is_the_candidate(self):
        self.upstream.commit(BASE_FILES, "base")
        self.upstream.git("checkout", "-q", "-b", "topic")
        self.upstream.commit({"skills/demo/new.md": "topic\n"}, "topic skill change")
        self.upstream.git("checkout", "-q", "main")
        pin = self.upstream.commit({"other/main.txt": "main advanced\n"}, "main pin")
        self.write_fork(self.fork(pin, [DROP_BRAND]))
        self.upstream.git("merge", "-q", "--no-ff", "topic", "-m", "merge skill update")
        merge = self.upstream.git("rev-parse", "HEAD")
        report = self.tmp / "check.json"
        self.assertExit(0, self.run_cli("check", "demo", "--json", str(report)))
        self.assertEqual(json.loads(report.read_text())["candidate"], merge)
        self.assertExit(0, self.run_cli("bump", "demo", "--to", merge))
        self.assertEqual(self.out("new.md"), "topic\n")

    def test_merge_that_doesnt_touch_tracked_paths_is_not_a_candidate(self):
        pin = self.upstream.commit(BASE_FILES, "base")
        self.write_fork(self.fork(pin, [DROP_BRAND]))
        self.upstream.git("checkout", "-q", "-b", "topic")
        self.upstream.commit({"other/x.txt": "x\n"}, "untracked change")
        self.upstream.git("checkout", "-q", "main")
        self.upstream.git("merge", "-q", "--no-ff", "topic", "-m", "merge")
        self.assertExit(3, self.run_cli("check", "demo"))


class VerifyInterpreter(CliTest):
    """Finding 5: {python} meets the fork's verify_python, whichever Python runs skill-sync."""

    def test_unavailable_minimum_is_a_clear_error(self):
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, [DROP_BRAND], verify=[py("pass")], verify_python=">=3.99"))
        self.assertIn("needs Python 3.99+", self.assertExit(4, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS)))

    def test_python_placeholder_meets_the_minimum(self):
        minimum = "3.10" if sys.version_info >= (3, 10) or shutil.which("python3.10") or any(
            shutil.which(f"python3.{m}") for m in range(11, 31)) else "3.9"
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(pin, [DROP_BRAND], verify_python=f">={minimum}", verify=[
            py(f"import sys; assert sys.version_info >= ({minimum.replace('.', ', ')}), sys.version")]))
        self.assertExit(0, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS))

    @unittest.skipUnless(AUTOREVIEW.is_file(), "runs inside the repository that vendors autoreview")
    def test_repository_definitions_pin_pillow_for_every_interpreter(self):
        definition = json.loads(AUTOREVIEW.read_text())
        self.assertEqual(definition["verify_python"], ">=3.10")
        pillow = next(v for v in definition["verify"] if v["name"] == "pillow")["argv"]
        self.assertIn("Pillow==12.3.0; python_version >= '3.10'", pillow)
        self.assertIn("Pillow==11.3.0; python_version < '3.10'", pillow)


class MarkdownSections(CliTest):
    """Finding 6: CommonMark heading indentation and fence lengths."""

    def _sync_removing(self, text: str, heading: str = "## Drop", count: int = 1):
        pin = self.upstream.commit(dict(BASE_FILES, **{"skills/demo/SKILL.md": text}))
        self.write_fork(self.fork(pin, [{"id": "drop", "kind": "remove-section", "file": "skills/demo/SKILL.md",
                                         "anchor": heading, "count": count, "reason": "x"}]))
        return self.run_cli("sync", "demo")

    def test_indented_headings_end_and_start_sections(self):
        self.assertExit(0, self._sync_removing("# Demo\n\n## Drop\nremove this\n\n  ## Keep\nkeep this\n"))
        self.assertEqual(self.out(), "# Demo\n\n  ## Keep\nkeep this\n")
        self.assertExit(0, self._sync_removing("# Demo\n\n   ## Drop ##\nremove\n## Keep\nkeep\n"))
        self.assertEqual(self.out(), "# Demo\n\n## Keep\nkeep\n")

    def test_four_space_indent_is_code_not_a_heading(self):
        self.assertExit(0, self._sync_removing("## Drop\nx\n\n    ## Keep\n## Keep\nk\n"))
        self.assertEqual(self.out(), "## Keep\nk\n")

    def test_fence_closes_only_on_a_matching_fence(self):
        fenced = "````md\n```py\n## Drop\ninside\n````\n\n## Keep\noutside\n"
        output = self.assertExit(1, self._sync_removing(fenced))
        self.assertIn("found 0", output)
        tilde = "~~~\n```\n## Drop\n~~~\n## Drop\ngone\n## Keep\nk\n"
        self.assertExit(0, self._sync_removing(tilde))
        self.assertEqual(self.out(), "~~~\n```\n## Drop\n~~~\n## Keep\nk\n")


if __name__ == "__main__":
    unittest.main()
