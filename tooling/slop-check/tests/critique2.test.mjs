// The critique-2 must-fix rows (critique-2/fix-list.md), one test per row, each the reviewer's probe.
// Test names carry the row number and the reviewer ids.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { append, edit, expectClean, expectFinding, expectIncomplete, fixture, hasFinding, read, REPO_ROOT, run, write } from './helpers.mjs';

const TEMPLATES = path.join(REPO_ROOT, 'skills', 'slop-cleanup', 'templates');
const inv = (dir) => { const r = run(['inventory'], dir); assert.equal(r.status, 0, r.stdout + r.stderr); return r; };
const row = (res, t, c) => res.records.find((r) => r.table === t && r.column === c);
const removeEmptyDirs = (dir) => { for (const e of fs.readdirSync(dir, { withFileTypes: true })) if (e.isDirectory()) { const p = path.join(dir, e.name); removeEmptyDirs(p); if (!fs.readdirSync(p).length) fs.rmdirSync(p); } };
const outsideRoots = (res) => res.records.some((f) => f.rule === 'incomplete' && f.message.includes('outside every declared root'));

test('[row 1, Fable new 1] baseline --write always writes slop-check.jsonl, so a clone (no empty folders) can snapshot it', () => {
  const dir = fixture('py-repo');
  fs.rmSync(path.join(dir, '.slop'), { recursive: true });
  assert.equal(run(['baseline', '--write'], dir).status, 0);
  assert.equal(read(dir, '.slop/baseline/slop-check.jsonl'), '', 'an empty baseline is an empty file git keeps');
  removeEmptyDirs(dir); // as a clone would have it
  assert.equal(run(['policy', '--base-policy-dir', dir], dir).status, 0);
});

test('[row 2, Fable new 2, Neon 4, Astra M] each shipped template passes its own root check; a root of "." covers the tree', () => {
  const ts = fixture('ts-repo');
  for (const f of ['cleanup.toml', 'slop-rules.mjs', '.oxlintrc.json', 'knip.json']) fs.copyFileSync(path.join(TEMPLATES, 'ts-node', f), path.join(ts, f));
  fs.mkdirSync(path.join(ts, 'scripts'));
  assert.ok(!outsideRoots(run(['check', '--only', 'comments,logs'], ts)), 'ts-node template');
  const py = fixture('py-repo');
  fs.copyFileSync(path.join(TEMPLATES, 'python-node', 'cleanup.toml'), path.join(py, 'cleanup.toml'));
  for (const d of ['frontend/src', 'frontend/test', 'scripts', 'docs']) fs.mkdirSync(path.join(py, d), { recursive: true });
  write(py, 'AGENTS.md', '# Agent rules\n');
  write(py, 'frontend/vendor/README.md', '# Vendored\n');
  fs.copyFileSync(path.join(TEMPLATES, 'ts-node', 'slop-rules.mjs'), path.join(py, 'frontend/slop-rules.mjs'));
  assert.ok(!outsideRoots(run(['check', '--only', 'comments,logs'], py)), 'python-node template');
  const dot = fixture('ts-repo');
  edit(dot, 'cleanup.toml', 'roots = ["README.md", "AGENTS.md", "docs", "vendor/README.md"]', 'roots = ["."]');
  edit(dot, 'cleanup.toml', 'source_roots = ["server", "client", "shared"]', 'source_roots = ["."]');
  edit(dot, 'README.md', '(docs/agent-contract.md)', '(docs/nope.md)');
  const res = run(['check', '--only', 'docs,comments'], dot);
  assert.ok(!outsideRoots(res), res.stdout);
  expectFinding(res, 'docs/refs', { message: 'docs/nope.md' });
});

test('[row 3, Grok 6, Astra 3] check and inventory need --catalog-command; a catalog file is never read', () => {
  const dir = fixture('ts-repo');
  fs.copyFileSync(`${dir}.catalog.json`, path.join(dir, 'catalog.json'));
  edit(dir, 'cleanup.toml', '[schema]\n', '[schema]\n');
  expectIncomplete(run(['check'], dir, { catalog: false }), 'no --catalog-command');
  expectIncomplete(run(['inventory'], dir, { catalog: false }), 'no --catalog-command');
  assert.equal(run(['check'], dir).status, 0);
});

test('[row 4, Muse N1, Astra H] a tagged template that builds an identifier is unresolved; a bound value stays resolved', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/store.ts', 'export const addUpdate = `INSERT INTO status_updates (incident_id, body, next_due_at) VALUES ($1, $2, $3)`;',
    'declare const sql: any;\nexport const addUpdate = (column: string) => sql`INSERT INTO status_updates (incident_id, body, ${sql.identifier(column)}) VALUES ($1, $2, $3)`;\nexport const seal = (id: number) => sql`UPDATE incidents SET sealed_at = now() WHERE id = ${id}`;');
  const res = inv(dir);
  assert.equal(row(res, 'status_updates', 'next_due_at').status, 'unresolved');
  assert.equal(row(res, 'incidents', 'sealed_at').status, 'referenced');
});

