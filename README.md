# sloperations

Agent skills that are self-contained and safe to copy onto machines that can't
reach their upstream repositories.

## Skills

- [`skills/test-audit`](skills/test-audit/SKILL.md): write, review and prune
  tests. It bundles a CLI, `scripts/test-audit`, that works in TypeScript and
  Python repos with no setup.

To install a skill, copy its folder into your agent's skills directory. Nothing
in a skill folder needs network access to an upstream repository.

## Following upstream

`skills/test-audit` is a hand-maintained rewrite of an upstream skill.
[`upstream/watch.json`](upstream/watch.json) records the upstream paths it
draws from and the commit they were last reviewed at.
[`upstream/watch.sh`](upstream/watch.sh) compares those paths with the upstream
branch through the GitHub API, and the `upstream-watch` workflow runs it daily
to keep one issue open while they differ. To act on an issue, port what matters
into the skill by hand and set `pin` to the commit the issue names.

```sh
DRY_RUN=1 upstream/watch.sh   # show what changed upstream; changes nothing
```

## Development

```sh
sh tooling/test-audit/run-tests.sh   # the CLI's tests, on every Python >= 3.9 found
```
