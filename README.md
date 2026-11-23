# sloperations

This repository contains self-contained agent skills and research on AI code
review. The skills can be copied onto machines that can't reach their upstream
repositories.

## Skills

- [`skills/test-audit`](skills/test-audit/SKILL.md): write, review and prune
  tests. It bundles a CLI, `scripts/test-audit`, that works in TypeScript and
  Python repos with no setup.

To install a skill, copy its folder into your agent's skills directory. Nothing
in a skill folder needs network access to an upstream repository.

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
```