test('[row 4, Fable new 4, Neon 5] Python str.format and constant joins: dynamic is unresolved, static is referenced, the table name is kept', () => {
  const add = (dir, code) => edit(dir, 'app/db.py', 'def audit_column(conn, column):', `${code}\n\n\ndef audit_column(conn, column):`);
  for (const code of ['def pick(conn, column):\n    return run(conn, "SELECT {} FROM accounts".format(column))',
    'def pick(conn, column):\n    query = "SELECT {} FROM accounts".format(column)\n    return conn.fetch(query)']) {
    const d = fixture('py-repo'); add(d, code); assert.equal(row(inv(d), 'accounts', 'legacy_flag').status, 'unresolved', code);
  }
  const k = fixture('py-repo'); add(k, 'COLUMNS = "id, legacy_flag"\nFLAGGED = "SELECT " + COLUMNS + " FROM accounts"\n\n\ndef flagged(conn):\n    return run(conn, FLAGGED)');
  assert.equal(row(inv(k), 'accounts', 'legacy_flag').status, 'referenced');
  const p = fixture('py-repo'); add(p, 'def raw(conn, column):\n    return conn.execute(column.format("id")).fetchall()');
  assert.notEqual(row(inv(p), 'audit_events', 'action').status, 'unreferenced');
  const t = fixture('py-repo');
  edit(t, 'app/db.py', '    return conn.execute(sql.SQL("SELECT {} FROM audit_events").format(sql.Identifier(column))).fetchall()',
    '    tmpl = sql.SQL("SELECT {} FROM audit_events")\n    return conn.execute(tmpl.format(sql.Identifier(column))).fetchall()');
  const tres = inv(t);
  assert.equal(row(tres, 'accounts', 'legacy_flag').status, 'unreferenced', 'the table name is kept, so other tables are not poisoned');
  assert.equal(row(tres, 'audit_events', 'action').status, 'unresolved');
});

test('[row 4, Neon 6] chained + fragments are one site and never poison or reference unrelated tables', () => {
  const dyn = fixture('ts-repo');
  write(dyn, 'server/store.ts', 'export const q = (col: string) => "SELECT " + col + " FROM status_updates WHERE id = $1";\n');
  assert.equal(row(inv(dyn), 'incident_tags', 'tag').status, 'unreferenced');
  const stat = fixture('ts-repo');
  write(stat, 'server/store.ts', 'export const q = "SELECT " + "next_due_at " + "FROM incidents";\n');
  assert.equal(row(inv(stat), 'status_updates', 'next_due_at').status, 'unreferenced');
  const py = fixture('py-repo');
  edit(py, 'app/db.py', 'def audit_column(conn, column):', 'def concat(conn, column):\n    q = "SELECT " + column + " FROM audit_events"\n    return conn.execute(q)\n\n\ndef audit_column(conn, column):');
  assert.equal(row(inv(py), 'accounts', 'legacy_flag').status, 'unreferenced');
});

test('[row 5, Neon 7] a self-referential logger assignment does not crash the emitter', () => {
  const py = fixture('py-repo');
  write(py, 'app/rec.py', 'def f():\n    x = x.bind(a=1)\n    x.info("accounts listed")\n');
  expectClean(run(['check', '--only', 'logs'], py));
});

test('[row 6, Neon 3] /// directives, a directive on a later line, quoted ESLint rules and "pyright: strict, report…" are suppressions', () => {
  const ts = fixture('ts-repo');
  write(ts, 'shared/bypass.ts', '/// @ts-nocheck\n/// @ts-ignore\nexport const a = 1;\n/* note:\n * @ts-ignore\n */\nexport const b = 2;\n/* eslint "no-console": "off" */\nexport const c = 3;\n');
  assert.equal(run(['check', '--only', 'comments'], ts).records.filter((f) => f.path === 'shared/bypass.ts' && f.rule === 'comments/suppression').length, 4);
  const py = fixture('py-repo');
  write(py, 'app/bypass.py', '# pyright: strict, reportUnusedImport=false\nimport os\n');
  expectFinding(run(['check', '--only', 'comments'], py), 'comments/suppression', { path: 'app/bypass.py' });
});

