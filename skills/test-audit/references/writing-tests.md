---
summary: "Behavioral integration proof, test value and cost, and regression authoring"
title: "Writing and adding tests"
read_when:
  - You are writing a new test
  - You are adding a regression for a provider bug
---

## Prove behavior at the owning boundary

Prove a caller-visible contract through its production owner with fixed external
I/O fixtures and independent expected values. Prefer focused integration tests
and independent unit contracts over mocked internal decisions or whole-product
boots. Name the credible regression and what the test cannot prove.

Work in vertical slices: failing behavior, coherent repair, passing proof.
Regressions must fail on the original defect, not fixture setup; report unavailable
baseline proof. Refactor under green tests and preserve distinct contracts when
consolidating coverage. Use synthetic content and keep credentials out of captures.

Apply the [test value gate](../SKILL.md)
and [proof policy](testing-policy.md#proof-policy).
For requests, paid calls, recovery, defaults, or performance claims, read the
[scenario contracts](boundary-scenarios.md#scenario-contracts) before
changing code or proof. Too many private controls suggest fragmented ownership,
not a need for test-only exports.

## Cost budget

Measure every new or changed test file by running it alone with a single worker
(`scripts/test-audit run <file>` reports wall time); report wall time and CI
seconds once available; PRs state both.

- Seconds-long tests must prove a contract no cheaper layer can. Long end-to-end
  compositions belong in the release-only tier.
- Target under 5 seconds of test time per file. Above 30 seconds, explain the
  contract and why no cheaper layer proves it.
- Split files exceeding CI job budgets by owner, or consider moving them to a
  release-only tier if the repo has one. Weigh PR risk against release detection;
  keep coverage without duplicate release tests. Preserve assigned configs and
  process/timer policies; product E2E/live stays separate.
- Inject clocks; do not add real timers, sleeps, or polling. Claim ports through
  the repo's shared port allocator or let the OS assign one (never hard-code
  ports), isolate each file's state, and reuse suite-level server/process
  fixtures; never boot one per test when a suite-level fixture exists. Import
  narrow APIs, not broad barrels. Fix shared state; do not add serial configs
  or worker pins.
- Async E2E waits: produced state, never action return.
- For subprocess fixtures, expensive one-time preparation, and timeouts, follow
  the [subprocess and timeout recipes](fixtures.md#subprocesses-preparation-and-timeouts).
- Python (pytest): single worker means `-n 0` when pytest-xdist is active
  (it overrides worker counts set in `addopts`), otherwise no `-n`; suite-level
  fixtures are `scope="session"` or `"module"`; import from the owning module,
  not package `__init__` re-exports; inject clocks instead of `time.sleep`;
  bind port 0.

## Test Temp Directories

Use [temporary directory and executable fixture recipes](fixtures.md#test-temp-directories)
for shared cleanup, bare-directory exceptions, and close-on-exec copies.

## Agent reliability evals (skills)

For skill selection, compliance, and multi-turn workflow evals, use the
[fixture guide](fixtures.md#agent-reliability-evals-skills).

## Module mocks and export completeness

Follow the [mock recipe](fixtures.md#module-mocks-and-export-completeness)
for export preservation (pytest: `autospec`), state-isolating mocks,
annotations, and the shrinking baseline.

## Raw SQLite state access

Before raw database reads or file snapshots, follow the
[async close contract](fixtures.md#raw-sqlite-state-access).

## Background workers and watchers

Close background workers and isolate passive refresh as described in
[worker cleanup](fixtures.md#background-workers-and-watchers).

## Flake triage

Follow the canonical
[test failure policy](testing-policy.md#test-failure-policy).
Reproduce the original shard order before running alone; order-only failures
usually mean earlier-file shared-state leaks. Classify fixture state, missing
completion signals, and product races. Repair the producer. If a removal caused
the red, read its PR's accepted tradeoffs before restoring it. Choose repeat
counts for the reproduced failure mode, not a fixed clean-run quota.

When a DOM test leaves state behind after fixture teardown (leftover body
children, body or HTML styles/classes, pointer captures, global listeners, fake
timers), fix the fixture or resource owner rather than adding an ignore list:
unmount owned render roots, dispose non-rendering owners, and restore
document-wide state the fixture changed.

## Adding regressions (guidance)

Pass bounded synthetic inventories and fixed timing through the real planner or
scheduler for capacity bugs. Preserve coverage, ownership, and execution-budget
assertions; keep coverage that reads the real repository checkout in its
existing integration tests.

For live provider bugs, prefer a CI-safe regression at the smallest production
composition reaching the defect: real adapters with captured requests, server-level
integration for session/history/tool pipelines, or independent parser unit
contracts. Keep inherently live-only checks narrow and opt-in.

When a guard test samples one entry per registry class, each new class needs
classification in that test so the guard cannot silently skip it; see the
[registry-driven guard contract](boundary-scenarios.md#registry-driven-guard-contract).
