---
summary: "test-audit CLI: detect, run, changed, deadcode, loc, baseline, ledger, mutate"
title: "test-audit tooling"
read_when:
  - You need a repo's test command, single-worker flags, or changed-file plan
  - You are recording a campaign baseline, ledger, LOC, dead code, or mutation proof
---

Deterministic helpers for the steps the skill describes in prose. Prefer them
over hand-built commands; read their output, not just the exit code.

```sh
S=<skill-dir>/scripts/test-audit      # e.g. .claude/skills/test-audit/scripts/test-audit
$S [global options] COMMAND [options]   # global options also work after COMMAND
```

## Runtime

- Zero setup: POSIX `sh` launcher + stdlib-only Python package (3.9-3.14).
- Interpreter: `$TEST_AUDIT_PYTHON` if set, else `python3` >= 3.9, else `python`
  >= 3.9, else `uv run --no-project --no-config --python ">=3.9" python`. None:
  exit 2 naming what to install.
- Isolation: runs `python -I -S -B` and puts only `scripts/` on `sys.path`.
  `-I` drops the cwd, the script dir, `PYTHON*` env vars, and user site; `-S`
  drops site-packages; `-B` writes no `__pycache__` (the skill may live inside
  the target repo). Repo files named `json.py` or `test_audit/` cannot shadow
  anything.
- Repo writes: only `baseline --out FILE` and `mutate`'s temporary edit
  (restored and verified). No venvs, installs, or manifest or lockfile edits.
  - **Temp dirs:** every temp dir is created under a temp root that is
    checked first. If `TMPDIR` resolves inside the repo, the command exits 2
    before creating anything.
  Built-in tool caches are disabled or sent to temp dirs outside the repo,
  which are removed on exit:

  | Tool | Enforced |
  | --- | --- |
  | Python (every test run, incl. pytest and custom commands, and every `changed --run` check) | fresh `PYTHONPYCACHEPREFIX` per run, so neither the repo nor its `.venv` gets `.pyc` files; pytest also gets `PYTHONDONTWRITEBYTECODE=1 -p no:cacheprovider` |
  | Vitest / `vp test` | `--no-cache --no-fsModuleCache` (when `--help` lists them). `node_modules/.vite-temp/`: removed only if this run created it and it is empty; no file in it is ever deleted (see "Not enforced") |
  | Jest | `--cacheDirectory <temp>` |
  | ruff | `check --no-fix --no-cache` (overrides `fix = true`), `format --check --no-cache` |
  | mypy | `--cache-dir /dev/null` |
  | tsc / tsgo | `--noEmit`; plus `--tsBuildInfoFile <temp>` only when the effective config is `incremental` or `composite`. That is read from `tsc --showConfig -p tsconfig.json` (or `tsgo`), which resolves `extends` and writes nothing; if that fails, the relative `extends` chain is read directly. TS < 5.6 rejects the flag otherwise (TS5069). Verified with TS 5.4.5, 5.9.3, and 7.0.2, inherited-incremental and off |
  | oxlint, eslint, biome, prettier, oxfmt, `vp check`, pyright | read-only check modes; fixing or writing needs a CLI flag (`--fix`/`--write`) these tools never receive, and none writes a cache by default |

  Not enforced:
  - What the tests themselves write.
  - Plugins or reporters the repo configures (e.g. a Vitest `outputFile`).
  - Vite's compiled config: Vite bundles a TS config to
    `node_modules/.vite-temp/<config>.timestamp-<ms>-<hash>.mjs` and deletes
    it after loading.
    - **Interrupted load:** an interrupted run (SIGINT/SIGTERM while the
      config loads) can leave that file behind, inside the install
      directory.
    - **Why not deleted:** a file name or timestamp cannot prove which
      process wrote it in that shared directory, so test-audit never deletes
      it.
    - **Why not avoided:** Vite's experimental config loader would avoid the
      file, but it is not used.
  - A tsconfig whose incremental setting cannot be determined (both
    `--showConfig` and the relative `extends` chain fail): no flag is
    passed, and a warning says a `.tsbuildinfo` may be written.
- Ecosystem isolation: a TS-only repo runs nothing Python (besides this
  interpreter); a Python-only repo runs nothing Node.
