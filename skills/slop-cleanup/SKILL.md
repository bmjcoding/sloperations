---
name: slop-cleanup
description: "Clean up agent-written debt across code, tests, comments, docs, schema and logs: frame, baseline, scan into a ledger, gate first, fix behavior-neutral batches, escalate, verify. Use for a repository-wide cleanup or a subsystem sweep."
---

# Slop cleanup

One procedure for any stack, plus `scripts/slop-check`, the shared deterministic checker (Node 24+,
pinned parsers, and the Python 3.9+ standard library for `.py` files). Composes, never copies:

- `test-audit`: the whole test lane (authoring gate, junk patterns, retention bar, campaign ledger,
  `mutate` fault probes).
- `codebase-design`: the deletion test and seams for fragmented or shallow modules.
- `domain-modeling`: canonical facts and the glossary (`CONTEXT.md`) for naming, redundant columns
  and log field names.
- `improve-codebase-architecture`: structural candidates. The cleanup reports them and does not
  fix them.

References: [evidence](references/evidence.md) · [data model](references/data-model.md) ·
[logging](references/logging.md) · [gates](references/gates.md) · [lead's brief](references/lead-brief.md).
Templates: `templates/ts-node/` and `templates/python-node/` (copy into the repository, then own them).

**Set up once per machine:** run `<skill>/scripts/slop-check`. If it says its parsers are missing,
run the exact `(cd … && npm ci)` command it prints; that needs the npm registry or a mirror of it.
A machine with neither receives the skill folder with `scripts/node_modules` already installed on the
same platform. [Gates](references/gates.md#adopting-it-in-a-repository) has the adoption steps.

## Authority

Every check here is advisory and enforced by review, locally and in CI. `slop-check policy` compares
policy files with a protected snapshot; accepting any weakening takes the owner's signed approval.
Merges are the owner's.

## The eight phases

1. **Frame.** Commit `cleanup.toml` from a template: stacks, source, test and doc roots (each must
   exist), checks, production database dialect and migration heads, `require_physical_primary_key`
   (default false), vendored payloads versus first-party vendor docs, external-repository citation
   prefixes, escalation owner, merge and branch authority. Without it, `slop-check` returns incomplete.
2. **Map work in flight and pick the base.** List open PRs, touched files, branches, worktrees and
   running agents. If feature work is stacked for landing, the cleanup stacks on the top of that
   stack. Record the base commit and its `HEAD^{tree}`.
3. **Baseline at the base commit.** Run every check once. Record pass or fail per check; each
   failing test as an open defect with provenance (once, not twice if a follow-up entry already
   names it); lint findings grouped by production path versus test path; dead code;
   `slop-check check --warnings`; `slop-check inventory`.
4. **Scan into the ledger.** Run the mechanical generators first: compiler unused flags, dead-code
   tools with real entry points, a `jscpd` report, `slop-check`. Then run read-only lane sweeps, one
   worker per subsystem, briefed with the mechanical findings ([lead's brief](references/lead-brief.md)).
   Scan every file, open-PR files included. The lead verifies each candidate against live code
   before it becomes a ledger row (`docs/debt-log.md` for the run: kind, location, evidence,
   proposed action, risk, status). If feature work is still churning, mark rows `Queued` and stop
   here until a quiet point.
5. **Gates first.** At the quiet point, commit the lint rules, `log-contract.json`, the
   finding-fingerprinted baselines (`slop-check baseline --write`, inline suppressions included)
   and the checks, before any cleanup edit. That gate commit is the protected snapshot for this run;
   the owner approves its policy set as a whole. They guard this branch until they merge.
6. **Fix in owner batches.** One commit per concern, one PR per lane or subsystem, behavior-neutral.
   - Delegates run typecheck, lint and only the tests covering their batch, one heavy run at a time.
   - Consolidations: run a disposable equivalence check and quote its result in the commit message.
     Commit a permanent test only when no owner test covers the behavior, the test passes
     `test-audit`'s authoring gate, and a fault probe shows it fails when the production line breaks.
   - A second reviewer (another model or a person) checks each batch: no deletion dropped sole
     coverage (the remaining proof is named); added or rewritten tests pass the gate and the probe;
     no live boundary or workaround comment went without a proven replacement diagnostic; no doc or
     comment edit added history; no baseline growth or unexplained suppression came in.
7. **Escalate.** Send the owner one batch with options and a conservative default ("leave it, logged
   with the reason"). It holds behavior differences, architecture candidates, schema drops and
   consolidations, log event or field renames, timestamp-field unification, logger invariant fixes,
   and unconfirmed findings. Approved changes land as separate stacked PRs, never inside the
   behavior-neutral cleanup.
8. **Verify, close out, sign off.**
   - Regenerate every input first: rerun the lint tools into `.slop/reports/` (with their exit
     status), and read the migrated database through `--catalog-command`. Then run `slop-check check`
     once on the final tree.
   - **A red required check blocks sign-off.** Record the diff as "blocked on open defect `<id>`"
     until it is fixed and green, or the owner signs a waiver. A ledger row never clears it.
   - Prove the baselines only shrank: `slop-check check` passes, and `slop-check policy
     --base-policy-dir <snapshot of the gate commit>` reports no weakening
     ([recipe](references/gates.md#policy-comparison)).
   - Persist every "left, with the reason" row in the tree, with no new file: a one-line
     present-tense comment at its site, or a `reason` on its baseline or tool-config row.
   - Move the ledger into the final PR description. Close with how the debt entered and which gate
     (or residual risk) stops it recurring. `docs/follow-ups.md` keeps only real open bugs.
   - Workspaces: the cleanup deletes no worktree or branch. It reports this run's released ones,
     and other threads' or unmanaged ones, for the owner to select; it removes only its own
     isolated scratch directories.
   - Landing: fold the cleanup PR into the top feature PR, and each layer down into the base PR.
     Verify that exact merge tree against current `main`; the owner lands the base PR. A feature-only
     tree never lands ahead of its cleanup.

## Evidence standard (detail in [evidence](references/evidence.md))

| Kind | Act only when | Behavior-preserving proof | Always escalate |
| --- | --- | --- | --- |
| Dead code, files, deps | No reference by name or string (dynamic imports, configs, templates, tests, docs); the tool agrees with real entry points | Typecheck, owning tests and build pass; deletion-only diff | Public API; anything loaded by name |
| Duplicated logic | Copies shown identical, or their differences listed | Equivalence run quoted in the commit | Copies that differ on any input |
| Shallow module | `codebase-design` deletion test, caller list, named friction | Same external-interface tests before and after | Structural refactors |
| Weak test or fixed wait | `test-audit` row with protected behavior, credible fault, remaining proof; waits classified | Keeper fails on a fault probe; a replaced wait fails when its condition never comes | Baseline failures (defects, never deletions) |
| Stale comment or doc | Live code contradicts it, or its path or symbol is gone | Rewrite checked against code | Live workaround comments; compatibility promises |
| Data model | [data model](references/data-model.md) | Rolling-safe, owner-approved migrations | Every drop, rename or consolidation |
| Structured logging | [logging](references/logging.md) | Producer sink tests | Any event or field rename |
| Branches, worktrees | Never deleted by the cleanup | Reported for the owner | All of them |

## Intentional code (verify, then leave with a present-tense reason)

- Stable empty references kept for identity (a frozen empty array shared to avoid re-renders).
- Look-alike helpers kept apart on purpose because their inputs or failure modes differ.
- Tuned constants whose values came from measurement.
- Waits that test time or absence itself, with a comment saying so.
- Comments that state a live invariant, a dependency-boundary workaround or an operational hazard.
- Expand-phase columns not yet read, ORM association tables, and columns behind `SELECT *`.
- Paths that cite another repository; vendored payloads and generated output.
- Test-runner registration calls (`node:test` `test`/`describe`), bounded literal event names,
  and log fields a contract lists as arriving through a spread.

## Stop rules

- No `cleanup.toml`, or `slop-check` returns incomplete (exit 3): stop and fix the framing or the input.
- Feature work still churning: stop after phase 4.
- Evidence below the standard, or two options differ in behavior: escalate, do not fix.
- A cleanup edit would change behavior, drop a column, rename a log event or field, or fix a
  logger invariant: separate PR, owner-approved.
- A required check is red at the merge tree: no sign-off.
- `slop-check policy` reports weakening: no sign-off without the owner's signed approval.
- A delegate finding is unverified against live code: it is not a ledger row yet.
- A worktree or branch is not provably this run's own: report it, never delete it.

## slop-check

```sh
S=<skill>/scripts/slop-check; R=<repository root>
$S check --root $R --catalog-command "psql -X -At -v ON_ERROR_STOP=1 $DATABASE_URL"   # every required check
$S check --root $R --only docs,comments            # a quick partial run: never a pass (exit 3 when clean)
$S baseline --write --root $R                      # after an approved change; refuses --only
$S policy --root $R --base-policy-dir <snapshot>   # no .git needed
$S inventory --root $R --catalog-command "…"       # advisory column report
$S catalog-query --root $R                         # the query --catalog-command receives on stdin
```

Exit 0 pass, 1 blocking findings, 2 usage or config error, 3 incomplete (a missing or unusable input,
a partial run, or an internal error). Output is sorted JSON lines. Rules, inputs and CI wiring are in
[gates](references/gates.md).
