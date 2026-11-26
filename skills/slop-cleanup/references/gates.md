# Gates

Every rule names the fault it catches. Rules with a legacy backlog enter through the
finding-fingerprinted baseline, ranked by production path versus test path, and burn down one rule
or subsystem at a time. A heuristic rule blocks only after a labeled sample on the target stack
shows fewer than 10% "not useful" hits; above that it is narrowed, made a warning, or turned off.

## Lint templates (pinned explicit rule lists, no floating presets)

| Template | Tool (tested version) | What it holds |
| --- | --- | --- |
| [`ts-node/.oxlintrc.json`](../templates/ts-node/.oxlintrc.json) and [`slop-rules.mjs`](../templates/ts-node/slop-rules.mjs) | oxlint 1.95.0 `--type-aware` with oxlint-tsgolint 7.0.2008 and TypeScript 7.0.2; `eslint-plugin-jsdoc` 65.5.5 (with `eslint` 10.15.0 installed as its library) | correctness category; security sinks; `no-floating-promises`, `no-misused-promises`, `await-thenable`, with `node:test` registrations allowed; `no-explicit-any` and `no-unsafe-*` on `server/` and `shared/` (warn on `client/`); `ban-ts-comment` with a description; unsafe string and operand rules; switch exhaustiveness; `unbound-method`; React hooks; keyboard and screen-reader `jsx-a11y` rules; `import/no-cycle`; a browser-to-server `no-restricted-imports` layer rule; `no-console`; self-compare and constant binary expressions; `no-control-regex`; `no-unused-expressions`; `jsdoc-js/check-param-names`; and three local rules: `slop/no-focused-test` (`test.only`, `describe.only`, `{ only: true }`) and the Drizzle `slop/delete-needs-where` and `slop/update-needs-where`. `no-unnecessary-condition` and the React compiler rules are warnings |
| [`ts-node/knip.json`](../templates/ts-node/knip.json) | knip 6.48.0 | real entry points; `unlisted` and `unresolved` always fail; unused files, exports and dependencies go through the baseline |
| [`python-node/ruff.toml`](../templates/python-node/ruff.toml) and [`tests/ruff.toml`](../templates/python-node/tests/ruff.toml) | ruff 0.17.6 | `F E9 B PLE PLR0124 PLW0129 E722 BLE001 S110 ARG RUF006 ASYNC RUF100 PGH003 PGH004 T20 LOG G TID251`; `time.sleep` banned in tests (a real-time or absence wait keeps `# noqa: TID251` with its reason) |
| [`python-node/pyrightconfig.json`](../templates/python-node/pyrightconfig.json) | pyright 1.1.414 | `standard` plus unused and unnecessary checks as errors, ratcheting to `strict` per package |
| [`python-node/pyproject-tools.toml`](../templates/python-node/pyproject-tools.toml) | pydoclint 0.12.0, deptry 0.25.1 | merged into `pyproject.toml`: pydoclint on existing docstrings only; deptry with `DEP001` always failing |

Edit on adoption: the layer paths in the oxlint overrides; an override that turns `no-console` off
for the repository's own logger module; `logger-objects` in `ruff.toml` (the repository's logger
objects, module path then name); the Drizzle rules' `drizzleObjectName`, or both rules removed in a
repository that does not use the Drizzle query builder. Add `"noUnusedLocals": true` and
`"noUnusedParameters": true` to `tsconfig.json`.

Run the lint tools with `--report-unused-disable-directives-severity=error` (oxlint) or `RUF100`
(Ruff), so an orphaned suppression fails. Advisory scans run with `--exit-zero` or as reports: Ruff
`ERA001`/`SIM`/`PLW`, `vulture --min-confidence 80`, and `jscpd`.

Left out on purpose: docstring and JSDoc presence rules, prose style linters, a second linter per
slot, complexity and length limits, `exactOptionalPropertyTypes`, GPL-licensed analyzers,
repository-wide mutation or coverage gates, and network-dependent checks as merge gates.

## slop-check rules

