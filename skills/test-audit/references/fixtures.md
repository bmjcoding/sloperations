---
summary: "Temporary directories, module mocks, database cleanup, background workers, and subprocess fixtures"
title: "Test fixture recipes"
read_when:
  - You need temporary files, module mocks, database teardown, or background-worker cleanup in a test
  - You are building skill reliability evals
---

Use these recipes for the fixture you are changing. Follow the
[test authoring contract](writing-tests.md) for behavior and cost. Vitest
examples also apply to Vite+ projects (`vp test` runs Vitest); each recipe
notes its pytest equivalent.

## Test Temp Directories

Create test-owned temporary directories through one shared helper that
registers cleanup with the test lifecycle, so ownership is explicit and cleanup
stays in the test lifecycle. For example, with a tracker bound to Vitest's
`afterEach`:

```ts
import { afterEach } from "vitest";
import { useAutoCleanupTempDirTracker } from "../helpers/temp-dir.js";

const tempDirs = useAutoCleanupTempDirTracker(afterEach);

it("uses a temp workspace", () => {
  const workspace = tempDirs.make("example-");
  // use workspace
});
```

The tracker should expose no manual cleanup method: the test runner owns
cleanup after each test. Avoid new usage of older manual create/cleanup helpers
and new bare `fs.mkdtemp*` calls unless a test is explicitly verifying raw
temp-dir behavior. When a bare temp dir is genuinely needed, add an auditable
allow comment with a reason:

```ts
// temp-dir: allow verifies raw fs cleanup behavior
const workspace = fs.mkdtempSync(prefix);
```

If the repo reports new bare temp-dir creation or manual helper usage in
changed test paths, check that report for your diff.

pytest: use the built-in `tmp_path` (per test) or `tmp_path_factory` (per
session) fixtures; pytest owns cleanup. Treat a bare `tempfile.mkdtemp()` like
a bare `fs.mkdtemp*`: allowed only with the same reason comment.

```python
def test_uses_temp_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # use workspace
```

Copy fixture trees whose files a test later executes directly (stubs on `PATH`,
shebang wrappers, native binaries) with a helper that copies files
close-on-exec, not a recursive `fs.cpSync` without a `filter`. On Node 24 that
copy path opens files without close-on-exec, so a child forked by another test
worker thread mid-copy keeps the file writable and a later `execve` fails with
`ETXTBSY`.

## Agent reliability evals (skills)

CI-safe tests that drive the real agent entry point with a mock model provider,
and assert admission, run-ID responses, abort requests, session wiring, and
config effects, are the base for agent reliability evals.

Skill evals should cover:

- **Decisioning:** when skills are listed in the prompt, does the agent pick the right skill (or avoid irrelevant ones)?
- **Compliance:** does the agent read `SKILL.md` before use and follow required steps/args?
- **Workflow contracts:** multi-turn scenarios that assert tool order, session history carryover, and sandbox boundaries.

Start evals with fixed inputs and mock providers:

- A scenario runner using mock providers to assert tool calls + order, skill file reads, and session wiring.
- A small suite of skill-focused scenarios (use vs avoid, gating, prompt injection).
- Optional live evals (opt-in, env-gated) only after the CI-safe suite is in place.

## Module mocks and export completeness

New first-party module mock factories (for example Vitest `vi.mock` and
`vi.doMock`) should preserve the real module's exports when the fixture only
needs to override a few functions:

```ts
vi.mock("./runtime.js", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./runtime.js")>()),
  start: vi.fn(),
}));
```

Use pass-through for **export completeness** only. `importOriginal` and
`vi.importActual` can return a separate module instance for stateful singletons;
they do not guarantee shared state or lifecycle identity. Keep a closed mock when
isolating real state or initialization is the fixture's purpose, and explain that
contract on the line immediately above the mock call:

```ts
// mock-isolation: Keep the database and process-wide cache outside this fixture.
vi.mock("./runtime.js", () => ({ start: vi.fn() }));
```

pytest: patch only the names the fixture overrides, with
`unittest.mock.patch(..., autospec=True)` or `create_autospec`, so mocks keep
the real module's attributes and signatures; the rest of the module stays
real. Patch where the subject looks the name up. A closed mock replaces the
whole module reference with an autospec and takes the same comment rule:

```python
from unittest.mock import patch

@patch("app.service.runtime.start", autospec=True)
def test_boot(start): ...

# mock-isolation: Replace runtime calls; real module initialization is allowed.
@patch("app.service.runtime", autospec=True)
def test_boot_isolated(runtime): ...
```

Patching a module reference happens after import, so autospec does not stop
the real module's initialization; use it only when initialization is safe. To
keep import-time state out of the fixture, install a controlled fake in
`sys.modules` (for example with `patch.dict(sys.modules, {...})`) before
importing the subject, and restore import state afterward.

If the repo checks mock export completeness against a baseline of existing
unannotated factories, new or changed factories cannot borrow another site's
allowance. After removing or annotating existing factories, prune the baseline
so it only shrinks. Commit an isolation annotation together with the baseline
entry it retires. A whole-checkout prune can drop unrelated entries, so inspect
the full baseline diff, then confirm the result with a check run that does not
prune.

## Raw SQLite state access

Closing the main database handle synchronously may not close every connection.
Worker-backed state writes can keep a worker connection whose retirement only
starts at that call; its final close checkpoints and deletes the WAL under an
exclusive lock at an arbitrary later time. Before opening the database with a
raw connection (such as Node's `DatabaseSync`), or copying, hashing, or
snapshotting its files, await the owner's full asynchronous close, including
worker connections, and clear any failure latches the test helpers keep.
Otherwise the raw connection can fail with `SQLITE_BUSY`, or the snapshot can
change underneath the test. An exclusive transaction (`BEGIN EXCLUSIVE`) on a
raw connection only shows the lock was free at that moment; it does not prove
every other connection closed. Prove closure through the owner's shutdown
completion.

Python's `sqlite3` follows the same rule: close the owner, including connections
held by worker threads, before `sqlite3.connect` on the file or copying it.
`with sqlite3.connect(...) as conn:` only commits or rolls back; it does not
close, so call `conn.close()` explicitly.

## Background workers and watchers

When several operations share a background discovery or watcher worker, close
it in `afterEach` and await its shutdown before removing its temporary roots or
restoring fake timers. Shared-worker test lanes should also drain such
singletons between files.

Disable passive refresh or watch mode when a test does not exercise it. That
stops periodic scans; initial discovery and explicit changes still use the
worker and must finish before cleanup.

pytest: own the worker in a yield fixture whose teardown closes it and waits
for shutdown. Request the temp-root fixture from it so teardown order closes
the worker before the root goes away:

```python
@pytest.fixture
def watcher(tmp_path):
    w = start_watcher(tmp_path, watch=False)
    yield w
    w.close()  # returns after the worker thread has joined
```

## Subprocesses, preparation, and timeouts

When a test instance boots a server, wait for its readiness and startup probes,
not just process launch. Back off failed probes (for example from 10 ms to at
most 500 ms) within the original startup deadline instead of extending it.

- Do expensive one-time preparation, such as compiling subprocess workers, at
  collection or suite setup. Preparing inside a test or hook, for example
  through an `await import()` whose graph triggers it, spends that cost (tens of
  seconds warm, minutes cold) inside the test or hook deadline. Import the
  subject statically; suites that re-import it per test add a side-effect
  preload that prepares at collection. Add the preload only to suites that
  already need the preparation.
- Nested shutdown fixtures prepare once during collection, then borrow the
  prepared artifacts. Keep each owner joining its child before returning the
  loan; do not put cold compilation inside a shutdown deadline.
- pytest: put one-time preparation in a `scope="session"` fixture (with
  `tmp_path_factory` for its output) and have tests request it; never prepare
  inside the test body.
- Let servers bind port 0 and read the assigned port back; never hard-code
  ports in either runner.

Do not race operations against ad-hoc timeout helpers. Await a completion gate
before settlement, bound the work with the test's abort signal, or use fake
timers through the owner's injected clock. In Python, do not race with ad-hoc
`asyncio.wait_for` or sleeps either; drive the owner's injected clock, and use
`time-machine` or `freezegun` only through that clock. If the repo keeps a
baseline of legacy timeout races, it only shrinks: prune it after removals.
