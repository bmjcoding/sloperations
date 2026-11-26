# sloperations

This repository contains self-contained agent skills and research on AI code
review. The skills can be copied onto machines that can't reach their upstream
repositories.

## Skills

- [`skills/test-audit`](skills/test-audit/SKILL.md): write, review and prune
  tests. It bundles a CLI, `scripts/test-audit`, that works in TypeScript and
  Python repos with no setup.
- [`skills/slop-cleanup`](skills/slop-cleanup/SKILL.md): clean up agent-written
  debt across code, tests, comments, docs, schema and logs, in eight phases. It
  bundles `scripts/slop-check`, a deterministic checker (docs and comments,
  structured logs against `log-contract.json`, schema against a migrated
  PostgreSQL catalog, and a finding-fingerprinted baseline with a policy
  comparison), plus config templates for TypeScript with Node and for Python
  with Node. It needs Node 24 or later, parsers pinned in its lockfile
  (`(cd skills/slop-cleanup/scripts && npm ci)`, once per checkout), and
  Python 3.9 or later for Python files. It composes `test-audit`,
  `codebase-design`, `domain-modeling` and `improve-codebase-architecture`.

To install a skill, copy its folder into your agent's skills directory. Nothing
in a skill folder needs network access to an upstream repository. slop-cleanup's
checker also needs `npm ci` once, from the npm registry or a mirror of it.

## Research

[AI code review research](research/README.md) covers review platforms, a reference
service design for Bitbucket Data Center, Amazon EKS, and Amazon Bedrock, and
example review policy and result formats.

## Following upstream

`skills/test-audit` is a hand-maintained rewrite of an upstream skill.
[`upstream/watch.json`](upstream/watch.json) records the upstream paths it
draws from and the commit they were last reviewed at.
[`upstream/watch.sh`](upstream/watch.sh) compares those paths with the upstream
branch through the GitHub API. Nothing runs it on a schedule in this repo: run
it by hand, or from whatever monitors the upstreams.

```sh
DRY_RUN=1 upstream/watch.sh   # show what changed upstream; changes nothing
upstream/watch.sh             # keep one issue open per entry while they differ
```

After porting what matters into the skill, set `pin` in `watch.json` to the
upstream commit that was reviewed.

## Development

```sh
sh tooling/test-audit/run-tests.sh   # the CLI's tests, on every Python >= 3.9 found
sh tooling/slop-check/run-tests.sh   # slop-check's tests: Node >= 24, every Python >= 3.9 found
```

The slop-check suite applies real migrations to a throwaway PostgreSQL when
Docker or a local PostgreSQL is available, and skips those cases otherwise; see
[`tooling/slop-check/README.md`](tooling/slop-check/README.md).
