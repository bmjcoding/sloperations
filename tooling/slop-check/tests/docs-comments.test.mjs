// Check 1, docs and comments: refs, two-tier history, markers and inline suppressions.
// Test names carry the critique finding ids they encode (reviewer and number).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { append, edit, expectClean, expectFinding, expectIncomplete, fixture, hasFinding, run, tempDir, write } from './helpers.mjs';

const docs = (dir, extra = []) => run(['check', '--only', 'docs', ...extra], dir);
const comments = (dir, extra = []) => run(['check', '--only', 'comments', ...extra], dir);

test('the clean TypeScript fixture passes with no output (zero-false-positive controls)', () => {
  // Controls: "can no longer be approved" (Tier 2), "is used to seed" (be-form), a quoted UI string,
  // a block quote, a code fence, inline code, an ADR narrating history, the external citation, a build
  // output path, a vendored tarball path, JSX text, regex and template literals, TODO(#12), a code span
  // as link text, and "the key used to sign" (a participle, carried as a baselined row with its reason).
  const res = run(['check'], fixture('ts-repo'));
  assert.equal(res.status, 0, res.stdout + res.stderr);
  assert.equal(res.stdout, '');
});

test('Tier 2 phrases are warnings, shown only with --warnings, and never fail', () => {
  const res = run(['check', '--warnings'], fixture('ts-repo'));
  assert.equal(res.status, 0);
  expectFinding(res, 'docs/history-ambiguous', { path: 'docs/agent-contract.md', message: 'no longer' });
  assert.ok(res.records.every((f) => f.severity === 'warning'));
});

for (const [id, sentence] of [
  ['previously ... now', 'The handler previously retried each call, and now it fails fast.'],
  ['[Neon 15] Previously ... Now across one sentence break', 'Previously, the server polled every second. Now it subscribes to notifications.'],
  ['was renamed', 'The queue table was renamed when the worker split.'],
  ['it used to', 'It used to poll the queue every second.'],
  ['[Muse M7, Grok 4] a noun subject used to', 'The worker service used to poll the queue every second.'],
]) {
  test(`docs/history blocks Tier 1 narration in a reference doc: ${id}`, () => {
    const dir = fixture('ts-repo');
    append(dir, 'AGENTS.md', `\n${sentence}\n`);
    const res = docs(dir);
    assert.equal(res.status, 1, res.stdout);
    assert.equal(res.records.find((f) => f.rule === 'docs/history').line, 6);
  });
}

test('[Neon 15] present-tense passives and be-form "used to" stay out of Tier 1', () => {
  const dir = fixture('ts-repo');
  append(dir, 'AGENTS.md', '\nOnce a session has been removed from the cache, the next call re-authenticates. The token is not used to sign requests.\n');
  expectClean(docs(dir));
});

test('docs/history blocks a version-history heading, but not inside an ADR or a heading that names something [Astra 17]', () => {
  const dir = fixture('ts-repo');
  append(dir, 'README.md', '\n## Version history\n\nNothing yet.\n\n## History API\n\nReturns past events.\n');
  append(dir, 'docs/adr/0001-ledger.md', '\n## History\n\nSee above.\n');
  const res = docs(dir);
  expectFinding(res, 'docs/history', { path: 'README.md', message: 'heading' });
  assert.equal(res.records.filter((f) => f.rule === 'docs/history').length, 1, res.stdout);
});

test('[Grok 1, Muse M5, Astra 5] a second narration in an allowed paragraph, or a swapped one, is new', () => {
  const dir = fixture('ts-repo');
  append(dir, 'README.md', '\nThe queue table was renamed when the worker split.\n');
  assert.equal(run(['baseline', '--write'], dir).status, 0);
  expectClean(run(['check'], dir));
  edit(dir, 'README.md', 'The queue table was renamed when the worker split.', 'The queue table was renamed when the worker split. The status column was renamed in the catalog.');
  const added = run(['check'], dir);
  assert.equal(added.status, 1);
  expectFinding(added, 'docs/history', { status: 'new' });
  edit(dir, 'README.md', 'The queue table was renamed when the worker split. ', '');
  const swapped = run(['check'], dir);
  expectFinding(swapped, 'docs/history', { status: 'new' });
  expectFinding(swapped, 'docs/history', { status: 'stale' });
});