- Ephemeral analyzers: knip uses the repo's `node_modules/.bin/knip`, else
  `pnpm dlx` / `yarn dlx` (Berry) / `bunx` / `npx --yes` `knip@6` (by package
  manager). vulture and deptry use `uvx`. A missing analyzer or a network failure
  is `unavailable` (exit 2), never a pass.

## Global options

| Option | Meaning |
| --- | --- |
| `--repo PATH` | repo root; default: git top-level of cwd |
| `--json` | one JSON envelope on stdout (schema below); runner output captured, progress on stderr |
| `--config PATH` | default `<repo>/.test-audit.json` when present |
| `--dry-run` | print commands; run nothing that executes tests, checks, or analyzers |
| `--timeout SECONDS` | per-command limit; on timeout, kill the process group and mark `error` |

Commands always run locally, in the owning package root. For a remote run,
run the whole CLI inside the remote checkout.

## Commands

| Command | Does | Exit |
| --- | --- | --- |
| `detect` | ecosystems, roots, package managers, runners and test command, checks, analyzers, config | 0, or 2 on bad config |
| `run FILE...` | each file alone, single worker, its owning root's runner | 0 all pass; 1 any failed; 2 errors only, or a non-test file |
| `changed [--base REF] [--run]` | classify changes; map owner tests; plan checks and tests | plan: 0 (2 if no merge-base). `--run`: 1 any failure, 2 unavailable or error |
| `deadcode [--base REF] [--all]` | knip / vulture / deptry; default: only findings new since base | 1 new findings; 2 analyzer unavailable or failed |
| `loc [PATH...] [--base REF]` | `git diff --numstat` base..worktree + untracked, by class | 0 |
| `loc --totals [PATH...]` | absolute working-tree line counts per class (tracked + untracked) | 0 |
| `baseline [PATH...] [--out FILE] [--allow-dirty]` | HEAD SHA, per-file results, LOC; refuses a dirty tree | 0; 1 any failed; 2 dirty or errors |
| `ledger [PATH...] [--format md\|csv\|json] [--expand-rows]` | every test declaration with blank mark and evidence columns | 0 |
| `mutate --file P (--replace OLD --with NEW [--occurrence N] \| --patch F) --test T [--test T ...] [--test-cmd CMD] [--skip-precheck]` | mutate, run keepers, restore bytes | 0 CAUGHT; 1 MISSED; 2 ERROR or refused |

CLI paths resolve against the cwd first, then the repo root. When several
outcomes apply, the exit code is 1 if anything failed, else 2 if anything
could not run, else 0.

- `run`
  - Plans every file before running any; a non-test file aborts with exit 2.
  - Streams output; `--json` captures it and keeps a 40-line `output_tail`.
- `changed`
  - **Base** (also `loc`, `deadcode`): always merge-base(HEAD, ref). The ref
    is `--base`, else config `baseBranch`, then `origin/HEAD`, `origin/main`,
    `origin/master`, `main`, `master` (first that exists).
    - **No merge-base** (shallow clone, unrelated history): exit 2 with
      guidance (`git fetch --deepen=N` / `--unshallow`, or `--base <ancestor
      sha>`). It never falls back to a direct diff.
  - **Scope:** the union of committed (base..HEAD), staged, unstaged, and
    untracked-not-ignored paths, with `--no-renames` (a rename is delete +
    add).
    - **Rows:** each row lists its `sources`. `net_change: false` marks a
      path whose edits cancel out (e.g. staged then reverted); its owners are
      still planned.
    - **Deletions:** paths missing from the worktree are listed separately,
      with their sources.
  - **Plan:** tests = touched tests + owner tests. Each check gets its files
    relative to the nearest root that configures it. `tsc`/`tsgo` take no file
    arguments and run per project.
