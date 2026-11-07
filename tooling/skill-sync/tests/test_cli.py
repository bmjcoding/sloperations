"""skill-sync behaviour, exercised only through its CLI against local fixture repos (no network)."""
from __future__ import annotations

import json
import os
import unittest

from support import BASE_FILES, VERIFY_FLAGS, CliTest

DROP_BRAND = {"id": "brand/hosting", "file": "skills/demo/SKILL.md", "anchor": "Use BrandCo hosting.",
              "replacement": "Use any host.", "reason": "brand"}
DROP_HOSTED = {"id": "skill/no-hosted", "kind": "remove-section", "file": "skills/demo/SKILL.md",
               "anchor": "## Hosted", "reason": "bot-specific"}
TRIPWIRE = {"terms": ["brandco"], "allow": [{"paths": ["skills/demo/LICENSE"], "reason": "copyright holder"}]}


class AnchorCounts(CliTest):
    def test_count_must_match_exactly(self):
        sha = self.upstream.commit(BASE_FILES)
        twice = {"id": "run/twice", "file": "skills/demo/SKILL.md", "anchor": "run it", "replacement": "go",
                 "count": 2, "reason": "both"}
        self.write_fork(self.fork(sha, [twice]))
        self.assertExit(0, self.run_cli("sync", "demo"))
        self.assertIn("go\ngo\n", self.out())

        once = dict(twice, count=1)
        self.write_fork(self.fork(sha, [once]))
        before = self.tree()
        output = self.assertExit(1, self.run_cli("sync", "demo"))
        self.assertIn("expected 1 match(es)", output)
        self.assertIn("found 2", output)
        self.assertEqual(before, self.tree(), "a failed sync must leave the output alone")

    def test_missing_anchor_and_glob_counts(self):
        sha = self.upstream.commit(BASE_FILES)
        absent = {"id": "gone", "file": "skills/demo/SKILL.md", "anchor": "no such text", "replacement": "",
                  "reason": "x"}
        self.write_fork(self.fork(sha, [absent]))
        self.assertIn("found 0", self.assertExit(1, self.run_cli("sync", "demo")))

        # A glob's count is the total across every file it matches.
        across = {"id": "md/see", "file": "skills/demo/**/*.md", "anchor": "the skill", "replacement": "the guide",
                  "count": 1, "reason": "x"}
        self.write_fork(self.fork(sha, [across]))
        self.assertExit(0, self.run_cli("sync", "demo"))
        self.assertIn("the guide", self.out("references/more.md"))

    def test_remove_section_counts_headings_outside_code(self):
        files = dict(BASE_FILES)
        files["skills/demo/SKILL.md"] += "\n```md\n## Hosted\n```\n"
        sha = self.upstream.commit(files)
        self.write_fork(self.fork(sha, [DROP_HOSTED]))
        self.assertExit(0, self.run_cli("sync", "demo"))
        text = self.out()
        self.assertNotIn("Ask the bot", text)
        self.assertIn("## Run", text)
        self.assertIn("```md\n## Hosted\n```", text, "a heading inside a code fence is not a section")


class StrictFailureKeepsPin(CliTest):
    def test_check_and_bump_refuse_a_candidate_that_breaks_a_strict_rule(self):
        pin = self.upstream.commit(BASE_FILES)
        fork_file = self.write_fork(self.fork(pin, [DROP_BRAND, DROP_HOSTED], tripwire=TRIPWIRE))
        self.assertExit(0, self.run_cli("sync", "demo"))
        self.assertExit(3, self.run_cli("check", "demo"))  # nothing newer

        broken = self.upstream.commit({"skills/demo/SKILL.md": BASE_FILES["skills/demo/SKILL.md"].replace(
            "Use BrandCo hosting.", "Host with BrandCo.")}, "reword")
        report = self.tmp / "check.json"
        output = self.assertExit(1, self.run_cli("check", "demo", "--json", str(report)))
        self.assertIn("FAIL (strict)", output)
        self.assertIn("brand/hosting", output)
        self.assertIn("The pin stays where it is", output)
        result = json.loads(report.read_text())
        self.assertEqual(result["status"], "fails")
        self.assertEqual(result["candidate"], broken)
        self.assertIn("brand/hosting", result["failed_strict"])
        self.assertIn("tripwire", result["failed_strict"])

        pinned, before = fork_file.read_bytes(), self.tree()
        self.assertExit(1, self.run_cli("bump", "demo", "--to", broken))
        self.assertEqual(pinned, fork_file.read_bytes(), "the pin must not move")
        self.assertEqual(before, self.tree(), "the output must not change")

    def test_passing_candidate_is_proposed_then_adopted_by_bump(self):
        pin = self.upstream.commit(BASE_FILES)
        fork_file = self.write_fork(self.fork(pin, [DROP_BRAND]))
        self.assertExit(0, self.run_cli("sync", "demo"))
        self.upstream.commit({"other/unrelated.txt": "still untracked\n"}, "unrelated")
        self.assertExit(3, self.run_cli("check", "demo"))  # a commit outside the tracked paths isn't a candidate

        good = self.upstream.commit({"skills/demo/new.md": "New page.\n"}, "add page")
        output = self.assertExit(0, self.run_cli("check", "demo"))
        self.assertIn(f"tooling/skill-sync/skill-sync bump demo --to {good}", output)
        self.assertIn("add page", output)
        self.assertFalse((self.root / "skills/demo/new.md").exists(), "check never writes the repository")

        self.assertExit(0, self.run_cli("bump", "demo", "--to", good))
        self.assertIn(good, fork_file.read_text())
        self.assertEqual(self.out("new.md"), "New page.\n")
        self.assertIn(good, self.out("UPSTREAM.md"))
        self.assertExit(0, self.run_cli("verify"))
        self.assertExit(3, self.run_cli("check", "demo"))

    def test_check_refuses_a_ref_that_is_not_ahead_of_the_pin(self):
        old = self.upstream.commit(BASE_FILES)
        pin = self.upstream.commit({"skills/demo/new.md": "x\n"})
        self.write_fork(self.fork(pin))
        output = self.assertExit(1, self.run_cli("check", "demo", "--to", old))
        self.assertIn("not a descendant of the pin", output)