test('[row 7, Muse N3] "can be used to", "will be used to" and "been used to" are present tense; a noun subject "used to" still blocks', () => {
  const dir = fixture('ts-repo');
  append(dir, 'AGENTS.md', '\nThe probe can be used to measure latency. The flag will be used to gate writes. It has been used to seed fixtures.\n');
  expectClean(run(['check', '--only', 'docs'], dir));
  append(dir, 'AGENTS.md', '\nThe worker service used to poll the queue.\n');
  expectFinding(run(['check', '--only', 'docs'], dir), 'docs/history');
});

test('[row 8, Muse N2] renaming a file while fixing one of its baselined rows is a move, not weakening', () => {
  const base = fixture('ts-repo'), cand = fixture('ts-repo');
  const rows = (d) => read(d, '.slop/baseline/slop-check.jsonl').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  const extra = { tool: 'slop-check', rule: 'comments/suppression', path: 'server/telemetry.ts', fingerprint: 'write :: @ts-expect-error -- legacy :: x', count: 1 };
  for (const d of [base, cand]) write(d, '.slop/baseline/slop-check.jsonl', [...rows(d), extra].map((r) => JSON.stringify(r) + '\n').join(''));
  fs.renameSync(path.join(cand, 'server/telemetry.ts'), path.join(cand, 'server/log.ts'));
  write(cand, '.slop/baseline/slop-check.jsonl', rows(cand).filter((r) => r !== extra && r.fingerprint !== extra.fingerprint).map((r) => JSON.stringify(r.path === 'server/telemetry.ts' ? { ...r, path: 'server/log.ts' } : r) + '\n').join(''));
  const res = run(['policy', '--base-policy-dir', base], cand, { noGit: true });
  assert.equal(res.status, 0, res.stdout);
  expectFinding(res, 'policy/moved', { path: 'server/log.ts' });
});

test('[row 9, Astra H, Neon 8] log.info(msg=..., extra=...) is checked; telemetry.info(...args) is a dynamic event', () => {
  const py = fixture('py-repo');
  write(py, 'app/kw.py', 'import logging\n\nlog = logging.getLogger(__name__)\n\n\ndef f(p):\n    log.info(msg="unexpected event", extra={"password": p})\n');
  const pres = run(['check', '--only', 'logs'], py);
  expectFinding(pres, 'logs/secret-key', { path: 'app/kw.py' });
  expectFinding(pres, 'logs/contract', { path: 'app/kw.py', message: '"unexpected event" is not in log-contract.json' });
  const ts = fixture('ts-repo');
  write(ts, 'server/spread_ev.ts', 'import { telemetry } from "./telemetry.ts";\nexport const f = (args: [string, Record<string, unknown>]) => telemetry.info(...args);\n');
  expectFinding(run(['check', '--only', 'logs'], ts), 'logs/dynamic-event', { path: 'server/spread_ev.ts' });
});

test('[row 10, Neon 10, Astra L] a later tracked TODO does not excuse an earlier untracked one', () => {
  const dir = fixture('ts-repo');
  append(dir, 'AGENTS.md', '\nTODO: fix this and TODO(#42): fix that.\n');
  write(dir, 'shared/two.ts', '// TODO one, TODO(#9) two\nexport const t = 1;\n');
  assert.equal(run(['check', '--only', 'docs'], dir).records.filter((f) => f.rule === 'docs/markers').length, 1);
  assert.equal(run(['check', '--only', 'comments'], dir).records.filter((f) => f.rule === 'comments/markers').length, 1);
});

test('[row 11, Fable new 7] footnotes are not undefined reference links', () => {
  const dir = fixture('ts-repo');
  append(dir, 'README.md', '\nRecall is scored at five.[^1][^2]\n\n[^1]: The first note.\n[^2]: The second note.\n');
  expectClean(run(['check', '--only', 'docs'], dir));
});

test('[row 12, Fable new 6, Neon 2] Poetry dependency tables are not policy; [[tool.mypy.overrides]] is', () => {
  const base = fixture('py-repo'), cand = fixture('py-repo');
  for (const d of [base, cand]) {
    edit(d, 'cleanup.toml', 'paths = ["cleanup.toml",', 'paths = ["pyproject.toml", "cleanup.toml",');
    write(d, 'pyproject.toml', '[tool.poetry.dependencies]\npsycopg = "3.3.8"\n\n[tool.mypy]\nstrict = true\n');
  }
  edit(cand, 'pyproject.toml', '"3.3.8"', '"3.3.9"');
  assert.equal(run(['policy', '--base-policy-dir', base], cand).status, 0, 'a Poetry dependency bump is not a policy edit');
  append(cand, 'pyproject.toml', '[[tool.mypy.overrides]]\nmodule = "app.*"\nignore_errors = true\n');
  expectFinding(run(['policy', '--base-policy-dir', base], cand), 'policy/weakening', { path: 'pyproject.toml' });
});
