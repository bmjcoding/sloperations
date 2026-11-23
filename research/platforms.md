# AI code review platform research

Verified 2026-11-05 against first-party documentation. These are documented product capabilities, not independent quality measurements. The research covers public sources. Each team should evaluate suitability and obtain approval for its deployment. Prices are snapshots, exclude inference/infrastructure where applicable, and require commercial confirmation.

## Findings that change the product premise

Agent-to-agent remediation is already a substantial part of this market. Greptile has JSON/agent CLI output, MCP retrieval and automated fix skills; CodeRabbit has CLI structured findings and coding-agent loops; Qodo has a scoped Review Resolver; Bugbot can spawn a Cloud Agent and commit fixes. The opportunity is an **evidence-driven, bounded review protocol**, with deterministic policy, cost ceilings, fast incremental checks, and ownership-aware scope—not merely replacing human comments with an agent consumer. Sources are detailed below.

No reviewed vendor publishes a guarantee of deterministic findings, exhaustive bug detection, or sub-second semantic review. Treat claims about bugs caught, resolution rates, and average duration as vendor claims rather than an apples-to-apples benchmark. Configuration determinism and stable finding identity are achievable even when model inference remains probabilistic.

## CodeRabbit

**Mechanism.** CodeRabbit documents isolated repository clones, over 50 static-analysis/lint/SAST tools, agentic context exploration, specialized parallel agents, and learned organizational context. This is a hybrid analysis pipeline rather than a single diff prompt. Reuse the separation between candidate generation, deterministic tools and verification; unrestricted exploration and memory can increase latency and variation. [Architecture](https://docs.coderabbit.ai/overview/architecture).

**Scope.** `.coderabbit.yaml` supports glob-based path instructions and inclusion/exclusion filters. Exclusions win; positive includes restrict eligible review scope. Guidelines such as `AGENTS.md` can supply shared context. Review instructions do not automatically disable separate docstring generation or pre-merge checks: feature policy needs explicit boundaries. [Path instructions and filters](https://docs.coderabbit.ai/configuration/path-instructions).

**Governance.** Configuration inheritance is opt-in (`inheritance: true`). Repository YAML, central YAML, repository/organization UI settings and defaults have documented precedence. Enterprise workspace/organization global overrides apply above repository settings. This distinction between defaults and mandatory overrides is directly useful for an internal implementation. [Inheritance](https://docs.coderabbit.ai/configuration/configuration-inheritance).

**Shift left and agents.** The CLI reviews committed, staged and unstaged tracked changes; untracked files need explicit inclusion. It can pass structured findings to coding agents, supports API-key authentication for headless environments, and maintains incremental review checkpoints. Changing comparison base resets saved context. Local and PR reviews intentionally have different breadth, so local review is not proof that remote review is redundant. [CLI](https://docs.coderabbit.ai/cli).

**Bitbucket and private deployment.** Bitbucket Cloud and Data Center integrations are separately documented. Data Center setup uses a dedicated bot, tokens, OAuth and webhooks. The public Data Center integration requests repository-write and project-admin capabilities; those are vendor setup choices, not necessarily minimum permissions for a read/analyze/status-only internal reviewer. [Data Center integration](https://docs.coderabbit.ai/platforms/bitbucket-data-center), [Cloud integration](https://docs.coderabbit.ai/platforms/bitbucket-cloud).

Self-hosted CodeRabbit is an Enterprise option documented for 500+ seats, distributed as a container and connected to a configured LLM provider. Self-hosting keeps orchestration local, but inference traffic still reaches the model endpoint. It supports Bitbucket Data Center and BYO model. Detailed deployment instructions are customer-only. [Self-hosting](https://docs.coderabbit.ai/self-hosted/overview).

**Commercial snapshot.** Current pricing advertises Essentials/Team/Advanced at $24/$48/$72 per developer per month billed annually, Enterprise custom. Published PR-review limits are 5/8/10/12 per developer per hour respectively; eligible excess usage is optional with caps. Agent runtime is separately $0.40/minute. These are product fees rather than self-hosted compute or an API-token estimate. [Pricing](https://www.coderabbit.ai/pricing).

## Greptile

**Context mechanism.** Greptile says it builds a graph of functions, classes and dependencies to review changes against broader repository context. Its docs advertise approximately three-minute reviews, a vendor average rather than a latency SLO. Feedback adapts future review behavior, so learned context needs versioning if reproducibility matters. [Overview](https://www.greptile.com/docs/introduction).

**Granularity.** Recommended `.greptile/` directories contain structured `config.json`, textual `rules.md`, and explicit context `files.json`; parent configuration cascades into child directories. It can read related repositories as context. This is close to the proposed monorepo policy model. [Configuration](https://www.greptile.com/docs/code-review/greptile-config), [File reference](https://www.greptile.com/docs/code-review/greptile-config-reference), [Cross-repository context](https://www.greptile.com/docs/code-review/cross-repo-context).

**Noise controls.** `strictness` ranges from 1 (verbose) to 3 (critical-only). `commentTypes` selects logic/syntax/style categories, with the supplied array replacing defaults. `ignorePatterns` and `autoReview` control files and open/push/rebase events. Higher strictness numerically means a higher publication threshold, not more exhaustive analysis. [Nitpickiness](https://www.greptile.com/docs/code-review/controlling-nitpickiness).

**Depth tiers.** Base/Plus/Apex cost 1/3/10 credits; Auto chooses according to PR size, risk and complexity. Organizations, repos and directories can set effort. Mixed directory tiers take the highest for the PR; first matching tier rule wins. A PR command may raise but not lower the configured effort. CLI defaults to Base unless explicitly passed `--plus`/`--apex`, rather than automatically adopting PR configuration. Separate review depth from finding severity in the internal design. [Tiers](https://www.greptile.com/docs/code-review/review-tiers).

**Machine consumption and loop bounds.** CLI supports `--json`, `--agent`, branch comparison, diff view and resume. MCP exposes review/comment tools. The `greploop` skill triggers reviews, fixes findings and pushes until a 5/5 score with no unresolved comments, stopping after five iterations. This prevents infinite iteration but does not itself prove a cost, elapsed-time or patch-scope ceiling. A review score is a vendor judgment, not measured probability of correctness. [CLI](https://www.greptile.com/docs/code-review/greptile-cli), [Agent skills](https://www.greptile.com/docs/mcp/skills).

**Current integrations.** Bitbucket Cloud is supported; Data Center is Enterprise, alongside other self-managed SCM options. Self-hosting supports Docker Compose/Kubernetes, PostgreSQL/pgvector, Redis and customer-selected model endpoints including Bedrock. Keeping both SCM and inference endpoints private is what keeps code inside the network. [Providers](https://www.greptile.com/docs/integrations/code-providers), [Self-host deployment description](https://www.greptile.com/content-library/self-hosted-ai-code-review).

**Pricing ambiguity.** The pricing page currently says Pro $30/seat/month with 50 credits/seat and $1 extra credits. The homepage FAQ says Pro has no seat fees and costs $1/credit shared across the organization. Do not turn either into a firm procurement assumption until reconciled. Both agree review tiers consume 1/3/10 credits. [Pricing](https://www.greptile.com/pricing), [Homepage FAQ](https://www.greptile.com/).

## Qodo Review and open-source PR-Agent

Keep these separate: current commercial **Qodo Review v2** is not equivalent to installing open-source **PR-Agent**. Features in Qodo's hosted agentic platform should not be assumed present in the OSS engine.

**Commercial context.** Qodo's Context Engine combines repository structure, cross-file dependencies, historical PR decisions and organizational standards. Findings are ranked by severity and relevance and supplied with contextual evidence. These describe product behavior, not a reproducible precision/recall result. [Context Engine](https://docs.qodo.ai/core-concepts/context-engine).

**Agent workflow.** Review Resolver works over CLI, MCP and coding-agent plugins. It scopes remediation by requested severity, category or finding; reports fixed/skipped-with-reason/report-only outcomes. This is a good precursor to an explicit typed finding lifecycle. [Review Resolver](https://docs.qodo.ai/agentic-toolbox/agentic-toolbox-review-resolver-skill).

**Convergence.** Default fix-loop optimization narrows follow-up reviews when a finding has been resolved and commits appear to contain fixes rather than new functionality. Qodo explicitly says narrowing can affect recall; omitted findings remain recorded. Workspace, Git organization or repository settings control it. This is evidence for bounded follow-up scope, but the change classification should be deterministic or auditable for the proposed service. [Fix-loop optimization](https://docs.qodo.ai/code-review/optimize-review-focus-in-fix-loops).

**Configuration.** The documented `.pr_agent.toml` mechanism supports repository, project/group and organization setup. Bitbucket Data Center has project-level `pr-agent-settings` and a global `PR_AGENT_SETTINGS` project. More specific settings override broader ones. Mandatory organizational controls should therefore be enforced outside ordinary overrideable repo configuration. [File configuration](https://docs.qodo.ai/qodo-documentation/code-review/get-started/configuration-overview/configuration-file).

**Private deployment.** Qodo documents Kubernetes/Helm, persistent storage, private CA configuration, ingress, KEDA, external secrets and metrics; it has a specific Data Center integration guide. These are useful operational references for EKS. [On-premises](https://docs.qodo.ai/on-prem).

**Billing.** Qodo's current docs describe workspace-shared usage credits, unlimited users, monthly renewal and overage at the same credit rate up to a configurable spending cap; not seat-based billing. Enterprise deployment/commercial details are negotiated. The IDE plugin was retired November 1, 2026; use current Agentic Toolbox documentation rather than older IDE-product comparisons. [Pricing and usage](https://docs.qodo.ai/pricing-and-usage), [Current documentation index and retirement notice](https://docs.qodo.ai/llms.txt).

**PR-Agent OSS.** Supports `bitbucket` and `bitbucket_server` among other providers; local CLI, Docker and webhook invocation. `publish_output=false` permits local output without comments. Repository configuration is read from default branch; host-controlled authentication/endpoints/timeouts cannot be set through repo instructions. Its Bitbucket docs warn full-file hydration consumes requests and offer diff-only mode at the cost of context. [Usage](https://docs.pr-agent.ai/usage-guide/automations_and_usage/), [Introduction](https://docs.pr-agent.ai/usage-guide/introduction/).

The configuration reference exposes model timeout, retry and fallback settings. Default same-model retries can amplify latency/cost: explicitly bound total requests, not just each request timeout. [Reference](https://docs.pr-agent.ai/usage-guide/configuration_reference/).

## Cursor Bugbot

Current docs support GitHub/GitLab/Bitbucket including self-managed variants. Incremental review is default. `.cursor/config/bugbot.yaml` is read from the PR base branch, preventing a PR from weakening its own review; it cannot raise Autofix permission. Effort is low/default/high/smart. Existing-branch Autofix has a three-attempt PR limit, supports Bitbucket, and uses separately billed Cloud Agents. Findings default to a neutral check; requiring the check alone therefore does not enforce fixing findings. [Bugbot docs](https://cursor.com/docs/bugbot).

Bugbot moved from seat pricing to usage billing in May 2026, with a vendor-stated average $1–$1.50/review. Its reported high-effort bug-detection and resolution improvements are internal measurements, not independent precision or recall. Older `docs.cursor.com` pages still show $40 subscriptions, so current `cursor.com/docs` and dated announcements take precedence. [Pricing change](https://cursor.com/blog/may-2026-bugbot-changes).

**Design lesson:** configured base-branch policy, separate review/fix identities, explicit terminal statuses and bounded automatic attempts are already shipping competitive features.

## Graphite

Graphite Agent focuses on real bugs, codebase context and learning from feedback. It documents org-wide or repo enablement, rules, exclusions, file exclusions and acceptance metrics per rule. Its primary workflow is GitHub-centric. No Bitbucket integration or deployable self-hosted review engine was verified in the reviewed docs; avoid treating GitHub Enterprise Server connectivity as equivalent to self-hosting the review service. [AI reviews](https://graphite.com/docs/ai-reviews), [Setup](https://graphite.com/docs/ai-reviews-setup).

Graphite can hand tasks to Cursor Cloud Agents, which generate code and draft PRs, and continue their conversations across interfaces. This provides agent handoff but does not establish a public typed review-finding contract or universal fix-loop policy. [Agents](https://graphite.com/docs/agents). Review pricing should be checked directly rather than reusing historical Diamond add-on quotes. [Pricing](https://www.graphite.com/pricing).

## GitHub Copilot code review

Current docs list repository context gathering, review-to-cloud-agent handoff, IDE and GitHub surfaces; Azure DevOps is preview. Lite/Balanced/Max effort trade depth for cost. Vendor estimates are approximately $0.25/$1.50/$10 average review consumption, excluding Actions minutes; actual costs can exceed stated ranges. Max is preview. Bitbucket is not a listed review platform. [Product and billing](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/code-review).

Enterprises can set default and maximum effort, with organizations/repos unable to exceed the ceiling. Rulesets can automatically request reviews on creation/push/draft events. Multiple applicable trigger sources produce a single review. This is a useful centralized budget and duplicate-trigger design. [Configuration](https://docs.github.com/en/copilot/how-tos/copilot-on-github/set-up-copilot/configure-code-review).

Models are selected by the product and not disclosed per review, making exact inference cost and reproducibility difficult to reason about. Review charges combine AI credits and Actions infrastructure. Self-hosted execution runners do not by themselves make the LLM service private. [Models and pricing](https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing).

## GitLab Duo Code Review Flow

The agentic flow runs as a CI job with context pre-scan followed by review; review cannot fetch more context on demand. Context gathering is capped around 1 MiB, truncated near 800 KiB; >10,000-line files use diff-only input. Follow-up reviews read prior Duo threads, suppress repeats and prefer new lines while permitting correctness findings elsewhere. Unchanged rebases do not start another review, and new pushes cancel the running review. Available for GitLab self-managed/self-hosted models, not a Bitbucket reviewer. [Flow architecture and limits](https://docs.gitlab.com/user/duo_agent_platform/flows/foundational_flows/code_review/).

This is a valuable concrete architecture reference: bounded pre-scan plus immutable context is easier to time and reproduce than unconstrained review-time tool exploration. Group/project instruction and trigger hierarchy demonstrates central governance. Do not combine the older non-agentic Duo product's prerequisites and price with this flow.

## Anthropic Claude Code Review

The managed service uses several agents to find candidates, then verify, deduplicate and rank them. It reads source without building/running it. Quick/Standard/Deep depth is configured by organization or repository. It runs on Anthropic infrastructure, supports GitHub (including enterprise variants), and does not run in customer self-hosted environments. Bitbucket is not supported by this managed service. A Claude Team/Enterprise organization is still required when developers otherwise use Bedrock. Its check never concludes as failure and it does not approve/block PRs. [Overview](https://code.claude.com/docs/en/code-review).

Reviews are token-billed to the Anthropic organization separately from plan usage, **even when other Claude Code usage is through Bedrock**. Current official cost docs do not promise a fixed per-review price. Monthly service/org limits and available credit balance can skip a review, including when remaining budget is below expected cost. Every-push operation multiplies cost; manual and once-per-PR modes are available. [Costs and limits](https://code.claude.com/docs/en/code-review-costs).

`REVIEW.md` offers review-only instructions, with root/subdirectory scope; `CLAUDE.md` is shared context. Review rules are taken from trusted base/default branch rather than the PR's own changes, and protection settings determine which suppressions/includes are honored. Local `/code-review` uses `CLAUDE.md` but not `REVIEW.md`, an important shift-left parity limitation. [Customization](https://code.claude.com/docs/en/code-review-customize).

This managed product is a useful architecture comparator, but **an EKS reviewer calling approved Bedrock models is a distinct deployment**, with its own orchestration and finding contract. Do not assume the managed service's verifier, prompts, multi-agent scheduler or billing transfer to the custom implementation.

## Bito

Bito combines contextual AI review with static analysis and dependency tools and can ingest high-severity third-party findings. Official setup covers Bitbucket self-managed as well as cloud. [Overview](https://docs.bito.ai/ai-code-reviews-in-git/overview), [Self-managed Bitbucket guide](https://docs.bito.ai/ai-code-reviews-in-git/install-run-using-bito-cloud/guide-for-bitbucket-self-managed).

Published Team pricing is $12 annually/$15 monthly per seat; Professional $20 annually/$25 monthly, with self-hosting listed as +$5/seat/month. Team includes incremental reviews and CLI/IDE integrations; Professional adds custom guidelines, CI integration and self-hosting. These docs show older model names and should be commercially confirmed. [Billing](https://docs.bito.ai/help/billing-and-plans/overview).

Bito Cloud accessing self-managed Bitbucket is distinct from the separately offered self-hosted service. Opening inbound access to vendor gateway IPs is part of its cloud integration, not evidence that code stays in the company network. [Deployment options](https://docs.bito.ai/ai-code-reviews-in-git/getting-started), [Networking FAQ](https://docs.bito.ai/ai-code-reviews-in-git/faqs).

## Security/static-analysis comparators worth reusing

### Semgrep

Semgrep supports diff-aware PR/MR scanning, including Bitbucket with external CI; `SEMGREP_BASELINE_REF` supplies baseline context. Hosted-platform and CE configurations differ. [CI support](https://semgrep.dev/docs/semgrep-ci/sample-ci-configs).

Its platform distinguishes monitor/comment/block policy and layers SAST, supply-chain, secrets and AI triage/remediation. This is a better foundation for deterministic common rules than asking an LLM to rediscover a prohibited pattern every review. [Platform](https://semgrep.dev/products/semgrep-appsec-platform/).

Current usage docs distinguish scanning licenses from AI credits: analysis 1 credit/finding, autofix 20 credits/finding, detection variable. Exceeding credits stops AI scans/autofixes while autotriage can continue with warnings. A billing warning is not universally a hard enforcement boundary. [Usage limits](https://semgrep.dev/docs/usage-limits/).

### CodeQL / SARIF

CodeQL builds a code database and runs query suites, outputting SARIF. External CI is supported, but private enterprise usage must be licensed appropriately; do not assume an internal Bitbucket installation permits free unrestricted use. [CLI](https://docs.github.com/en/code-security/concepts/code-scanning/codeql/codeql-cli).

SARIF 2.1.0 offers rule IDs, precise locations and partial fingerprints. Stable paths and rule IDs are essential for deduplication across runs. Use SARIF for deterministic findings or import it into the agent protocol rather than scraping rendered PR comments. [SARIF fingerprinting](https://docs.github.com/en/code-security/reference/code-scanning/sarif-files/sarif-support).

CodeQL now documents diff-informed query optimization, but CI must still filter results by diff ranges: the query restriction permits rather than guarantees suppression outside ranges. Review-scope guarantees belong in orchestration, not only model/query instructions. [Incremental analysis](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/scan-from-the-command-line/incremental-analysis).

### Snyk

Snyk has Bitbucket Cloud and Server integrations and PR security/license checks. Its Server integration moved from a status per manifest to a status per product to reduce SCM calls in large repositories—a relevant operational lesson for monorepos. [Status aggregation](https://updates.snyk.io/snyk-pr-checks-for-bitbucket-server-is-migrating-to-per-product-commit-statuses-on-april-22nd-2025-312418/).

Snyk's announced Bitbucket Server comment support includes security/license summaries and Snyk Code inline findings. Reuse existing organizational scanners before creating parallel AI versions of checks already running centrally. [PR comment rollout](https://updates.snyk.io/pr-comments-to-be-default-enabled-and-generally-available-by-september-8-2025-320288/).

## Gaps to test rather than infer from vendors

1. Precision and recall on the adopting team’s languages, frameworks and defect types; synthetic benchmark wins are insufficient.
2. Warm/cold p50/p95 latency, queue time, SCM hydration and inference separately. No common comparable vendor dataset was found.
3. Same head/base/policy repeated-run stability; finding identity churn and budget variance.
4. Re-review behavior under force-push, rename, base update, no-op rebase and malicious policy change.
5. Actual model endpoint/data egress, retention, index ACL boundaries and model availability for the approved deployment.
6. Machine contract: stable finding IDs, commit-scoped evidence, unresolved vs newly discovered vs fixed findings, and honest partial-analysis states.
7. Total fix-loop spend/time/patch limits. An iteration count alone does not bound expensive runs, retries or scope creep.

The strongest competitive synthesis is a deterministic outer system controlling probabilistic semantic analysis: immutable inputs, versioned policy/context, deterministic tool evidence, limited calls and retrieval, cancellation of stale work, typed outputs, and a finite repair lifecycle with explicit verification.
