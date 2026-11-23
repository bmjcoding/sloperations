# Lessons from Anthropic's internal review process

Read 2026-11-06. This note applies the public case study to the proposed review service for teams using Bitbucket Data Center, EKS, and Bedrock.

## Source observations

The [article](https://claude.dev/blog/solving-code-review-at-anthropic/) reports an internal PR exceeding fifty review/fix rounds; roughly 59% of subsequently flagged code existed at the first pass. Their response includes a three-round limit, narrower re-review, deduplication and bug-class feedback. They evaluate against incidents and clean changes, including potentially harmful recommendations. Risk governs human review; merge-time checks and recovery complement PR analysis. These are self-reported practices, not independent evidence of effectiveness for another team.

## Proposed additions to the reference design

### 1. Distinguish unfinished review from newly introduced defects

An incremental reviewer needs at least two notions of change: code changed since its preceding run, and defects introduced by the PR relative to its target baseline. A previously unnoticed bug can be PR-introduced even when its code has not changed since the first review. It should not be falsely labeled legacy debt merely because the reviewer discovered it late.

Record `first_observed_run`, `causal_change_sha`, `introduced_relative_to_target`, and `touched_since_previous_review` independently. On a repair push, verify original findings, review affected dependencies and screen for serious new regressions. Late-discovered important bugs can still block, but must not create another automatic repair allowance. Optional late findings go to a separate backlog with provenance. If review was incomplete, preserve that state rather than declaring convergence because the latest delta is clean.

Maintain two bounded workstreams: current-PR remediation and repository-wide historical-debt analysis. Give them distinct budgets, ownership and scheduling. This keeps pre-existing improvements out of routine fix loops without discarding legitimate findings.

### 2. Measure the harmful-action rate

Add a clean-PR evaluation lane. A false positive matters more when another agent will obey it automatically. Build negative examples around intentional eventual consistency, domain-required duplication, unusual but valid transaction logic, and performance tradeoffs documented by owners.

Evaluate the downstream patch, not just whether a comment sounded plausible. Record unnecessary changes, behavior regressions, broken compatibility, weakened tests, expanded scope and new dependencies. Keep useful outcomes distinct: correctly fixed, rejected as false, intentional behavior, accepted exception, and unimportant. An agent applying a fix must not automatically count as a correct resolution.

Promote a finding category into automatic remediation only after it demonstrates acceptable correctness and harmful-action rates on held-out cases. The values need to be agreed from pilot data and the consequences of a bad fix.

### 3. Group causal instances without authorizing a broad rewrite

A useful diagnostic might identify multiple missing tenant predicates caused by one repository helper. Represent a stable finding group with explicit member locations and shared root cause. A fixer can then address the permitted instances in one push rather than one line per review round.

Validate grouping deterministically where possible. A model's statement that something is a systemic problem cannot expand its allowed paths, symbols, dependencies or architecture. If the root-cause repair requires a wider change, return NEEDS_ATTENTION with the smallest proposed scope extension; do not silently continue.

### 4. Make repo context executable and test whether it is followed

Keep concise, owner-approved failure definitions by path: tenant isolation, backward-compatible messages, bounded query counts, migration rollback compatibility, or idempotent ledger submission. Each should point to contracts, examples and trusted checks. Prefer enforceable rules for requirements that can be encoded.

The [customization docs](https://code.claude.com/docs/en/code-review-customize) distinguish service-only REVIEW.md from shared CLAUDE.md; their local `/code-review` command does not consume REVIEW.md. The proposed CLI and EKS service should resolve one versioned policy bundle, then intentionally select local or server checks with explicit coverage. Test instruction adherence using both violating and compliant fixtures for every important path policy. Reading a rule file is not evidence that the model follows it.

### 5. Keep risk routing separate from finding severity

Risk is a property of the proposed change's possible impact, while severity describes a discovered defect. A clean review of a high-risk migration does not make the migration low-risk. Use centrally enforced path floors plus signals such as public contract changes, privilege expansion, data movement, schema compatibility, dependency changes and rollback difficulty.

An optional semantic classifier may raise risk or request additional scrutiny; it cannot lower mandatory checks or required approval. Bind any human approval to the evaluated SHA and invalidate it after material unverified changes according to the adopting team’s policy. Require approval from the team’s policy owners before enabling automatic merges.

### 6. Add merge-candidate evidence and recovery metadata

A head/base review is insufficient if two individually valid PRs interact after landing. Where existing CI supports it, validate the prospective merged candidate under current target state. If the selected Bitbucket version lacks a merge queue, use serialized integration or revalidate against the current target immediately before merge.

Collect a responsible owner, test evidence and a recovery plan in the result manifest or linked trusted artifacts. For risky data changes, a git revert may not undo effects: assess schema compatibility, persisted data transformations and recovery procedure separately. Reversibility can influence scrutiny, but cannot waive mandatory controls.

### 7. Turn outcomes into a controlled evaluation loop

For a missed defect, preserve the bug-introducing snapshot and categorize the failure: absent contract, missing context, incorrect reasoning, filtered true finding, inadequate verification, stale result, or broken enforcement. The remedy differs: more model calls will not fix a missing mandatory merge check.

Add validated cases to a regression corpus, and keep a separate held-out set so daily tuning does not become memorization. Gate reviewer prompt, model, retrieval and rule changes on quality, convergence, latency and cost. Track worst-case behavior as well as means: episode exhaustion, stale publications and harmful fixes deserve individual investigation.

Measure active human investigation time plus repair/incident work, alongside review latency and inference spend. Reduced human comments alone can indicate that people stopped engaging, rather than that the service improved. A quality regression should trigger a predefined response such as disabling automatic fixes or raising scrutiny; the exact thresholds and authority must be agreed before rollout.

## What to avoid importing wholesale

- A mandatory multi-model council on every PR: benchmark selective verification against a bounded single-pass baseline first.
- Repeated full-PR reviews as a default route to confidence: they can reveal more existing issues without advancing the permitted repair.
- Letting a model classify a change as safe enough to bypass central controls.
- Treating all unresolved comments as equally blocking; severity, evidence, authorization and disposition belong in typed state.
- Assuming the internal process is identical to the commercial service. The [managed product docs](https://code.claude.com/docs/en/code-review) say it runs on Anthropic infrastructure, targets GitHub and does not itself approve/block merges. The proposed Bedrock service needs its own runtime, state machine and enforcement.

## Priority

Before adding deeper model review, implement clean-PR harmful-action evaluations, distinct causal/change tracking, immutable finding groups, and shared policy adherence tests. Keep the proposed two-repair-push cap. The initial review plus two bounded repair verifications is a maximum of three cycles in that episode; failed retries and duplicate deliveries must not create extra cycles or spending allowances.