- `deadcode`
  - **Analysis:** knip runs once at the top `package.json`, or else per
    outermost JS root, with `--reporter json --no-progress --no-exit-code`;
    the repo's knip config is honored. deptry runs `deptry . --json-output`.
  - **vulture paths:** without `[tool.vulture].paths`, vulture scans `.`
    (excluding `.venv,venv,node_modules,.tox,.nox,build,dist,.git,__pycache__`)
    plus a whitelist: `whitelist.py` / `vulture_whitelist.py` /
    `.vulture_whitelist.py` if present, or config `vultureWhitelist`.
    - **Configured paths:** with `paths` set and no extra whitelist, nothing
      positional is passed and vulture uses its config. Any positional
      argument replaces the configured `paths`, so with a whitelist the
      configured paths are passed explicitly too: `vulture src whitelist.py`.
  - **Diff:** findings are keyed by (analyzer, root, type, file, name);
    line numbers are ignored, so edits that only move code produce no new
    findings. `resolved` lists base-only findings.
  - **Base sets:**
    - **Absent directory:** a root whose directory is absent at base has an
      empty base finding set.
    - **Existing directory:** if the directory exists, vulture scans it at
      base even when the root marker (e.g. `pyproject.toml`) only appears at
      head, so pre-existing findings are not reported as new.
    - **Missing manifest:** knip needs `package.json` and deptry needs
      `pyproject.toml`. Without them at base, the base set is empty and
      `reason` says so.
  - **vulture:** only exit 0 (clean) or 3 (findings) with nothing but
    finding lines counts. Any other exit, or any diagnostic line (e.g. `invalid
    syntax`; vulture still exits 3 then), is `error`, with `diagnostics` kept.
  - **Base tree:** `git worktree add --detach <tmp>/tree <base>` outside the
    repo. Head's `node_modules` dirs (top + each JS root) and Yarn PnP files
    are symlinked in. The links, the worktree, and the temp dir are removed in
    `finally`, then `git worktree prune`.
  - **Base limits:** the base tree resolves packages through head's installs;
    a dependency added or removed in the change can skew base `dependencies` /
    `unlisted` findings. Python analyzers need no env; deptry maps package names
    to modules by name only, so `--all` may show DEP001/DEP003 noise (the diff
    cancels it).
- `loc`
  - **PATH:** limits either mode to those files/dirs. Classes:
    production, test, support, docs, config, generated, other (config
    `ignore` paths are listed in `ignored`, not counted).
  - **`--totals`:** counts `{files, lines}` per class over tracked +
    untracked-not-ignored files that exist in the working tree. Binary files
    count as files with 0 lines and are listed in `binary`. Use it for final
    scoped counts; baseline `.loc` has the same classes and shape, so the two
    compare key for key.
- `baseline`
  - Without `--out`, the JSON envelope goes to stdout (implies `--json`).
  - Dirty means `git status --porcelain` is non-empty; untracked files count.
  - Test files run with output captured; tails are kept only for failures.
  - `failing` is kept apart, with the note that these are likely product bugs.
- `mutate`
  - **Target:** refused (exit 2, before any keeper runs) for a supplied path
    that is itself a symlink (checked before resolving), paths outside the
    repo, non-regular files, and `.git/`. `--replace`
    must match exactly once unless `--occurrence N`. `--patch` must touch only
    `--file`; it is applied with `git apply` to a temp copy.
  - **Sequence:** precheck (keepers must pass unmutated, else ERROR); save a
    backup outside the repo (path printed on stderr); write; run keepers;
    restore in `finally`.
  - **Writes:** both writes walk from the repo root to the target's
    directory one component at a time.
    - **Component check:** each component is opened with
      `O_DIRECTORY|O_NOFOLLOW` relative to the previous one (`dir_fd`), so
      every component must be a real directory, not a symlink. Nothing is
      checked by path and then opened again.
    - **Missing directories:** on restore they are recreated inside that
      verified chain.
    - **Rename:** the bytes go to a temp file in that directory, which is
      renamed over the target name. The rename replaces whatever is there
      without following it, so a deleted target comes back and a swapped-in
      symlink cannot redirect the write.
    - **Refused restore:** if a component was swapped (e.g. `src/` replaced
      by a symlink to a directory outside the repo), nothing is written. The
      verdict is ERROR (exit 2) with `restored: false`, and `backup` / `reason`
      give the path of the saved original bytes.
    - **Verification:** mode bits and mtime are restored; then the target
      must be a regular file with the original sha256 and mode.
  - **Processes:** after every keeper run, and on SIGINT/SIGTERM/SIGHUP, the
    runner's whole process group gets SIGTERM. Then, after a grace period
    (3 s after a normal exit, 1.5 s on interrupt), it gets SIGKILL, and the
    command waits up to 5 s for the group to be gone before it restores.
    - **Typical timing:** on Ctrl-C the command restores, verifies, and exits
      130 within a few seconds.
    - **Never lost:** a SIGINT/SIGTERM/SIGHUP during `run`, `baseline`,
      `changed --run`, or `mutate` always ends the command. It exits 130 /
      143 / 129 and is never turned into a verdict:
      - **Handlers always installed:** SIGINT and SIGTERM get handlers even
        when inherited as ignored. A background job of a non-interactive
        shell (`test-audit mutate ... &` in a script) starts with SIGINT
        ignored. SIGHUP stays ignored only if it was inherited as ignored
        (`nohup`).
      - **Sliced wait:** the main thread waits for the runner in 0.2 s
        slices, so a signal the OS delivers to another thread still takes
        effect within 0.2 s.
    - **During cleanup:** a signal that arrives during teardown or restore is
      recorded at the Python-handler level, so it doesn't matter which
      thread received it. It is re-raised once the teardown and restore
      finish.
    - **Zombies:** a zombie (state `Z` in `/proc/<pid>/stat` on Linux)
      counts as gone. Orphans are reparented to PID 1, and in a container
      whose PID 1 does not reap they stay zombies forever.
    - **Not tracked:** a descendant that calls `setsid()` escapes the group.
      SIGKILL of test-audit itself cannot be handled: recover from the
      printed backup.
  - **Verdict:** CAUGHT if any keeper `failed`, else ERROR if any `error`,
    else MISSED.
  - **`--test-cmd`:** a template run with cwd = repo root. `{files}` runs once
    with all keepers; `{file}` runs once per keeper.

