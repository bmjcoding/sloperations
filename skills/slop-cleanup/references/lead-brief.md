# The lead's brief for a lane worker

Copy this, fill the angle brackets, and send one per subsystem. Workers scan and report; they do
not edit until the lead has verified their rows and assigned a fix batch.

---

You are sweeping **<subsystem>** (`<paths>`) of **<repository>** at base commit `<sha>` for a
behavior-neutral cleanup. Read `cleanup.toml` and the repository's agent instructions first.

**Already known (mechanical findings for your paths):**
<paste the relevant slop-check, compiler, dead-code and jscpd rows>

**Report, one row per finding:** kind, location (`path:line`), evidence, proposed action, risk. Do
not edit files. For each kind, meet the evidence standard
(`skills/slop-cleanup/references/evidence.md`):

- Dead code: show the name and string searches you ran (dynamic imports, configs, tests, docs) and
  that the dead-code tool agrees with real entry points.
- Duplicates: say whether the copies behave the same, or list the inputs where they differ.
- Shallow or fragmented modules: apply `codebase-design`'s deletion test, list callers and name the
  friction. These are escalations, not fixes.
- Tests: use `test-audit`'s ledger: protected behavior, credible fault, remaining proof. Classify
  every wait (condition, bounded poll, time or absence).
- Comments: say whether each stale comment states a live invariant or workaround; quote the code
  that proves it stale. Read each comment and docstring against the code below it, and each linked
  doc against the code it describes, for claims the code contradicts (a model may help; its output
  is a candidate list, never a finding until you quote the claim and the contradicting line).
- Docs: quote the sentence and the code it contradicts, or the path that does not exist.
- Schema and logs: cite the `slop-check inventory` row or the log call; never propose a drop or a
  rename as a cleanup fix.
- Naming: check terms against the glossary (`CONTEXT.md`, `domain-modeling`).

**Leave alone, and list as "left, with reason"** when verified: the intentional-code list in
`SKILL.md`.

**Stop and say so** when evidence is below the standard, two readings differ in behavior, or the
work touches files an open PR is changing.

**When you fix (after assignment):** one commit per concern; run typecheck, lint and only the
tests covering your batch, one heavy run at a time; quote equivalence-run results in the commit
message; add a test only through `test-audit`'s authoring gate with a fault probe. Finish with the
commands you ran and their results.