test('docs/refs: a broken link, a source deletion, and the first-party vendor README in scope', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'README.md', '(docs/agent-contract.md)', '(docs/agent-rules.md)');
  expectFinding(docs(dir), 'docs/refs', { path: 'README.md', message: 'docs/agent-rules.md' });
  const del = fixture('ts-repo');
  fs.rmSync(path.join(del, 'server/telemetry.ts'));
  expectFinding(docs(del), 'docs/refs', { path: 'AGENTS.md', message: 'server/telemetry.ts' });
  const ven = fixture('ts-repo');
  edit(ven, 'vendor/README.md', '`vendor/SOURCE`', '`vendor/SOURCES`');
  const res = docs(ven);
  expectFinding(res, 'docs/refs', { path: 'vendor/README.md', message: 'vendor/SOURCES' });
  assert.ok(!res.stdout.includes('ui-kit'), 'the vendored tarball and the extracted payload are declared');
});

test('[Astra 17, Muse L2] an undefined reference link, a missing root-level file and a prose path fail', () => {
  const dir = fixture('ts-repo');
  append(dir, 'AGENTS.md', '\nRead the [release steps][releasing], `CONTRIBUTING.md`, and server/no-such-file.ts first.\n');
  const res = docs(dir);
  expectFinding(res, 'docs/refs', { message: 'reference link [releasing] has no definition' });
  expectFinding(res, 'docs/refs', { message: 'CONTRIBUTING.md' });
  expectFinding(res, 'docs/refs', { message: 'server/no-such-file.ts' });
});

test('[Neon 8, Astra 10] paths cannot resolve outside the tree, by a different case, or through a symlink', () => {
  const dir = fixture('ts-repo'), outside = tempDir('outside');
  write(outside, 'secret.md', 'outside\n');
  fs.symlinkSync(path.join(outside, 'secret.md'), path.join(dir, 'docs/linked.md'));
  fs.symlinkSync(dir, path.join(dir, 'docs/loop')); // a directory cycle the walk must not follow
  append(dir, 'AGENTS.md', `\nSee [outside](../${path.basename(outside)}/secret.md), [case](Readme.md) and [linked](docs/linked.md).\n`);
  const res = docs(dir);
  for (const m of ['secret.md', 'Readme.md', 'docs/linked.md']) expectFinding(res, 'docs/refs', { message: m });
});

test('[Neon 8] concept pairs in code spans are not paths; capitalized and two-slash paths are', () => {
  const dir = fixture('ts-repo');
  append(dir, 'AGENTS.md', '\nThe `docs/history` rule, the `server/client` split and `test/unit` tier. See `server/db/gone/`.\n');
  assert.deepEqual(docs(dir).records.filter((f) => f.rule === 'docs/refs').map((f) => f.message), ['path server/db/gone/ does not exist']);
});

test('[Neon 8, Astra 17] each TODO needs its own tracker; a URL elsewhere in the paragraph does not count', () => {
  const dir = fixture('ts-repo');
  append(dir, 'AGENTS.md', '\nSee https://example.com for style. TODO: finish this section. TODO(#3): add the diagram.\n');
  assert.equal(docs(dir).records.filter((f) => f.rule === 'docs/markers').length, 1);
});

test('[Astra 17] markers are advisory until a repository sets markers = "error"', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'cleanup.toml', 'markers = "error"\n', '');
  append(dir, 'AGENTS.md', '\nTODO: describe the release steps.\n');
  const res = docs(dir, ['--warnings']);
  expectClean(res);
  expectFinding(res, 'docs/markers');
});

test('[Neon 8] a GFM table is read cell by cell, so rows do not join into one sentence', () => {
  const dir = fixture('ts-repo');
  append(dir, 'AGENTS.md', '\n| Item | Note |\n| --- | --- |\n| Item previously used X | note |\n| Unrelated row | now active |\n');
  expectClean(docs(dir));
});

test('Markdown structure: HTML comments, indented code and JSX text are not prose or comments', () => {
  const dir = fixture('ts-repo');
  append(dir, 'AGENTS.md', '\n<!-- The store was renamed; TODO tidy -->\n\n    the store was renamed (an indented code block)\n');
  write(dir, 'client/note.tsx', 'export const Note = () => <p>See // server/gone.ts, TODO soon</p>;\n');
  expectClean(run(['check', '--only', 'docs,comments'], dir));
});

test('comments/refs: a missing file fails; [Fable 11] an ancestor-relative path and an env assignment do not', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/migrate.ts', '// Holds an advisory lock', '// See server/db/lock.ts. Holds an advisory lock');
  expectFinding(comments(dir), 'comments/refs', { path: 'server/migrate.ts', message: 'server/db/lock.ts' });
  const ok = fixture('ts-repo');
  write(ok, 'client/lib/a.ts', 'export const a = 1;\n');
  write(ok, 'client/lib/b.ts', '// Pairs with lib/a.ts; run with NODE_EXTRA_CA_CERTS=/app/ca.pem.\nexport const b = 2;\n');
  expectClean(comments(ok));
});

