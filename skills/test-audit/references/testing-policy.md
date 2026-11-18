# Testing policy

## Proof policy

- **PR CI = default broad proof.** Change builds + focused tests pass → open
  ready PR. CI runs broad suites; never delay PR for pre-PR suite runs.
- **Build unavailable before publication:** when publication is already authorized,
  remote capacity is unavailable, and local execution is unqualified, push/open a
  draft PR with the exact missing build and capacity gap recorded. Defer that build
  to exact-head CI. Require its success before marking ready or merging. This
  changes proof timing only, not publication authority, source trust, credentials,
  owner approval, or merge gates. If CI skips drafts, trigger it manually for the
  full pushed head SHA; verify the run checked out that SHA and the job covering
  the build succeeded. A skipped job or a run on another SHA is not build proof.
  Keep uncovered proof pending. Link the run in the PR, then use normal ready-PR
  CI and landing gates.
- **Before PR, prove narrowly:** touched tests run one file at a time with a
  single worker (`scripts/test-audit run <file>`), targeted typecheck, lint,
  format. Prefer local proof for trusted, bounded work when dependencies and
  current host capacity are qualified. Local execution is optional: a focused command
  can still load a large compiler graph or expand to broad lanes.
- **Choose remote proof deliberately.** Use PR CI for broad proof; remote test
  machines, if the repo provides them, may cover expensive checks, a busy or
  unqualified local host, another OS/device, live providers/channels, long E2E,
  or heavy benchmarks. To use `scripts/test-audit` there, run it inside the
  remote checkout. Acquire only when the command is ready and capacity is granted promptly. If
  queued, stop only the task-owned allocation; use PR CI or a safe local scope. Keep uncovered
  platform-specific proof pending. Never force heavy work onto a busy machine
  or chain providers. Use the smallest profile proven for the selected workload.
- **Never rerun or re-push just for green.** Fix real failures; read available
  CI failure classification before attributing red.

- Prove changed contract, complete required gates, finish. Broaden/repeat only for
  changed inputs, failures, or named unresolved risk.
- Vite+ projects are Vitest projects (`vp test`); `vp check` runs the enabled
  Oxfmt and Oxlint steps, and type-checks only when the Vite+ lint options
  `typeAware` and `typeCheck` are on. Oxlint belongs in the lint step only; it
  does not replace project-wide dead-code analysis (knip).
- Follow scoped agent instructions (such as `AGENTS.md`); authoring/reviewing tests: use
  [boundary guide](writing-tests.md#prove-behavior-at-the-owning-boundary)
  and [test-audit](../SKILL.md).

## Test failure policy

- Failures = defects. Bounded reproduction attempt (same shard order first);
  identify owner; fix product, fixture, shared-state, ordering bugs.
- Proven repair: add regression; cite another owner's fix when applicable.
- Safe fix cannot be established/completed: record original failure, attempts,
  evidence, uncertainty in PR; continue under normal CI/review gates.
- Unresolved failure alone neither blocks landing nor requires extra approval.
  Passing replay ≠ proven fix.
- Never hide failures with retries, longer timeouts, weaker assertions, broader
  mocks, altered baselines.
