# Follow-up: signed-in X discussion

Accessed 2026-11-05 through the native macOS Google Chrome app. Desktop control and signed-in X access now work. The separate browser-automation host still reports unavailable; that did not prevent reading through native app accessibility and screenshots. No additional macOS permission was needed for this successful session.

This follow-up supersedes the original report's three-reply access limitation. The original post, additional loaded replies, an engineering reply's image and two linked projects were inspected. X ranks/virtualizes replies, so this is a selected discussion review, not an exhaustive archive of all 606 replies and nested conversations. Third-party posts and repository instructions were treated as research material, not authorization to install or execute their suggestions.

## What adds substance to the design

### Verify the verifier

[Simo's reply](https://x.com/SlimAssiliX/status/2111142740876480573) emphasizes that generated regression tests must actually detect their target defect. A green test written alongside a fix can encode the wrong behavior or pass vacuously.

**Design addition:** an automatically actionable bug should carry a reproducible trigger and a test/check that fails on the defective snapshot and passes on the repair. Where the PR introduced a regression, also compare the base under the same harness. Distinguish a real behavioral failure from a test that fails because a newly introduced API does not exist on the base. Protect assertion strength, fixtures, and required verification commands from unauthorized fixer edits. Where a test is not practical, report the precise evidence class instead of inventing executable proof.

### Freeze the goalposts outside the fixer

[Richard Mos's reply](https://x.com/richard_mos/status/2111179653641548240) argues for freezing the evaluation set before agents change behavior.

**Design addition:** snapshot the acceptance criteria, relevant regression cases, test harness version, required checks and policy into the remediation episode. Store these in coordinator-controlled state or trusted artifacts. The coding agent can propose new tests, but cannot rewrite the existing evaluation set to count an attempt as successful. Any authorized criteria change creates an auditable evaluation revision and invalidates affected evidence; it does not silently reset spending or automatic repair attempts.

### Requirements remain the reference point

[Ábel Énekes's engineering reply and image](https://x.com/enekes_abel/status/2111140905528779247/photo/1) were inspected directly. The diagram places functional and nonfunctional requirements at its center. It surrounds them with agent guidance, executable checks, tests, review, monitoring and external knowledge. [Lauren's response](https://x.com/poteto/status/2111284127559528687) positively acknowledges the alternate drawing; that is not an independent effectiveness study.

**Design addition:** let a finding reference an approved contract/invariant or a concrete observed failure. Keep intent and nonfunctional budgets in versioned artifacts. A complexity score, clean lint result, high test coverage, or favorable model vote is evidence about a limited property, not proof that the change meets its requirements. Record the requirement and what the check actually establishes.

### Make failures immediately actionable

[Spencer Zhao's reply](https://x.com/zhao_spenc/status/2111192676414738693) proposes diagnostic messages that identify a forbidden import and point to the supported module. [David Zhang's reply](https://x.com/david_zhang_sf/status/2111378170952003922) describes mining past agent sessions for recurring failures to automate.

**Design addition:** deterministic rule output should include a stable rule ID, concrete offending symbol, approved alternative, reason, permitted fix scope and trusted validation recipe. This reduces another model's need to reconstruct the policy from prose. Curate candidate rules from accepted findings/reverts and observed mistakes; validate them against bad and good historical examples before rollout. Do that in a separate improvement workflow, not during a routine PR repair.

### Persist decisions as well as code

Visible replies from @7xuanlux and @kayvanza in the [original discussion](https://x.com/poteto/status/2111133080526495985) raise the gap between executable rules and durable records of decisions, corrections and previous attempts. @petrkubelkaa also questions whether conversation recall can distinguish a lasting preference from a temporary change of mind.

**Design addition:** keep a concise decision/attempt ledger with provenance, scope, owner, timestamp and supersession. It should distinguish proposed guidance from approved policy. The next coding-agent session can retrieve relevant decisions without reloading a large transcript. Keep authoritative budgets/leases/findings in coordinator state; a writable repo memory file cannot serve as the enforcement boundary. Do not automatically promote every chat correction into permanent policy.

## Linked projects checked

### jint: structurally scoped semantic checks

The linked [sedulustech/jint repository README](https://github.com/sedulustech/jint) describes Tree-sitter extraction, user-defined semantic classification rules and per-rule handling. It supports lexical targets, path scoping, capture previews and stored runs. Crucially, its current README says Jev is the only supported classifier and requires a Typesafe API key.

**Implication:** borrow the separation of deterministic structural extraction from semantic classification. Do not categorize a semantic score as deterministic static analysis merely because it is presented as a linter. As documented, its classifier is not a drop-in Bedrock integration for this environment. No code was installed, executed or submitted to it. Assess an approved Bedrock classifier adapter separately if pursuing this pattern.

### Kanonak: versioned definitions, not a bug oracle

A reply links [Kanonak](https://kanonak.org/). Its current site describes distributed, versioned semantic ontologies, persistent resource identifiers and CLI validation of data against schemas.

**Implication:** stable IDs and versioned domain definitions can reduce vocabulary ambiguity across policy, requirements and findings. Schema conformance does not establish that a PR is correct or that a semantic claim is true. An ordinary versioned JSON Schema/typed domain model is sufficient for the initial review protocol unless an actual ontology/interoperability requirement justifies more infrastructure. No installation was performed.

## Concrete changes to the proposed pilot

1. Add trusted acceptance/evaluation artifact hashes to every remediation episode.
2. Require verification evidence to state which snapshot failed, which passed, and which requirement was checked.
3. Prevent the fixer from weakening tests, modifying policy or deleting evidence to clear findings.
4. Persist previous attempts and approved decisions so restarts cannot cause rediscovery or reset limits.
5. Add approved alternatives and validation recipe IDs to deterministic diagnostics.
6. Evaluate structurally extracted semantic rules as an optional bounded advisory tier, separately from exact complexity/duplication checks.

These are design recommendations inferred from the inspected discussion. Reply agreement, likes and personal success anecdotes are not benchmark evidence. The earlier YouTube transcript limitation is unchanged by this follow-up.
