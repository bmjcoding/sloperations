---
name: test-audit
description: "Writing/changing/reviewing/sweeping tests: reject duplicate or implementation-coupled proof; remove test-only production seams."
license: MIT. LICENSE.txt has complete terms
---

# Test Audit

- Authoring gates every new/changed test.
- Audits: remove low-value proof in coherent owner batches; never chase deletion counts.
- Whole-subsystem campaign: read [CAMPAIGN.md](references/CAMPAIGN.md) first.

## Authoring gate

Before adding test, answer:

1. Protected observable behavior, invariant, or independent contract?
2. Credible regression that makes it fail?
3. Why existing proof misses it? Each contract: one primary owner at strongest
   boundary. Another layer needs distinct transport/lifecycle risk. Extend
   tables/shared fixtures; consolidate repeated setup.
4. Export, flag, wrapper, injection hook with no production caller needed?
   Move proof to real boundary instead.

- Use [boundary and cost guide](references/writing-tests.md).
- Regressions must fail pre-fix for intended reason, pass after repair; report
  unavailable baseline proof.
- One owner regression covers bug; never replay at every crossed layer.
- Behavior-preserving refactors should not break test.

## Junk patterns

- Reject self-comparisons, copied inventories, source greps,
  implementation/call-shape assertions, duplicate contracts, test-only seams,
  subject-generated expected values, mocks supplying asserted behavior.
- Fixtures must not fabricate owner receipts/order or claim persistence path
  never writes.
- Negative controls must fail at intended guard; names must match exercised behavior.
- Auditing candidates: use [full checklist](references/audit.md#junk-patterns).

## Retention bar

- Keep independent API, SDK, protocol, config, migration, storage, security,
  platform, default, prompt-byte, generated-language, package, release,
  architecture, observable-ordering contracts. Static/slow ≠ disposable.
- Source inspection must survive identifier-only refactors and guard independent
  user-facing contract.
- Baseline failures may be bugs to repair, not tests to delete.

## Scripts

- `scripts/test-audit` works in JS/TS and Python repos with no setup (any
  Python ≥3.9 or uv). Never skip it because the repo has no Python; never
  install it, or anything for it, as a project dependency. See
  [tooling](references/tooling.md).

## Audit and validation

- Before audit edit: read [candidate evidence and workflow](references/audit.md#candidate-evidence);
  inspect full owners/callers/history; record remaining proof before deletion.
- Delete obsolete production seams with tests; never optimize for deletion counts.
- Follow [proof policy](references/testing-policy.md#proof-policy); never edit a
  checkout during an active test run; run owner/sibling proof
  (`scripts/test-audit run`) + changed checks (`scripts/test-audit changed`);
  then get an independent review: the `autoreview` skill if installed, else the
  harness's own review.
- Report causes, retained contracts, proof/cost, production versus test LOC,
  follow-ups. Commit/push/land only with task authority.

## References

- [references/audit.md](references/audit.md): audit workflow, junk-pattern checklist, candidate evidence, validation.
- [references/CAMPAIGN.md](references/CAMPAIGN.md): whole-subsystem test-pruning campaign.
- [references/writing-tests.md](references/writing-tests.md): behavioral integration proof, test value and cost, regression authoring.
- [references/boundary-scenarios.md](references/boundary-scenarios.md): request, paid-work, recovery, default, and measurement proof contracts.
- [references/fixtures.md](references/fixtures.md): temporary directories, module mocks, database cleanup, background workers, subprocess fixtures.
- [references/testing-policy.md](references/testing-policy.md): proof policy and test failure policy.
- [references/tooling.md](references/tooling.md): `scripts/test-audit` commands, options, exit codes.
