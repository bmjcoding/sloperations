# Evidence standard

What must be true before a row in the ledger becomes an edit. The lead checks every delegate
finding against live code first; an unverified finding is a candidate, not a row.

## Ledger row

`kind | location | evidence | proposed action | risk | status`. Status is one of `Queued`,
`Verified`, `Fixed (<commit>)`, `Escalated`, `Left, with reason`, `Defect (<id>)`.

## Dead code, files, exports, dependencies, scripts, CSS

- Search by name and by string: dynamic imports, string-built names, configs, templates, tests,
  docs, CI files, and package scripts.
- The dead-code tool runs with the repository's real entry points configured and committed
  (`knip.json`, `[tool.deptry]`). `vulture` is a candidate generator only.
- Check the vendored or generated relationship: upstream payloads are never edited here.
- Proof: typecheck, the owning tests and the build pass; the diff only deletes.
- Escalate public API exports and anything discovered at run time.

## Duplicated logic

- Show the copies behave the same, or list the exact inputs where they differ. A copy kept apart
  on purpose stays, with a one-line comment saying why.
- Proof: a disposable equivalence run (old against new over representative inputs), quoted in the
  commit message and not committed. A permanent test is added only when no owner test covers the
  behavior; it passes `test-audit`'s authoring gate with written-out expected values, and a fault
  probe shows it fails when the production line breaks.
- Restated rules (the same policy in new words) are not found by `jscpd`. Read for them in lane
  sweeps and list where shared rules live in the repository's agent instructions.

## Tests and waits

- The test lane is `test-audit`, unchanged: its ledger names the protected behavior, a credible
  fault and the remaining proof before any delete.
- Judge tests by oracle quality and remaining proof, not by file. A deletion that leaves a
  behavior uncovered needs a keeper test first.
- A failing test at the baseline is a defect row with provenance, never a deletion. "Predates the
  cleanup" is provenance, not a dismissal; a race under load stays a defect even when isolated
  reruns pass.
- Classify every wait: condition wait, bounded poll, or a wait that tests time or absence (commented).
  A replaced wait must fail when the awaited condition never occurs. Counting sleeps is not a pass
  criterion.

## Comments and docstrings

- Delete line restatements, commented-out code and obsolete rationale.
- Keep comments that state a live invariant, an intentional look-alike, a dependency-boundary
  workaround or an operational hazard, unless a proven diagnostic replaces them. Read the code the
  comment protects before deciding.
- `slop-check` blocks broken references and every inline suppression that is not a baseline row
  (and those with no reason or codes). Untracked `TODO`/`FIXME` is a warning until the repository
  sets `markers = "error"` after a labeled sample; history wording in code comments is a warning for
  the lead to read.

## Reference documentation

- Reference docs (README, AGENTS/CLAUDE, infra and first-party vendor READMEs) describe the
  current state in the present tense. History lives in ADRs, changelogs and git.
- `slop-check` blocks broken local links and paths, and unambiguous narration in reference docs.
  Ambiguous wording ("no longer", "deprecated", round or lane labels) is a warning: read it, and
  keep it when it states current behavior.
- Factual accuracy against code is the sweep's job: check each rewritten sentence against the code.

## Recall and scorecards

- Measure recall only on verified mechanical findings (dead code, unlisted dependencies, broken
  local paths). Zero false positives on external-repository citations and declared vendor or build
  paths.
- Record a matched base-versus-final summary (check verdicts, baseline deltas, ledger counts,
  runtimes) for visibility. Clone counts and sleep counts are reported, never targets.
- Rank lint backlogs by production path versus test path before choosing what to fix.