| Check | Rule | Severity | Baselined |
| --- | --- | --- | --- |
| docs | `docs/refs`: a local link or definition target, a reference link with no definition, or a path in a code span or prose that does not resolve | error | yes |
| docs | `docs/history`: "previously … now" (also across one sentence break into "Now …"), "was/were renamed, replaced, removed or rewritten", "used to <verb>" except after is/are/was/were (not), or a heading that is a version history, outside `history_exclude` | error | yes |
| docs, comments | `*/history-ambiguous`: "no longer", "deprecated", "replaced by", "as before", round or lane labels, "has been removed" and similar | warning | no |
| docs, comments | `*/markers`: a `TODO`/`FIXME` whose own parentheses or clause carry no tracker reference | `[comments] markers`, warning until calibrated | yes |
| comments | `comments/refs`: a comment names a path that resolves nowhere (its folder, an ancestor, the root, or a unique tail) | error | yes |
| comments | `comments/suppression`: every inline suppression directive (`eslint-disable*`, `oxlint-disable*`, `/* eslint rule: … */`, `@ts-ignore`, `@ts-expect-error`, `@ts-nocheck`, `noqa`, `type: ignore`, `pyright:` other than `strict`, `ruff: noqa`) | error | yes: each must be a baseline row |
| comments | `comments/unexplained-suppression`: no `-- reason`, description or rule codes | error | yes |
| comments | `comments/history` (history wording in code comments) | warning | no |
| logs | `logs/dynamic-event`, `logs/secret-key`, `logs/contract` ([logging](logging.md)) | error | no |
| schema | `schema/missing-table`, `missing-column`, `nullability`, `undeclared-column`, `undeclared-table`, `duplicate-column`, `duplicate-revision`, `broken-chain`, `cycle`, `heads`, `unimported-model`, `model-import`, `row-identity` ([data model](data-model.md)) | error | no |
| schema | `schema/no-primary-key` | warning, or error with `require_physical_primary_key` | no |

