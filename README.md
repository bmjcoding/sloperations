# sloperations

This repository contains self-contained agent skills and research on AI code
review. Each skill holds its upstream material in its own folder, so it works on
machines that can't reach the upstream repositories.

## Skills

- [`skills/codebase-design`](skills/codebase-design/SKILL.md): design deep
  modules behind clean seams, enforce import rules, make concurrent or
  crash-safe state writes converge, and run a requested architecture review.
- [`skills/domain-modeling`](skills/domain-modeling/SKILL.md): sharpen domain
  terms against scenarios and code, and keep `CONTEXT.md` (or `CONTEXT-MAP.md`)
  and ADRs current.
- [`skills/grilling`](skills/grilling/SKILL.md): interview the user about a
  plan in rounds of questions, each with a recommended answer, until every
  decision is settled.
- [`skills/research`](skills/research/SKILL.md): investigate a topic from primary
  sources into one cited Markdown file, or trace why code is the way it is in the
  conversation through commits and pull requests.
- [`skills/unslop`](skills/unslop/SKILL.md): numbered rules for cutting AI
  tells and dead weight from prose.
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
  `codebase-design` and `domain-modeling`.

## Install and update

Clone this repository (or copy its folder onto an offline machine) and run `./install.sh` from
the checkout (macOS and Linux):

```sh
./install.sh              # first install or link newly added skills
./install.sh --dry-run    # show what it would do
git pull && ./install.sh  # update a git checkout and link skills added since
```

`install.sh` links each folder in `skills/` into `~/.agents/skills/<name>`, which Codex and other
Agent Skills tools read, and `~/.claude/skills/<name>`, which Claude Code reads. Because they are
links, `git pull` updates installed skills. It leaves links that already resolve to this checkout
alone, and never replaces or deletes existing files, folders or other links. If a destination is
occupied or unwritable, it names the path, leaves that skill unlinked and exits non-zero. A
mid-run error on the second client can still leave the first link in place; fix it and rerun.

For each path it refuses (`grilling` stands for the skill's name below):

- A folder, such as an upstream copy: move it outside both skill directories, for example
  `mkdir -p ~/skill-backup && mv ~/.agents/skills/grilling ~/skill-backup/`.
- A link: check it, for example `readlink ~/.claude/skills/grilling`, and `rm` it only if it
  points at a folder you moved aside or at an old location of this checkout.

Move aside the superseded `improve-codebase-architecture`, `grill-me` and `grill-with-docs` the same
way, then rerun. Start a new Claude Code or Codex session to pick up newly linked skills. Claude
Code cloud sessions don't read this machine's `~/.claude/skills`; commit the skills a repository
needs into its `.claude/skills/`. Cowork uses only skills uploaded and enabled on your Claude
account, under Customize, then Skills.

Nothing in a skill folder needs network access to an upstream repository.
slop-cleanup's checker also needs `npm ci` once, from the npm registry or a mirror
of it.

## Research

[AI code review research](research/README.md) covers review platforms, a reference
service design for Bitbucket Data Center, Amazon EKS, and Amazon Bedrock, and
example review policy and result formats.

## Following upstream

Every skill except slop-cleanup is a hand-maintained rewrite of upstream skills.
Each adapted skill's bundled license notice (`LICENSE` or `LICENSE.txt`) holds its
upstream license notices.
[`upstream/watch.json`](upstream/watch.json) records the upstream paths each skill
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
sh tooling/install/run-tests.sh      # install.sh against throwaway HOME directories
```

The slop-check suite applies real migrations to a throwaway PostgreSQL when
Docker or a local PostgreSQL is available, and skips those cases otherwise; see
[`tooling/slop-check/README.md`](tooling/slop-check/README.md).

Check a skill against the Agent Skills specification with
`uvx --from skills-ref agentskills validate skills/<name>` (fetches skills-ref on
first run; the skills themselves need no network).
