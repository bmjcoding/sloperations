// The CLI and framing: option and config validation, partial runs, fail-closed errors, a fresh copy
// of the skill, and determinism. Test names carry the critique finding ids they encode.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { append, edit, expectClean, expectFinding, expectIncomplete, fixture, run, SCRIPTS, tempDir, write } from './helpers.mjs';

test('[Muse H1, Neon 5, Fable 8, Astra 12] a mistyped --only is a usage error', () => {
  assert.equal(run(['check', '--only', 'doc'], fixture('ts-repo')).status, 2);
  assert.equal(run(['check', '--only', 'commments'], fixture('ts-repo')).status, 2);
});

test('[Muse H1] a clean --only run that omits a required check is incomplete, never a pass', () => {
  const dir = fixture('ts-repo');
  const res = run(['check', '--only', 'docs'], dir);
  assert.equal(res.status, 3);
  expectFinding(res, 'partial-run', { message: 'comments, logs, schema' });
  assert.equal(run(['check', '--only', 'docs,comments,logs,schema'], dir).status, 0);
});

test('[Astra 12] options are validated per command', () => {
  const dir = fixture('ts-repo');
  for (const args of [['policy', '--only', 'docs'], ['check', '--write'], ['inventory', '--warnings'], ['check', '--onyl', 'docs'], ['bogus']]) assert.equal(run(args, dir).status, 2, args.join(' '));
});

test('[Neon 14] an unknown section or key in cleanup.toml is a usage error, not a silent default', () => {
  for (const [from, to] of [['[checks]', '[check]'], ['vendor_payloads =', 'vendor_payload =']]) {
    const dir = fixture('ts-repo'); edit(dir, 'cleanup.toml', from, to); assert.equal(run(['check'], dir).status, 2, to);
  }
});

test('[Muse H2, Neon 3, Astra 12] a comment inside a multi-line array keeps the rest of the config; an unterminated array is an error', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'cleanup.toml', 'source_roots = ["server", "client", "shared"]', 'source_roots = [\n  "server",  # the API\n  "client",\n  "shared",\n]');
  write(dir, 'client/todo.ts', '// TODO tidy\nexport const t = 1;\n');
  expectFinding(run(['check'], dir), 'comments/markers', { path: 'client/todo.ts' });
  edit(dir, 'cleanup.toml', '  "shared",\n]', '  "shared",\n');
  assert.equal(run(['check'], dir).status, 2);
});

test('[Muse M1] every declared root must exist', () => {
  const dir = fixture('ts-repo');
  fs.rmSync(path.join(dir, 'client'), { recursive: true });
  expectIncomplete(run(['check'], dir), 'declared root missing: client');
});

test('[Fable 17] an internal error is incomplete (exit 3), never the findings exit', () => {
  const dir = fixture('py-repo'), bin = tempDir('fakepy'), fake = path.join(bin, 'python3');
  write(bin, 'python3', '#!/bin/sh\ncat >/dev/null\necho "not json"\n');
  fs.chmodSync(fake, 0o755);
  const res = run(['check', '--only', 'comments'], dir, { env: { SLOP_CHECK_PYTHON: fake } });
  expectIncomplete(res, 'internal error');
});

test('[Muse M9] a fresh copy of the skill without its parsers prints the exact install command for that copy', () => {
  const copy = tempDir('skill');
  fs.cpSync(SCRIPTS, copy, { recursive: true, filter: (src) => !src.includes(`${path.sep}node_modules`) });
  const r = spawnSync(path.join(copy, 'slop-check'), ['check', '--root', fixture('ts-repo')], { encoding: 'utf8' });
  assert.equal(r.status, 2);
  assert.ok(r.stderr.includes(`(cd "${fs.realpathSync(copy)}" && npm ci)`), r.stderr);
});

test('check, inventory and policy output is sorted and byte-identical across two runs', () => {
  const ts = fixture('ts-repo'), py = fixture('py-repo'), base = fixture('ts-repo');
  edit(ts, 'server/app.ts', '"request"', '`request ${routeName}`');
  edit(ts, 'README.md', '(docs/agent-contract.md)', '(docs/nope.md)');
  edit(ts, 'log-contract.json', '"ms"', '"elapsed"');
  edit(py, 'app/db.py', 'log.info("accounts listed",', 'log.info(f"listed {conn}",');
  const cases = [['check', '--warnings'], ['inventory']].flatMap((a) => [[a, ts], [a, py]]).concat([[['policy', '--base-policy-dir', base], ts]]);
  for (const [args, dir] of cases) {
    const a = run(args, dir), b = run(args, dir);
    assert.ok(a.stdout.length > 0, `${args.join(' ')} produced output`);
    assert.equal(a.stdout, b.stdout, `${args.join(' ')} differs between runs`);
    if (args[0] !== 'inventory') { const lines = a.stdout.trim().split('\n'); assert.deepEqual(lines, [...lines].sort((x, y) => (x < y ? -1 : x > y ? 1 : 0))); }
  }
});
