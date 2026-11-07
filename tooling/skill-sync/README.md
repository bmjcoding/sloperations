# skill-sync

Vendors agent-skill folders from upstream repositories and keeps them current
without hand-merging. Each vendored skill is regenerated from a pinned upstream
commit by a short list of anchored edits, so you review rules, not diffs of
someone else's files. It needs only Python 3.9+ and git. It has no dependencies.

```sh
tooling/skill-sync/skill-sync list               # forks, modes, pins
tooling/skill-sync/skill-sync sync autoreview    # regenerate skills/autoreview at its pin
tooling/skill-sync/skill-sync check autoreview   # try the newest upstream commit; writes nothing
tooling/skill-sync/skill-sync bump autoreview --to <sha>
tooling/skill-sync/skill-sync watch test-audit   # upstream changes to port by hand
tooling/skill-sync/skill-sync verify             # committed outputs == pin + rules, byte for byte
```

## How it works

A fork definition, `upstream/<name>.json`, names an upstream repository, a
branch and a **pin**: a full commit SHA. `sync` builds the output in a temporary
directory, in this order:

1. **Fetch.** It fetches the pinned commit into a bare cache outside the repository:
   `$SKILL_SYNC_CACHE`, else `~/.cache/skill-sync`. It confirms the commit resolves to
   exactly the pinned SHA. Files are read as raw git blobs, so upstream's `.gitattributes`
   can't change their bytes. Symlinks and submodules are refused.
2. **Stage.** It copies the declared `paths` into a stage that mirrors upstream's layout.
   It also copies `support` files, which only verify commands use and which are never
   vendored, and the `license` file.