## Pass, fail, or error

| Runner | failed | error |
| --- | --- | --- |
| pytest | exit 1 | exit 2 collection/interrupt, 3, 4, 5 no tests, pytest missing |
| Vitest / `vp test` | `Tests ... N failed`, or unhandled `Errors` with tests run | `No test files found`; `Tests no tests` (load error); no summary; filter matched >1 file |
| Jest | `Tests: N failed` | `Test suite failed to run`, `Tests: 0 total`, `No tests found` |
| bun | `(fail) name` lines | `N error` with no failing test (load error) |
| custom | any nonzero exit | `testErrorExitCodes`, 126/127, exit 128+N (below) |
| all | | killed by a signal, runner not found, timeout |

Signal deaths:

- **Negative exit:** always an error (the kernel saw the signal), checked
  first.
- **Exit 128+N:** N is a signal number on this system. It is what a shell
  reports for a killed child (`sh -c 'cmd; exit $?'` after SIGKILL = 137),
  but a runner can also exit that way on purpose (Jest `testFailureExitCode:
  130`). So the runner's own result decides first:
  - **Summary found:** a Jest/Vitest/bun summary, or pytest's exit codes
    0-5, give passed/failed/error as usual.
  - **No framework result:** only then is 128+N an error ("killed by
    signal N"). That covers custom `--test-cmd`/`testCommand` commands, and
    known runners that exit without a summary.

`changed --run` checks have no framework result: a check killed by a signal
(negative, or 128+N) is `error` (exit 2), not `failed`. So a check tool that
legitimately exits 129-192 is reported as a signal death.

## Detection

- **Roots:**
  - **JS:**
    - Top `package.json`, plus workspace members from `pnpm-workspace.yaml`
      `packages` (with `!` excludes), else `package.json#workspaces`.
    - Without a top `package.json`: the outermost `package.json` dirs outside
      fixture/test/vendor/dist dirs.
  - **Python:**
    - Top `pyproject.toml` / `setup.py` / `setup.cfg` / `requirements*.txt`
      / `uv.lock` / `pytest.ini` / `tox.ini` / `conftest.py`, plus members of
      `[tool.uv.workspace]`.
    - Falls back to the outermost `pyproject.toml` dirs; Python test files
      alone make a top root.
- **Owning root:** a file belongs to the nearest root of its ecosystem that
  has runner signals of its own. Packages without any defer to the nearest
  ancestor that has them (e.g. a root Vitest `projects` config).
- **Package manager:**
  - **JS:** `packageManager` (root, then top), else a lockfile:
    `pnpm-lock.yaml` > `package-lock.json`/`npm-shrinkwrap.json` > `yarn.lock` >
    `bun.lock`/`bun.lockb` (more than one gives a warning), else npm. Yarn
    reports `pnp` when `.pnp.cjs` exists.
  - **Python:** `uv.lock` or `[tool.uv]` gives uv; then poetry, pdm, else pip.
- **Runner signals per root:**
  - **Strong:** `vitest.config.*` / `vitest.workspace.*`; `vite.config.*`
    with `test:`; `vite.config.*` importing `vite-plus`; `jest.config.*`;
    `package.json#jest`; `bunfig.toml [test]`; the runner named in
    `scripts.test`.
  - **Weak:** a dependency on `vitest` / `vite-plus` / `jest` / `@types/bun`.
  - **Resolution:** strong signals win. Vitest and Vite+ are one family
    (`scripts.test` picks between them, else `vite-plus` means `vp`). A root
    with no signals whose package manager is bun uses `bun test`. Python
    roots use pytest (pytest also runs unittest).
- **Ambiguity:** with more than one family (e.g. Jest + Vitest configs), the
  root is reported `ambiguous`, and each file resolves by the nearest runner
  config walking up from its directory.
  - **Config passed:** for Jest, Vitest, and `vp test`, that config is
    passed as `--config <path>` so the runner loads the config that selected
    it.
  - **Unresolved:** such files are `error` with a message naming the config
    keys; nothing is guessed silently.
- **pytest settings:** `python_files`, `python_classes`,
  `python_functions`, and `testpaths` come from pytest's active config.
  - **Discovery:** walking up from the test file's directory, the first match
    in each directory, in pytest's order: `pytest.toml`, `.pytest.toml`,
    `pytest.ini`, `.pytest.ini` (these win even when empty), `pyproject.toml`
    (`[tool.pytest]` / `[tool.pytest.ini_options]`), `tox.ini [pytest]`,
    `setup.cfg [tool:pytest]`.
    - **Verified:** pytest 9.2 honors all four `pytest.*` / `.pytest.*`
      files.
    - **Root marker:** each of them also marks a Python root.
  - **Shared:** `run`, `ledger`, `baseline`, and `changed` use the same
    settings.
    - **Empty lists:** a key that is present wins even when empty
      (`python_functions = []` collects nothing, as in pytest).
    - **`testpaths` scope:** only argument-less discovery uses it, as in
      pytest. `baseline` and `ledger` without PATH apply it. An explicit
      file (`run other/test_b.py`), a PATH (`ledger other`), and owner
      mapping in `changed` ignore it.
    - **`testpaths` expansion:** this mirrors pytest 9.2's
      `Config._decide_args`, assuming pytest is invoked from the config's
      directory. Each entry is expanded with `glob.iglob(entry,
      recursive=True)` from that directory. If nothing matches at all,
      pytest warns and searches recursively instead, and so does
      test-audit.
  - **Approximations:** this is a model of pytest's selection, not pytest
    itself.
    - **Patterns:** `python_files` patterns are matched against the
      basename, or against the path if they contain `/`.
    - **Not modeled:** `conftest.py` hooks (`pytest_ignore_collect`,
      `collect_ignore`), `norecursedirs`, plugins, and pytest's rootdir rule
      when invoked from elsewhere. The ledger's own limits are under "Ledger
      limits".
  - **Reported:** `detect` shows each root's `pytest_config`.
- **Commands:**

  | Runner | Command |
  | --- | --- |
  | Vitest | `node_modules/.bin/vitest run <abs file> <flag> --no-cache --no-fsModuleCache [--config C]` |
  | Vite+ | `vp test run <abs file> <flag> --no-cache --no-fsModuleCache [--config C]` (local bin, else global `vp`) |
  | Jest | `jest --runInBand --cacheDirectory <temp> --runTestsByPath <file> [--config C]` |
  | bun | `bun test ./<file>` (a single file runs in one process; no flag) |
  | pytest | `env PYTHONDONTWRITEBYTECODE=1 <py> -m pytest <file> -p no:cacheprovider [-n 0]` |

  - **Tool lookup:** the nearest `node_modules/.bin`; under Yarn PnP with a
    declared dependency, `yarn run --binaries-only <bin>`.
  - **Python interpreter:** `$VIRTUAL_ENV`, then `<root>/.venv`, then
    `<repo>/.venv`, then `python3` on PATH. It is never created.
  - **From `scripts.test`:** leading `KEY=VAL` / `cross-env` assignments
    (passed via `env`) and `--config`/`-c` are reused.
- **Single-worker flags:** read from the installed `--help`, not assumed.
  - **Vitest / `vp test`:** `--maxWorkers=1` (+ `--minWorkers=1` if listed),
    else `--no-file-parallelism`, else `--threads=false`.
  - **Jest:** `--runInBand`, else `--maxWorkers=1`.
  - **pytest:** `-n 0` only when `--numprocesses` appears, i.e. xdist is
    active; this overrides `addopts = "-n auto"`.
  - **Unverified:** when `--help` cannot run, the fallback is used and
    `flags_verified: false`.
- **Checks (configured only):**

  | Check | Trigger | Command |
  | --- | --- | --- |
  | oxlint | dependency or `.oxlintrc.json` | `oxlint FILES` |
  | eslint | config file or `eslintConfig` | `eslint FILES` |
  | biome | `biome.json(c)` or dependency | `biome check --no-errors-on-unmatched FILES` |
  | prettier | config or dependency | `prettier --check --ignore-unknown FILES` |
  | oxfmt | dependency or `.oxfmtrc.json` | `oxfmt --check FILES` |
  | `vp check` | `vite-plus` dependency | `vp check --no-error-on-unmatched-pattern FILES` |
  | tsc | `tsconfig.json` + `typescript` | `tsc --noEmit -p tsconfig.json [--tsBuildInfoFile <temp>]` |
  | tsgo | `tsconfig.json` + `@typescript/native-preview` | `tsgo --noEmit -p tsconfig.json [--tsBuildInfoFile <temp>]` |
  | ruff | `[tool.ruff]`, `ruff.toml`, or dependency | `ruff check --no-fix --no-cache FILES` and `ruff format --check --no-cache FILES` |
  | mypy | `[tool.mypy]`, `mypy.ini`, or dependency | `mypy --cache-dir /dev/null FILES` |
  | pyright | `[tool.pyright]`, `pyrightconfig.json`, or dependency | `pyright FILES` |

  - **Python tools:** come from the project venv. ruff alone may fall back to
    `uvx ruff@<uv.lock version>`.
  - **Missing:** a missing tool is `available: false` and `unavailable` under
    `--run`.

## Classification

First match wins: config `classify.ignore` (dropped), config globs in the order
test, support, generated, docs, config, production; config `testGlobs`; then:

| Class | Built-in rule |
| --- | --- |
| generated | lockfiles, `*.min.*`, `*.map`, `*.snap`, `*_pb2.py`, `*.gen.*`/`*.generated.*`, dirs `__snapshots__`, `__generated__`, `generated`, `dist`, `coverage` |
| test | `*.test.*`/`*.spec.*` (JS/TS, incl. `.test-d.ts`); `*_test`/`*_spec` under bun; `__tests__/**` under Jest; Python files matching the active pytest config's `python_files` (default `test_*.py`, `*_test.py`); `testpaths` only narrows argument-less discovery. Never inside fixture dirs |
| support | `conftest.py`, setup/test-utils/helpers/`*.fixture(s)`/`*.mock(s)` names, anything under `fixtures`, `__fixtures__`, `__mocks__`, `mocks`, `testdata`, `test-utils`, `test_helpers`...; non-test files under `test`, `tests`, `__tests__`, `spec`, `e2e`, `testing` |
| docs | `.md .mdx .rst .adoc .txt` (not `requirements*.txt`), README/LICENSE/CHANGELOG..., top-level `docs/` |
| config | manifests, tsconfig, tool configs, `*.config.*`, `*.toml/.ini/.cfg/.yml`, `.github/`, `Dockerfile`, `Makefile`, `.test-audit.json` |
| production | source extensions (JS/TS, Python, Go, Rust, CSS, shell, SQL, ...) |
| other | everything else |

- **Globs:** match whole repo-relative paths. `**` spans directories; `*`,
  `?`, `[...]`, and `{a,b}` do not cross `/`.
- **Owner tests:** require the same stem and ecosystem.
  - **Sibling:** `x.test.ts`, `x.spec.ts`, `x_test.ts`, `test_x.py`,
    `x_test.py` in the same dir.
  - **`__tests__`:** `__tests__/x.*` under the same dir.
  - **Tests mirror:** a `tests/` / `test/` / `spec/` tree whose subpath is a
    segment suffix of the production dir (or the reverse), after stripping a
    leading `src/` / `lib/` / `app/` / `source/`.

## Config `.test-audit.json`

```json
{
  "baseBranch": "main",
  "testCommand": "pnpm exec vitest run {file} --maxWorkers=1",
  "testErrorExitCodes": [2, 3, 4, 5],
  "roots": {"packages/legacy": {"runner": "jest"}, "services/api": {"python": ".venv/bin/python"}},
  "runners": [{"glob": "e2e/**", "testCommand": "make e2e FILE={file}"}, {"glob": "src/**", "runner": "vitest"}],
  "checks": {"eslint": null, "lint-docs": "pnpm markdownlint {files}"},
  "classify": {"support": ["src/testing/**"], "generated": ["src/gen/**"], "ignore": ["vendor/**"]},
  "testGlobs": ["integration/**/*.it.ts"],
  "deadcode": {"knipPackage": "knip@6", "vulturePackage": "vulture", "deptryPackage": "deptry",
               "vultureMinConfidence": 80, "vultureWhitelist": ["tools/vulture_allow.py"], "disable": ["deptry"]}
}
```

- **Command precedence:** `runners` glob > `roots.<path>.testCommand` >
  `roots.<path>.runner` > `testCommand` > detection.
- **Templates:** split with shell rules and run without a shell, with cwd =
  owning root. A `{files}` token expands to one argument per file, `{file}`
  is the path relative to cwd, and `{root}` is the root.
  - **Whole word:** a word that is exactly a placeholder becomes raw argv
    entries; no shell sees it.
  - **Embedded:** a placeholder inside a larger word (e.g. the script of
    `sh -c '... {file}'`) gets a shell-quoted value. It must be in plain
    position (not inside quotes or backticks, not escaped by a backslash, not
    directly after `$`), and is expanded in one pass, so substituted paths
    are never rescanned.
  - **Checks:** config `checks` follow the same rules.
  - **Shell features:** wrap the command in `sh -c '...'`.
- **`checks`:** `null` disables a built-in check. A new name runs at the
  repo top on all changed files (repo-relative).
- **Validation:** the config is checked when it loads, before any command
  runs. Each of these gives a JSON usage error, exit 2:
  - an unknown key at any level (see the `$` rule below), or a wrong type
    (e.g. `"roots": null`, `"checks": 2`, `"roots": [...]`);
  - a non-string or empty list element, or a NUL byte in any string or key;
  - an out-of-range number or exit code;
  - an unknown runner or analyzer name;
  - a glob that does not compile (e.g. `[z-a]`);
  - a command template that does not parse (e.g. an unbalanced quote), or
    has an embedded placeholder in an unsafe position. The same applies to
    `--test-cmd`.

  These are what is checked; it is not a guarantee that a valid config
  cannot fail later. For example, a root path that matches no package is
  only a warning, and a valid command can still fail when it runs.

  `detect` reports the applied keys.
- **`$` keys:** top-level keys starting with `$` (`$schema`, `$comment`, any
  `$...`) are metadata, accepted and ignored, with values unchecked. Inside
  any nested object, `$` keys are unknown keys like any other.

## JSON output

Every `--json` run prints one object (keys sorted, stable across Python
versions):

```json
{"schema_version": 2, "tool": "test-audit", "version": "1.0.0", "command": "run",
 "repo": "/abs/repo", "dry_run": false, "exit_code": 1, "warnings": [],
 "result": {...}}
```

- **Schema version 2:** the test result record no longer has `cwd`. It only
  differed from `root` under the removed `--via` option; commands run in
  `root`.
- **Errors:** an error has `error: {"kind": "usage"|"environment", "message"}`
  instead of `result`. Errors raised before the repo resolves omit `repo` and
  `dry_run`.
- **Paths:** repo-relative POSIX; `root` is `.` for the top.
- **Test result record:** shared by run, baseline, `changed --run`, and mutate:
  `{file, root (owning root, where the command runs), runner, command, status
  passed|failed|error|dry-run, exit_code, duration_s, reason, output_tail?}`.

| Command | `result` |
| --- | --- |
| detect | `ecosystems`, `roots[]` (`path, ecosystem, name, package_manager{name,version,source,pnp?}, runner, runner_source, runners_detected[{runner,signals,strong}], ambiguous, test_script, pytest_config?{path,dir,python_files,python_classes,python_functions,testpaths}, checks[{name,root,command,accepts_files,available,source}], test_command{runner,command,single_worker_flags,flags_verified,notes,error}, overrides?`), `workspaces{js,python}[{source,patterns,members}]`, `config_checks`, `deadcode_analyzers[{name,root,ecosystem,command,source,available}]`, `config{path,applied}` |
| run | `results[]`, `summary{passed,failed,error,total,duration_s}` |
| changed | `base, base_ref, files[{path,class,status added\|modified\|untracked,sources[committed\|staged\|unstaged\|untracked],net_change}], deleted[{path,class,sources}], owners{prod:[{test,rule}]}, unmapped_production, support_changed, plan{checks[{name,root,command,files,available,source}], tests[{file,reasons,root,runner,command,error}]}`, plus `results{checks[],tests[]}` with `--run` |
| deadcode | `mode new\|all, base, base_ref, analyzers[{name,root,command,status ok\|unavailable\|error\|dry-run,reason,head_findings,base_findings?,new_findings?,diagnostics?,output_tail?}], findings[{analyzer,root,type,file,name,line?,...}], resolved[]` |
| loc | diff: `mode "diff", scope, base, base_ref, files[{path,class,added,removed,binary,untracked}], by_class{class:{files,added,removed,net}}, totals, binary, ignored`; `--totals`: `mode "totals", scope, by_class{class:{files,lines}}, totals{files,lines}, binary, ignored` |
| baseline | `kind, head, dirty, scope, files[], summary, failing[{file,status,reason}], failing_note, loc{class:{files,lines}} (all 7 classes, same as loc --totals by_class), loc_totals`; with `--out`, `files` stays in the file and `result.out` names it |
| ledger | `files, declarations[{file,line,name,path[],kind test\|each\|row,rows,modifiers,mark,evidence,row_ids?,row_index?,row_id?,suite_rows?}], errors, counts` |
| mutate | `file, sha256, line, diff[], tests, commands, precheck[], results[], restored, backup? (only when the restore was refused or failed), verdict CAUGHT\|MISSED\|ERROR\|DRY-RUN, reason?` |

## Ledger limits

- **Ordering:** files sorted by path; declarations by line, then column.
  Names nest with ` > ` (JS) or `::` (Python). The mark and evidence columns
  are empty, for the lane agent.
- **JS/TS:** a token scanner, not a parser; it skips strings, comments,
  templates (with nested `${}`), and regex literals.
  - **Found:** `describe/suite/context/it/test/specify` (plus
    `f`/`x`-prefixed forms) with `.skip .only .todo .concurrent .sequential
    .fails .each .for .skipIf() .runIf()`.
  - **Rows:** counted for array-literal `.each([...])` and for template
    tables (non-blank lines after the header). An identifier table gives
    `rows: null`.
  - **Missed:** aliases (`const t = test.extend(...)`), helper-generated
    tests, and computed names (the source text is shown).
  - **JSX:** quotes inside JSX text can hide declarations on that line.
- **Python (`ast`):**
  - **Found:** module functions matching `python_functions` (default
    `test*`); classes matching `python_classes` (default `Test*`) without
    `__init__`; and `unittest.TestCase` subclasses (by base name).
  - **Rows:** `@pytest.mark.parametrize` row counts multiply across stacked
    and class-level decorators. Non-literal argvalues give `rows: null`.
  - **Ids:** literal `ids=[...]` and `pytest.param(id=...)`; otherwise
    pytest-style ids from literal values (`a-0`), else `argname<index>`.
  - **Missed:** `pytest_generate_tests`, fixture params, plugin-generated
    items.

## Examples

TS monorepo (pnpm + Vitest):

```sh
$S detect                                         # roots, runner, flags, checks
$S run packages/web/src/cart.test.ts              # alone, --maxWorkers=1
$S changed                                        # plan; then:
$S changed --run --json > /tmp/changed.json       # exit 1 on any failure
$S deadcode                                       # new knip findings vs merge-base
$S baseline packages/web --out /tmp/web-baseline.json
$S ledger packages/web --format csv > /tmp/web-ledger.csv
$S mutate --file packages/web/src/cart.ts --replace 'total + tax' --with 'total' \
   --test packages/web/src/cart.test.ts            # expect CAUGHT
$S loc --totals packages/web                       # final scoped counts; compare to baseline .loc
```

Python (uv + pytest):

```sh
$S detect                                         # .venv python; -n 0 if xdist is active
$S run tests/test_billing.py
$S changed --base origin/main --run
$S deadcode --all                                 # every vulture + deptry finding
$S loc
$S mutate --file src/app/billing.py --patch /tmp/off-by-one.diff --test tests/test_billing.py --test tests/test_invoice.py
```