test('comments/markers blocks an unscoped TODO; tracker IDs inside TODO(...) are exempt from history', () => {
  const dir = fixture('ts-repo');
  write(dir, 'shared/todo.ts', '// TODO tidy this up\n// TODO(R2-C, ABC-12): read in batches\nexport const x = 1;\n');
  const res = comments(dir, ['--warnings']);
  assert.deepEqual(res.records.filter((f) => f.path === 'shared/todo.ts').map((f) => f.rule), ['comments/markers'], res.stdout);
});

test('[Neon 9] a marker in a multi-line comment reports its own line', () => {
  const dir = fixture('ts-repo');
  write(dir, 'shared/block.ts', '/*\n * First line.\n * TODO tidy this\n */\nexport const y = 1;\n');
  assert.equal(comments(dir).records.find((f) => f.rule === 'comments/markers').line, 3);
});

test('comments/history is a warning in code comments, never blocking', () => {
  const dir = fixture('ts-repo');
  write(dir, 'shared/old.ts', '// The parser was rewritten in round 2.\nexport const y = 2;\n');
  expectClean(comments(dir));
  expectFinding(comments(dir, ['--warnings']), 'comments/history', { path: 'shared/old.ts' });
});

test('comments/suppression: a new inline suppression fails until it is a baseline row', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/app.ts', '  const ms', '  // @ts-expect-error -- the request type omits the proxy header\n  const ms');
  const res = comments(dir);
  expectFinding(res, 'comments/suppression', { path: 'server/app.ts' });
  assert.ok(!hasFinding(res, 'comments/unexplained-suppression'));
});

test('[Astra 17, Neon 9] directive grammar: prose mentions are not suppressions; bare and file-level ones are', () => {
  const dir = fixture('ts-repo');
  write(dir, 'shared/s.ts', '// We avoid @ts-ignore and eslint-disable in this module.\n// @ts-ignore\nexport const a: number = 1;\n/* eslint no-console: "off" */\nexport const b = 2;\n');
  assert.deepEqual(comments(dir).records.filter((f) => f.path === 'shared/s.ts').map((f) => `${f.rule}:${f.line}`).sort(),
    ['comments/suppression:2', 'comments/suppression:4', 'comments/unexplained-suppression:2', 'comments/unexplained-suppression:4']);
  const py = fixture('py-repo');
  write(py, 'app/quiet.py', '# pyright: reportUnusedImport=false\n# pyright: basic\n# pyright: strict\nimport os  # noqa\nimport sys  # type: ignore[import-untyped]\n');
  assert.deepEqual(comments(py).records.filter((f) => f.path === 'app/quiet.py').map((f) => `${f.rule}:${f.line}`).sort(),
    ['comments/suppression:1', 'comments/suppression:2', 'comments/suppression:4', 'comments/suppression:5', 'comments/unexplained-suppression:2', 'comments/unexplained-suppression:4']);
});

test('[Fable 5, Astra 5] a suppression binds to the code it silences: moving it onto other code is new', () => {
  const dir = fixture('ts-repo');
  expectClean(run(['check'], dir));
  edit(dir, 'server/telemetry.ts', '  // eslint-disable-next-line no-console -- the logger is the one module that writes stdout\n  console.log(JSON.stringify(record));', '  process.stdout.write(JSON.stringify(record));');
  append(dir, 'server/telemetry.ts', 'export function leak(secrets: unknown): void {\n  // eslint-disable-next-line no-console -- the logger is the one module that writes stdout\n  console.log(secrets);\n}\n');
  const res = run(['check'], dir);
  expectFinding(res, 'comments/suppression', { status: 'new' });
  expectFinding(res, 'comments/suppression', { status: 'stale' });
});

test('[Fable 4] code outside every declared root is incomplete, never invisible', () => {
  const dir = fixture('ts-repo');
  write(dir, 'workers/sync.ts', '// @ts-ignore\nexport const x = 1;\n');
  expectIncomplete(run(['check'], dir), 'workers/sync.ts');
});

test('Python docstrings and comments go through the same rules', () => {
  const dir = fixture('py-repo');
  edit(dir, 'app/db.py', '"""Return every account, oldest first."""', '"""Return every account, oldest first; see app/accounts.py. TODO: page it."""');
  const res = comments(dir);
  expectFinding(res, 'comments/refs', { path: 'app/db.py', message: 'app/accounts.py' });
  expectFinding(res, 'comments/markers', { path: 'app/db.py' });
});