Fingerprints bind a finding to its content: the sentence for history and markers, the enclosing
function plus the directive and the line it silences for suppressions, and the enclosing function
plus the source line (or the tool's own identifier) for tool findings. Never a line number.

Warnings print only with `--warnings` and never fail. These are incomplete (exit 3), never a pass:
a missing declared root; code outside every declared root and exclude; a missing, malformed or
stale input (contract, catalog, report, baseline row); a file the parsers cannot read; a run with
`--only` (a partial run is never a pass); and an internal error. Unknown config sections or keys,
unknown checks and options are usage errors (exit 2). Symbolic links are never followed, and paths
resolve case-exactly inside the tree.

Globs: `dir/**` covers everything under `dir`, and `vendor/*/**` covers extracted packages while
leaving `vendor/README.md` in scope.

## The baseline

Sorted JSON lines in `[baseline] dir`, one file per tool:

```json
{"tool":"ruff","rule":"E722","path":"app/mod.py","fingerprint":"f :: except:","count":1,"reason":"..."}
```

A finding beyond its row's count is `new`; a row with fewer findings than its count is `stale` and
fails until the baseline shrinks, including when its file is deleted. A row needs a positive integer
`count`; a malformed or duplicate row is incomplete. `check` never writes the baseline; `baseline
--write` does, from a full run only, keeping each row's `reason`, and never writes rules that always
fail (knip `unlisted`/`unresolved`, deptry `DEP001`).

Tool reports come from `[baseline.reports]`: `ruff` (`--output-format json`), `oxlint` (`-f json`),
`pyright` (`--outputjson`), `knip` (`--reporter json`), `deptry` (`--json-output`), and `pydoclint`
(its text output with `2>&1`, run with no native baseline). Beside each report sits `REPORT.exit`,
the tool's exit status from the same run: 0 must come with no error findings and 1 with some;
anything else, a missing status, or an unreadable report is incomplete. A report written from a
sub-folder sets `cwd`:

```toml
[baseline.reports.oxlint]
file = ".slop/reports/oxlint.json"
cwd = "frontend"
```

## Policy comparison

`slop-check policy --base-policy-dir <dir>` compares the candidate's policy files with a read-only
snapshot of the protected copy. It needs no `.git`. Make the snapshot from the gate commit (phase 5),
or from the protected branch once the gates have merged:

```sh
mkdir snapshot && git archive <revision> cleanup.toml .slop/baseline log-contract.json <the other policy paths> | tar -x -C snapshot
```

- Passes: removed rows, lower counts, `reason`-only edits, contract additions (reported for review),
  and a single-file rename whose rows reappear unchanged, row for row and count for count, at one new
  path while the old path is gone (reported `moved`).
- Weakening (exit 1): an added fingerprint, a higher count, a new suppression row, any edit to a
  config, `cleanup.toml` or allowance file (in a `pyproject.toml`, an edit to a `[tool.*]` table), an
  added or removed policy file, a removed contract event or field, a field made optional or allowed
  through a spread, and any tool configuration the linters would discover (`.oxlintrc.json`,
  `tsconfig*.json`, `ruff.toml`, `pyrightconfig.json`, a `pyproject.toml` with lint tables, and
  similar) that is not a declared policy path.
- Incomplete (exit 3): no snapshot, a snapshot with no `cleanup.toml`, or a snapshot lacking one of
  its own declared policy paths.

Accepting weakening takes the owner's signed approval.

## Adopting it in a repository

The skill lives in one place (`<skill>`); the repository is `--root`. Nothing from the skill is
copied into the repository except the templates.

1. Install the parsers once per checkout of the skill: run `<skill>/scripts/slop-check`; if it says
   the parsers are missing, run the `(cd … && npm ci)` command it prints.
2. Copy a template's files to the repository root (for a Python backend with a Node frontend, the
   TypeScript files go under the frontend's folder) and fill in `cleanup.toml`. List every root, the
   frontend's entry points in `knip.json`, every schema file, and every config a tool reads as a
   policy path. Vendored package tarballs stay build and browser inputs; only their extracted source
   is skipped.
3. Add `.slop/reports/` to `.gitignore`; commit `.slop/baseline/` (`baseline --write` always writes
   `slop-check.jsonl`, empty when there are no rows).
4. Put `test-audit`'s authoring gate in the repository's agent instructions (`AGENTS.md`), and add a
   lane item that reads backend routes against frontend callers (and the OpenAPI document if one
   exists).
5. Wire CI in this order, each step writing fresh output:

```sh
R=.slop/reports; mkdir -p $R
ruff check --output-format json . > $R/ruff.json; echo $? > $R/ruff.json.exit
pyright --outputjson > $R/pyright.json; echo $? > $R/pyright.json.exit
pydoclint app > $R/pydoclint.txt 2>&1; echo $? > $R/pydoclint.txt.exit       # pydoclint reports on stderr
deptry . --json-output $R/deptry.json > /dev/null; echo $? > $R/deptry.json.exit
(cd frontend && npx oxlint --type-aware --report-unused-disable-directives-severity=error -f json src > ../$R/oxlint.json; echo $? > ../$R/oxlint.json.exit)
(cd frontend && npx knip --reporter json > ../$R/knip.json; echo $? > ../$R/knip.json.exit)
alembic upgrade heads                      # or the Drizzle migrate command, on a disposable database
<skill>/scripts/slop-check check --root . --catalog-command "psql -X -At -v ON_ERROR_STOP=1 $DATABASE_URL"
<skill>/scripts/slop-check policy --root . --base-policy-dir snapshot
```

Delete `.slop/reports/` before the run, so a tool that fails to start leaves no old report behind.
The same `$DATABASE_URL` serves the migration and the dump, so the catalog comes from the database
the migrations just ran on.

As named checks in a CI job table: `docs` and `comments` declare the whole tree as input (`"*"`), so
deleting a referenced source file invalidates a cached pass. `logs` and `schema` declare their roots,
contract and migrations. `clones`, `vulture` and `inventory` stay advisory.

## Known limits

Not covered; review these by hand where they matter.

- A unique index that is partial (`WHERE …`), invalid, or built on an expression still counts as row
  identity.
- A rule weakened in a config reached only through `extends` is caught only if that file is itself a
  declared policy path.
- A symbolic link is never followed, so a symlinked baseline folder reads as empty and a link that
  leaves the tree is invisible.
- Baseline `.jsonl` files in subfolders of the baseline folder are read like the top-level ones.
- In `pyproject.toml`, lint tables are recognized only as unindented `[tool.ruff…]`-style or
  `[[tool.…]]` headers; an indented header or `ruff.…` dotted keys under a bare `[tool]` are not.
- The Alembic applied-heads check ignores `depends_on` edges across branches; same-named tables in two
  schemas are compared within each schema only when both schemas are listed in `[schema] schemas`.
- A rename into an existing file that had no baseline rows can be accepted as a move.
- A tool finding with no source location is fingerprinted by its message alone.
- Aliased test registrations (`const spec = test; spec.only(…)`) and database writes passed through
  another function are not seen by the local oxlint rules.
- Source folders named `venv`, `.venv`, `node_modules`, `.slop` or `__pycache__` are never scanned.
- An aliased Drizzle table import (`su.nextDueAt`) is not an ORM reference in the inventory.
- An explanatory comment between a suppression directive and its statement changes the suppression's
  fingerprint to that comment line.