class FeatureRules(CliTest):
    def test_failed_feature_rule_leaves_out_its_feature_and_dependents(self):
        files = dict(BASE_FILES)
        files["skills/demo/SKILL.md"] += "\nfancy one\nfancy two\nfancier\n"
        sha = self.upstream.commit(files)
        rules = [
            DROP_BRAND,
            {"id": "fancy/one", "type": "feature", "feature": "fancy", "file": "skills/demo/SKILL.md",
             "anchor": "fancy one", "replacement": "FANCY ONE", "reason": "x"},
            {"id": "fancy/two", "type": "feature", "feature": "fancy", "file": "skills/demo/SKILL.md",
             "anchor": "fancy 2", "replacement": "FANCY TWO", "reason": "drifted anchor"},
            {"id": "fancier", "type": "feature", "feature": "fancier", "file": "skills/demo/SKILL.md",
             "anchor": "fancier", "replacement": "FANCIER", "reason": "x"},
        ]
        features = {"fancy": {"description": "d"}, "fancier": {"description": "d", "requires": ["fancy"]}}
        self.write_fork(self.fork(sha, rules, features=features))
        output = self.assertExit(0, self.run_cli("sync", "demo"))
        self.assertIn("LEFT OUT", output)
        text = self.out()
        self.assertIn("Use any host.", text, "strict rules still apply")
        self.assertIn("fancy one\n", text, "a left-out feature keeps none of its edits")
        self.assertIn("fancier\n", text, "a feature requiring a left-out one is left out too")
        provenance = self.out("UPSTREAM.md")
        self.assertIn("`fancy`: rule fancy/two no longer applies", provenance)
        self.assertIn("`fancier`: requires left-out feature fancy", provenance)

        # The same drift is reported by check, and a candidate with left-out features still passes.
        self.upstream.commit({"skills/demo/extra.md": "x\n"})
        output = self.assertExit(0, self.run_cli("check", "demo"))
        self.assertIn("Features left out", output)

    def test_strict_rule_failure_is_never_downgraded(self):
        sha = self.upstream.commit(BASE_FILES)
        rules = [dict(DROP_BRAND, anchor="missing"),
                 {"id": "fancy", "type": "feature", "feature": "fancy", "file": "skills/demo/SKILL.md",
                  "anchor": "missing too", "replacement": "", "reason": "x"}]
        self.write_fork(self.fork(sha, rules, features={"fancy": {"description": "d"}}))
        output = self.assertExit(1, self.run_cli("sync", "demo"))
        self.assertIn("FAIL (strict)", output)