3. **Delete.** It applies the `delete` entries.
4. **Rules.** It applies the `rules`, in order.
5. **License.** It copies upstream's license into each output folder as `LICENSE`.
6. **Tripwire.** It runs the [tripwire](#the-tripwire).
7. **Links.** If `links` is set, every relative Markdown link must resolve inside
   its vendored folder, heading anchors included.
8. **Write.** It writes `UPSTREAM.md` into each output: upstream repo, path,
   commit, the rules and deletions applied with their reasons, features left out, and
   a do-not-hand-edit note. Then it swaps the folder into place.

Publishing is one recoverable transaction, covering every output folder and, for
`bump`, the new pin:

1. **Preflight.** Every destination is checked first (no file or symlink in the way, a
   writable parent, exactly one pin to replace). If any check fails, nothing changes.
2. **Journal.** Each step is recorded in `upstream/.skill-sync-txn.json`.
3. **Swap.** New folders are copied next to their destinations, then renamed in, and
   the pin is replaced last.
4. **Recovery.** A failure rolls back to the old outputs and the old pin. A killed run
   is recovered by the next `sync` or `bump`, before it reads any fork: rolled back
   unless the journal had reached "committed", in which case the cleanup is finished.
   `verify` fails while a journal is present.

Paths in a definition are upstream-relative. The one exception is `paths[].output`,
which is relative to this repository. Unknown keys are errors, so a typo can't
quietly disable a rule.

## Fork definitions

```jsonc
{
  "name": "autoreview",              // must match the file name
  "mode": "rules",                   // or "watch"
  "upstream": {
    "repo": "https://github.com/openclaw/agent-skills.git",
    "branch": "main",
    "sha": "<40-hex pin>",
    "require_signed": true,          // check/bump (rules and watch) adopt only GitHub-verified commits
    "filter": "blob:none"            // optional: blobless cache for very large upstreams
  },
  "paths": [{ "upstream": "skills/autoreview", "output": "skills/autoreview" }],
  "license": "LICENSE",
  "support": ["scripts/check-skills"],
  "features": { "helper-help-text": { "description": "...", "requires": [] } },
  "delete": [{ "id": "delete/x", "path": "skills/autoreview/references/x.md", "count": 1, "reason": "..." }],
  "rules": [
    { "id": "skill/x", "file": "skills/autoreview/SKILL.md",
      "anchor": ["literal line one", "literal line two"], "replacement": "new text",
      "count": 1, "type": "strict", "reason": "..." },
    { "id": "skill/y", "kind": "remove-section", "file": "skills/autoreview/SKILL.md",
      "anchor": "## Heading", "reason": "..." }
  ],
  "tripwire": { "terms": ["openclaw"], "ignore_case": true,
                "allow": [{ "paths": ["skills/autoreview/LICENSE"], "reason": "..." },
                          { "paths": ["skills/**"], "literal": "openclaw/tap/crabbox", "reason": "..." }] },
  "links": true,
  "verify_python": ">=3.10",          // {python} must be at least this version
  "verify_shared_tmp": ["autoreview-scratch-"],  // names verify may create in /tmp and /var/tmp
  "verify": [{ "name": "tests", "argv": ["{python}", "scripts/check-skills"], "timeout": 1800 }]
}
```

- **`anchor` and `replacement`** are literal text, never regexes. A list of strings is
  joined with newlines. End it with `""` to include the final newline.
- **`file`** is a glob: `*` stays within one path segment and `**` spans folders.
  `count` is the total number of matches across every file the glob matches. It
  defaults to 1. If the count is wrong, the rule fails.
- **`kind: "remove-section"`** deletes a Markdown section: from the heading line that
  equals `anchor` up to the next heading of the same or a higher level. Headings
  inside code fences don't count. The section's content is free to change upstream;
  only the heading is the anchor.
- **`delete`** entries take a path glob and the number of files it must match.
- **`verify`** commands run upstream code, in a copy of the stage with the upstream
  layout as the working directory, inside a [sandbox](#the-verify-sandbox). Nothing
  they write reaches the output.
- **`{python}`** is the interpreter running skill-sync if it meets `verify_python`.
  Otherwise it's the newest `python3.N` on PATH that does. The order is fixed, so the
  choice is deterministic for a given machine, and verify stops with a clear error when
  nothing qualifies. autoreview needs 3.10+ (upstream's floor), so a Python 3.9 skill-sync
  still runs its tests on a newer interpreter. Its Pillow pin also carries
  environment markers, so the install step is valid on any interpreter.

## Adding a fork

1. Read every upstream file you're vendoring.
2. Write `upstream/<name>.json` with the pin set to the upstream commit you read.
   Prefer anchors that are short but unique, and give every rule a reason.
3. Run `skill-sync sync <name> --checks`. That builds the folder, runs the verify
   commands, and writes the output only if everything passes.
4. Review the output and `UPSTREAM.md`, then sign-commit the definition together with
   the generated folder.

For a hand-maintained port, use `"mode": "watch"` with a list of `files` and an
optional `local` folder. Watch-mode forks have no rules and generate nothing.

## Adopting an upstream change

The scheduled workflow, `.github/workflows/upstream-skills.yml`, runs `check` on
each rule-mode fork. It keeps one issue per fork:

- **`<name>: upstream <sha7> ready`** gives the exact command to run locally:
  `tooling/skill-sync/skill-sync bump <name> --to <sha>`. `bump` re-runs the full
  check on your machine, moves the pin and regenerates the output. Review `git diff`,
  then sign the commit. CI never commits or pushes.
- **`<name>: upstream <sha7> fails strict rules`** lists the strict rules that
  stopped it. The pin stays put. Fix the anchors in `upstream/<name>.json`, then
  confirm with `skill-sync check <name>`. Use `--to <sha>` to try a specific commit.

`check` evaluates the newest commit after the pin that touches a tracked path
(`paths`, `support` or `license`). It searches the branch's first-parent history, so
a topic branch merged into the tracked branch yields the mainline merge, never a
topic commit that isn't a descendant of the pin. Its exit codes are distinct:

| Exit | Meaning |
| --- | --- |
| 0 | A newer candidate passes; adopt it with the printed `bump` command |
| 1 | The candidate fails: a strict rule, the tripwire, links, a verify command, its signature, or it isn't a descendant of the pin |
| 3 | Nothing newer: no commit after the pin touches the tracked paths |
| 2 / 4 | Usage error / git, network or filesystem error |

A candidate counts as signed when GitHub's commit API reports it as verified. Set
`SKILL_SYNC_GITHUB_TOKEN` to avoid the anonymous rate limit locally. CI sends no
token, because the job that does this lookup also runs upstream's tests.

For watch-mode forks, `watch <name>` lists the upstream commits since the pin that
touched the tracked files, and the diff. It exits 0 when there are none and 1 when
there are some. After porting by hand, `bump <name> --to <sha>` moves the pin to
acknowledge them. It applies the same `require_signed` gate as rules mode.

## Strict and feature rules

- **Strict rules (the default).** These cover anything that must hold for the vendored
  copy to be acceptable: removing another product's guidance, refusing an engine, fixing
  install instructions. If one fails, `sync` writes nothing and `check` and `bump` refuse
  the candidate. The last good pin stays.
- **Feature rules.** These have `"type": "feature"` and a `feature` name declared under
  `features`. Use them for nice-to-haves. If any rule in a feature fails, the whole
  feature is left out, plus every feature that `requires` it. The build is redone from
  scratch without those features' rules, so nothing is half-applied, and it still passes.
  Upstream's text runs in its place. The report and `UPSTREAM.md` list what was left out
  and why.

## The tripwire

After the rules, the tripwire searches every output file for the fork's `terms`,
case-insensitively by default. Any hit is a strict failure. This is what stops an
update when upstream adds new branded text that no rule knows about yet.

Allowlist entries need a written `reason`:

- An entry without `literal` skips whole files. Use it for license text, or helper code
  kept verbatim on purpose.
- An entry with `literal` masks only that exact text, so any other mention in the same
  file still trips. Use it for real distribution coordinates, such as a Homebrew tap.

The report notes allowlist entries that no longer match anything.

## The verify sandbox

Verify commands run upstream code, such as autoreview's test suite. Each one runs in an
OS-enforced sandbox, in its own session, with a fresh `HOME` and `TMPDIR` inside the
work directory and a scrubbed environment. Only `USER`, `LOGNAME`, locale variables and
`DEVELOPER_DIR` pass through, plus the entry's own `env`; no tokens pass. `PATH` passes
with its relative entries (`.`, empty, `bin`) removed, so a file planted in the stage
can't shadow a tool. The sandbox executable itself (`/usr/bin/sandbox-exec`, `bwrap`,
`docker`, `podman`) is resolved to an absolute path before any upstream code runs.

- **Reads:** the stage, that fresh `HOME` and `TMPDIR`, and the toolchain. The toolchain
  means system folders, the interpreter's prefixes and the `PATH` folders. Never the real
  `HOME`, the repository or the cache.
- **Writes:** only the stage and that `HOME` and `TMPDIR`.
- **Shared temp:** a fork can list name prefixes in `verify_shared_tmp` for upstream
  tests that must create folders directly in `/tmp` or `/var/tmp`. autoreview's tests do
  this to check that the helper refuses shared scratch. On macOS only those names are
  writable there. bwrap and the containers give `/tmp` and `/var/tmp` private mounts
  instead.
- **Network:** IP networking stays open, because the autoreview verify step installs
  pinned Pillow wheels with pip. Commands may also listen on and connect to local
  addresses, for test servers. Unix sockets work only inside the writable folders. On
  macOS the one exception is the system DNS resolver's socket
  (`/private/var/run/mDNSResponder`), because name resolution fails without it. So no
  host daemon is reachable: not Docker, not an ssh-agent, not a keyring. bwrap and the
  containers don't mount those sockets at all.
- **Cleanup:** on timeout, cancellation (Ctrl-C or SIGTERM) or a normal exit, skill-sync
  kills the command's process group and every descendant. It snapshots the process tree
  before anything dies, then sweeps until nothing is left. That includes children that
  started their own session.
  - **Orphans on Linux:** a detached child whose parent already exited has no parent link
    left, so the sweep finds it by a random token in its environment. bwrap and the
    containers also run the command in a PID namespace that dies with it.
  - **Orphans on macOS:** macOS doesn't expose other processes' environments, so the
    sweep identifies them by their sandbox profile instead. Each run's profile can read
    exactly one of two randomly named sibling files. Only a process under that profile
    passes the `sandbox_check(3)` test, and only processes younger than the command
    qualify. skill-sync and its ancestors are never touched.
  - **Remaining limit:** on macOS, and unsandboxed on Linux, an orphan escapes the sweep
    if it scrubs its environment and isn't under a sandbox profile. That can only happen
    unsandboxed. Under `sandbox-exec` such a process still carries the inherited profile,
    so the sweep finds it, and it stays confined: same reads, same writes, no host
    sockets.

`SKILL_SYNC_SANDBOX` picks a backend (`auto` by default, or `none`). The backends are:

| Backend | Where | How |
| --- | --- | --- |
| `sandbox-exec` | macOS | A generated deny-by-default profile. It allows only the paths above. Of the system services, only user lookup, DNS, logging, proxy settings and `trustd` are reachable; `trustd` handles certificate checks for pip. Keychain services are not. Tools that open each path component from `/` can list the exact ancestor folders of the writable paths, never their other contents. |
| `bwrap` | Linux | Bubblewrap with every namespace unshared except the network. Only the paths above are mounted (toolchain read-only), and the process dies with skill-sync. |
| `docker` / `podman` | Linux without a working bwrap | The host toolchain is mounted read-only over a pinned busybox image, with no capabilities and the host's network. It is Linux-only because it runs host binaries. |

With no backend, verify refuses to run. `--unsandboxed-verify` (on `sync`, `check`,
`bump` and `verify`) runs it without one. That is meant only for disposable CI runners
that hold no secrets. The scheduled workflow uses it with `SKILL_SYNC_SANDBOX=none`,
because Ubuntu runners ship no bubblewrap and AppArmor blocks the unprivileged user
namespaces it needs. The evaluate job holds only a read-only token. When a verify
command fails, its full log is kept under `<cache>/logs/` and the report names it.

## Verify

`skill-sync verify` regenerates every rule-mode fork at its pin, in a temporary
directory, and compares the result with the committed `skills/<name>/` folders. The
comparison covers file sets, bytes and the executable bit. It also flags leftovers
from an interrupted sync, and ignores `__pycache__` and `.DS_Store`. A hand edit, a
stray file or a stale regeneration fails it, and CI runs it daily. Add `--checks`
to also run each fork's verify commands.

## Tests

```sh
python3 -B -m unittest discover -s tooling/skill-sync/tests -t tooling/skill-sync/tests
```

The tests drive the CLI against local fixture git repositories and a local fake
GitHub API (`SKILL_SYNC_GITHUB_API`), so they need no network. Sandbox enforcement tests
run where `sandbox-exec` or a working `bwrap` exists and are skipped elsewhere. Tests that
only need verify commands to run use `--unsandboxed-verify` there. `-B` keeps bytecode
out of the repository, and the launcher itself never writes any. Fixture commits use a throwaway identity and an isolated git config, so
they never sign or use yours.