class Tripwire(CliTest):
    def test_terms_trip_case_insensitively_outside_the_allowlist(self):
        sha = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(sha, [], tripwire=TRIPWIRE))
        output = self.assertExit(1, self.run_cli("sync", "demo"))
        self.assertIn("skills/demo/SKILL.md:3: 'BrandCo'", output)
        self.assertNotIn("LICENSE:", output, "the whole-file allowlist entry covers LICENSE")
        self.assertFalse((self.root / "skills/demo").exists())

        self.write_fork(self.fork(sha, [DROP_BRAND], tripwire=TRIPWIRE))
        self.assertExit(0, self.run_cli("sync", "demo"))

    def test_allowed_literal_masks_only_itself(self):
        files = dict(BASE_FILES)
        files["skills/demo/SKILL.md"] = "Install: brew install brandco/tap/demo\n"
        sha = self.upstream.commit(files)
        tripwire = dict(TRIPWIRE, allow=TRIPWIRE["allow"] + [
            {"paths": ["skills/demo/**"], "literal": "brandco/tap/demo", "reason": "real install path"}])
        self.write_fork(self.fork(sha, [], tripwire=tripwire))
        self.assertExit(0, self.run_cli("sync", "demo"))

        sha = self.upstream.commit({"skills/demo/SKILL.md": files["skills/demo/SKILL.md"] + "Ask BRANDCO support.\n"})
        self.write_fork(self.fork(sha, [], tripwire=tripwire))
        output = self.assertExit(1, self.run_cli("sync", "demo"))
        self.assertIn("SKILL.md:2: 'BRANDCO'", output)
        self.assertNotIn("SKILL.md:1:", output)


class AtomicOutput(CliTest):
    def test_failed_sync_leaves_output_byte_identical_and_no_temp_dirs(self):
        sha = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(sha, [DROP_BRAND], tripwire=TRIPWIRE))
        self.assertExit(0, self.run_cli("sync", "demo"))
        (self.root / "skills/demo/stale.txt").write_text("stale\n")
        before = self.tree()

        self.write_fork(self.fork(sha, [], tripwire=TRIPWIRE))  # now the tripwire fires
        self.assertExit(1, self.run_cli("sync", "demo"))
        self.assertEqual(before, self.tree())
        self.assertEqual(sorted(os.listdir(self.root / "skills")), ["demo"])

    def test_successful_sync_replaces_the_whole_folder(self):
        sha = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(sha, [DROP_BRAND]))
        self.assertExit(0, self.run_cli("sync", "demo"))
        (self.root / "skills/demo/stale.txt").write_text("stale\n")
        mode = (self.root / "skills/demo/SKILL.md").stat().st_mode
        self.assertExit(0, self.run_cli("sync", "demo"))
        self.assertFalse((self.root / "skills/demo/stale.txt").exists())
        self.assertEqual(mode, (self.root / "skills/demo/SKILL.md").stat().st_mode)
        self.assertEqual(sorted(os.listdir(self.root / "skills")), ["demo"])
        self.assertEqual(sorted(os.listdir(self.root / "skills/demo")),
                         ["LICENSE", "SKILL.md", "UPSTREAM.md", "references"])

    def test_sync_recovers_from_an_interrupted_swap(self):
        sha = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(sha, [DROP_BRAND]))
        self.assertExit(0, self.run_cli("sync", "demo"))
        # Killed between the two renames: the old folder is parked, the new one never landed.
        os.rename(self.root / "skills/demo", self.root / "skills/.demo.skill-sync-old-dead")
        (self.root / "skills/.demo.skill-sync-new-dead").mkdir()
        self.assertExit(1, self.run_cli("verify"))
        self.assertExit(0, self.run_cli("sync", "demo"))
        self.assertEqual(sorted(os.listdir(self.root / "skills")), ["demo"])
        self.assertExit(0, self.run_cli("verify"))

    def test_verify_commands_cannot_write_into_the_output(self):
        sha = self.upstream.commit(BASE_FILES)
        scribble = {"name": "scribble", "argv": ["{python}", "-c", "open('skills/demo/junk.txt', 'w').write('x')"]}
        self.write_fork(self.fork(sha, [DROP_BRAND], verify=[scribble]))
        self.assertExit(0, self.run_cli("sync", "demo", "--checks", *VERIFY_FLAGS))
        self.assertFalse((self.root / "skills/demo/junk.txt").exists())


class VerifyCatchesHandEdits(CliTest):
    def setUp(self):
        super().setUp()
        sha = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(sha, [DROP_BRAND]))
        self.assertExit(0, self.run_cli("sync", "demo"))
        self.assertExit(0, self.run_cli("verify"))

    def test_edited_file(self):
        path = self.root / "skills/demo/SKILL.md"
        path.write_text(path.read_text().replace("Use any host.", "Use my host."))
        output = self.assertExit(1, self.run_cli("verify"))
        self.assertIn("skills/demo/SKILL.md: content differs", output)
        self.assertIn("+Use my host.", output)
        self.assertExit(0, self.run_cli("sync", "demo"))
        self.assertExit(0, self.run_cli("verify"))

    def test_added_removed_and_mode_changes(self):
        (self.root / "skills/demo/notes.md").write_text("mine\n")
        (self.root / "skills/demo/references/more.md").unlink()
        os.chmod(self.root / "skills/demo/SKILL.md", 0o755)
        output = self.assertExit(1, self.run_cli("verify", "--json", str(self.tmp / "v.json")))
        self.assertIn("notes.md: not generated", output)
        self.assertIn("references/more.md: missing", output)
        self.assertIn("SKILL.md: executable bit differs", output)
        self.assertFalse(json.loads((self.tmp / "v.json").read_text())["ok"])

    def test_edited_provenance_and_leftover_temp_dir(self):
        (self.root / "skills/demo/UPSTREAM.md").write_text("trust me\n")
        (self.root / "skills/.demo.skill-sync-new-1234").mkdir()
        output = self.assertExit(1, self.run_cli("verify"))
        self.assertIn("UPSTREAM.md: content differs", output)
        self.assertIn("leftover from an interrupted sync", output)


class Watch(CliTest):
    def test_reports_commits_and_diffs_for_tracked_files_only(self):
        pin = self.upstream.commit(BASE_FILES)
        fork = {"name": "notes", "mode": "watch", "local": "skills/notes",
                "upstream": {"repo": self.upstream.url, "branch": "main", "sha": pin},
                "files": [{"path": "skills/demo/SKILL.md", "note": "ported as SKILL.md"}]}
        fork_file = self.write_fork(fork)
        self.assertExit(0, self.run_cli("watch", "notes"))
        self.upstream.commit({"other/unrelated.txt": "changed\n"}, "unrelated")
        self.assertExit(0, self.run_cli("watch", "notes"))

        head = self.upstream.commit({"skills/demo/SKILL.md": BASE_FILES["skills/demo/SKILL.md"] + "New advice.\n"},
                                    "add advice")
        output = self.assertExit(1, self.run_cli("watch", "notes", "--json", str(self.tmp / "w.json")))
        self.assertIn("add advice", output)
        self.assertIn("+New advice.", output)
        self.assertNotIn("unrelated", output)
        self.assertIn(f"skill-sync bump notes --to {head}", output)
        self.assertEqual(json.loads((self.tmp / "w.json").read_text())["status"], "changes")

        # After porting by hand, bump acknowledges the commits; watch is quiet again.
        self.assertExit(0, self.run_cli("bump", "notes", "--to", head))
        self.assertIn(head, fork_file.read_text())
        self.assertExit(0, self.run_cli("watch", "notes"))
        self.assertExit(2, self.run_cli("sync", "notes"))  # watch forks have no generated output

    def test_deleted_upstream_file_counts_as_a_change(self):
        pin = self.upstream.commit(BASE_FILES)
        self.write_fork({"name": "notes", "mode": "watch",
                         "upstream": {"repo": self.upstream.url, "branch": "main", "sha": pin},
                         "files": [{"path": "skills/demo/references/more.md"}]})
        self.upstream.commit({"skills/demo/references/more.md": None}, "drop page")
        output = self.assertExit(1, self.run_cli("watch", "notes"))
        self.assertIn("MISSING at head", output)


class Definitions(CliTest):
    def test_bad_definitions_are_usage_errors(self):
        sha = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(sha, [dict(DROP_BRAND, anchr="typo")]))
        self.assertIn("unknown key(s) anchr", self.assertExit(2, self.run_cli("sync", "demo")))
        self.write_fork(self.fork(sha[:12]))
        self.assertIn("full 40-character", self.assertExit(2, self.run_cli("sync", "demo")))
        self.write_fork(self.fork(sha, [dict(DROP_BRAND, type="feature", feature="nope")]))
        self.assertIn("unknown feature", self.assertExit(2, self.run_cli("sync", "demo")))
        self.write_fork(self.fork(sha, paths=[{"upstream": "skills/demo", "output": "../escape"}]))
        self.assertIn("normalized", self.assertExit(2, self.run_cli("sync", "demo")))

    def test_missing_upstream_path_fails_the_build(self):
        sha = self.upstream.commit(BASE_FILES)
        self.write_fork(self.fork(sha, paths=[{"upstream": "skills/absent", "output": "skills/demo"}]))
        self.assertIn("does not exist upstream", self.assertExit(1, self.run_cli("sync", "demo")))

    def test_broken_relative_link_fails_when_links_are_checked(self):
        sha = self.upstream.commit(BASE_FILES)
        rule = {"id": "drop-run", "kind": "remove-section", "file": "skills/demo/SKILL.md", "anchor": "## Run",
                "reason": "x"}
        self.write_fork(self.fork(sha, [rule], links=True))
        output = self.assertExit(1, self.run_cli("sync", "demo"))
        self.assertIn("../SKILL.md#run names no heading", output)


if __name__ == "__main__":
    unittest.main()
