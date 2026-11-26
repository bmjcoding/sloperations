// Check 4: the finding-fingerprinted baseline (in-runner), tool reports, and the policy comparison
// against a read-only protected snapshot (--base-policy-dir). Policy and fail-closed probes run with
// no .git directory and with no git on PATH. Test names carry the critique finding ids they encode.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, test } from 'node:test';
import { append, edit, expectClean, expectFinding, expectIncomplete, FIXTURES, fixture, read, run, write } from './helpers.mjs';

const REPORTS = path.join(FIXTURES, 'reports');
const report = (name, root) => fs.readFileSync(path.join(REPORTS, name), 'utf8').replaceAll('@ROOT@', fs.realpathSync(root));
const rows = (dir, tool = 'slop-check') => read(dir, `.slop/baseline/${tool}.jsonl`).split('\n').filter(Boolean).map((l) => JSON.parse(l));
const setRows = (dir, list, tool = 'slop-check') => write(dir, `.slop/baseline/${tool}.jsonl`, list.map((r) => JSON.stringify(r) + '\n').join(''));
const putReport = (dir, file, text, exit) => { write(dir, `.slop/reports/${file}`, text); write(dir, `.slop/reports/${file}.exit`, `${exit}\n`); };

// A Python fixture whose CI wrote real ruff, pyright, deptry and pydoclint reports, with exit statuses.
function pyWithReports() {
  const dir = fixture('py-repo');
  fs.copyFileSync(path.join(REPORTS, 'mod.py.txt'), path.join(dir, 'app/mod.py'));
  for (const f of ['ruff.json', 'pyright.json', 'deptry.json', 'pydoclint.txt']) putReport(dir, f, report(f, dir), 1);
  append(dir, 'cleanup.toml', '\n[baseline.reports]\nruff = ".slop/reports/ruff.json"\npyright = ".slop/reports/pyright.json"\ndeptry = ".slop/reports/deptry.json"\npydoclint = ".slop/reports/pydoclint.txt"\n');
  return dir;
}

describe('baseline comparator', () => {
  test('same-file swap: fixing one baselined finding while adding a distinct one fails', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'server/telemetry.ts', 'no-console -- the logger is the one module that writes stdout', 'no-console -- stdout is the log sink');
    const res = run(['check'], dir);
    expectFinding(res, 'comments/suppression', { status: 'new' });
    expectFinding(res, 'comments/suppression', { status: 'stale' });
  });

  test('a fixed finding leaves a stale row that fails until the baseline shrinks; --write keeps reasons', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'server/telemetry.ts', '  // eslint-disable-next-line no-console -- the logger is the one module that writes stdout\n', '');
    expectFinding(run(['check'], dir), 'comments/suppression', { message: 'shrink the baseline' });
    assert.equal(run(['baseline', '--write'], dir).status, 0);
    assert.deepEqual(rows(dir).map((r) => r.rule), ['docs/history']);
    expectClean(run(['check'], dir));
    assert.match(rows(dir)[0].reason, /participle/);
  });

  test('real ruff, pyright, deptry and pydoclint reports: new until baselined; DEP001 is never baselined [Neon 11]', () => {
    const dir = pyWithReports();
    const res = run(['check'], dir);
    for (const [tool, rule] of [['ruff', 'F401'], ['ruff', 'E722'], ['pyright', 'reportUnusedImport'], ['deptry', 'DEP002'], ['pydoclint', 'DOC103']]) {
      assert.ok(res.records.some((f) => f.tool === tool && f.rule === rule && f.status === 'new'), `${tool} ${rule}\n${res.stdout}`);
    }
    assert.ok(res.records.some((f) => f.tool === 'ruff' && f.path === 'app/mod.py' && f.line === 1), 'absolute paths are made repo-relative');
    assert.equal(run(['baseline', '--write'], dir).status, 0);
    assert.ok(!fs.readFileSync(path.join(dir, '.slop/baseline/deptry.jsonl'), 'utf8').includes('DEP001'), 'never-baselined rules are not written');
    assert.deepEqual(run(['check'], dir).records.map((f) => `${f.tool}:${f.rule}`), ['deptry:DEP001']);
    assert.ok(rows(dir, 'ruff').some((r) => r.fingerprint === ':: import os'), 'rows key on content and the enclosing symbol, never line numbers');
  });

  test('[Neon 6, Astra 5] a finding moved to another function in the same file is new; a line shift is not', () => {
    const dir = fixture('py-repo');
    write(dir, 'app/mod.py', 'def f():\n    try:\n        pass\n    except:\n        pass\n\n\ndef g():\n    return 1\n');
    const ruff = (line) => JSON.stringify([{ code: 'E722', filename: path.join(fs.realpathSync(dir), 'app/mod.py'), location: { row: line, column: 5 }, message: 'Do not use bare `except`' }]);
    putReport(dir, 'ruff.json', ruff(4), 1);
    append(dir, 'cleanup.toml', '\n[baseline.reports]\nruff = ".slop/reports/ruff.json"\n');
    run(['baseline', '--write'], dir);
    write(dir, 'app/mod.py', '\n\ndef f():\n    try:\n        pass\n    except:\n        pass\n\n\ndef g():\n    return 1\n');
    putReport(dir, 'ruff.json', ruff(6), 1);
    expectClean(run(['check'], dir));
    write(dir, 'app/mod.py', 'def f():\n    return 1\n\n\ndef g():\n    try:\n        pass\n    except:\n        pass\n');
    putReport(dir, 'ruff.json', ruff(8), 1);
    const res = run(['check'], dir);
    expectFinding(res, 'E722', { status: 'new' });
    expectFinding(res, 'E722', { status: 'stale' });
  });

  test('a stale pydoclint baseline entry is reported after its file is deleted; no policy file is rewritten', () => {
    const dir = pyWithReports();
    run(['baseline', '--write'], dir);
    const before = fs.readFileSync(path.join(dir, '.slop/baseline/pydoclint.jsonl'));
    fs.rmSync(path.join(dir, 'app/mod.py'));
    putReport(dir, 'pydoclint.txt', '', 0); // pydoclint no longer visits the deleted file
    for (const f of ['ruff.json', 'pyright.json']) putReport(dir, f, f === 'ruff.json' ? '[]' : '{"generalDiagnostics": []}', 0);
    const res = run(['check'], dir);
    assert.ok(res.records.some((f) => f.tool === 'pydoclint' && f.status === 'stale' && f.path === 'app/mod.py'), res.stdout);
    assert.ok(fs.readFileSync(path.join(dir, '.slop/baseline/pydoclint.jsonl')).equals(before), 'check never writes the baseline');
  });

  test('real oxlint and knip reports; knip unlisted dependencies are never baselined', () => {
    const dir = fixture('ts-repo');
    fs.copyFileSync(path.join(REPORTS, 'lintme.ts.txt'), path.join(dir, 'server/lintme.ts'));
    write(dir, 'server/orphan.ts', 'export const orphan = 1;\n');
    putReport(dir, 'oxlint.json', report('oxlint.json', dir), 1);
    putReport(dir, 'knip.json', report('knip.json', dir), 1);
    append(dir, 'cleanup.toml', '\n[baseline.reports]\noxlint = ".slop/reports/oxlint.json"\nknip = ".slop/reports/knip.json"\n');
    const res = run(['check'], dir);
    assert.ok(res.records.some((f) => f.tool === 'oxlint' && f.rule === 'eslint(no-unused-vars)' && f.path === 'server/lintme.ts'));
    for (const rule of ['files', 'dependencies', 'unlisted']) assert.ok(res.records.some((f) => f.tool === 'knip' && f.rule === rule), rule);
    run(['baseline', '--write'], dir);
    assert.deepEqual(run(['check'], dir).records.map((f) => `${f.tool}:${f.rule}`), ['knip:unlisted']);
  });

  test('[Astra 6, Neon 5, Fable 17] a failed, crashed, empty, truncated or unrecorded analyzer run is incomplete, never clean', () => {
    const cases = [
      ['no exit status recorded', (d) => fs.rmSync(path.join(d, '.slop/reports/ruff.json.exit')), 'exit status'],
      ['a config error (exit 2)', (d) => putReport(d, 'ruff.json', '', 2), 'did not complete'],
      ['a missing executable (exit 127)', (d) => putReport(d, 'pyright.json', '', 127), 'did not complete'],
      ['an empty JSON report', (d) => putReport(d, 'ruff.json', '', 1), 'unreadable'],
      ['a pydoclint traceback', (d) => putReport(d, 'pydoclint.txt', 'Traceback (most recent call last):\n  File "x", line 1\nRuntimeError: crash\n', 1), 'unrecognized line'],
      ['a truncated report that disagrees with its exit status', (d) => putReport(d, 'ruff.json', '[]', 1), 'regenerate'],
    ];
    for (const [name, mutate, message] of cases) { const dir = pyWithReports(); mutate(dir); expectIncomplete(run(['check'], dir), message); assert.ok(true, name); }
  });

  test('[Astra 19] a report written under a sub-project folder maps to repository-relative paths', () => {
    const dir = fixture('py-repo');
    edit(dir, 'cleanup.toml', 'source_roots = ["app"]', 'source_roots = ["app", "frontend/src"]');
    write(dir, 'frontend/src/app.ts', 'export function used() {\n  const unused = 1;\n  return 2;\n}\n');
    putReport(dir, 'oxlint.json', JSON.stringify({ diagnostics: [{ message: "Variable 'unused' is declared but never used.", code: 'eslint(no-unused-vars)', severity: 'error', filename: 'src/app.ts', labels: [{ span: { line: 2 } }] }] }), 1);
    append(dir, 'cleanup.toml', '\n[baseline.reports.oxlint]\nfile = ".slop/reports/oxlint.json"\ncwd = "frontend"\n');
    const res = run(['check'], dir);
    assert.ok(res.records.some((f) => f.tool === 'oxlint' && f.path === 'frontend/src/app.ts' && f.line === 2), res.stdout);
    edit(dir, 'frontend/src/app.ts', '  const unused = 1;\n', '');
    putReport(dir, 'oxlint.json', '{"diagnostics": []}', 0);
    expectClean(run(['check'], dir));
  });

  test('[Neon 5, Grok 9, Astra 1] malformed baseline rows are incomplete: null, zero or negative counts, duplicates, never-baselined rules', () => {
    for (const mutate of [(r) => ({ ...r, count: null }), (r) => ({ ...r, count: -1 }), (r) => ({ ...r, count: 0 }), (r) => ({ ...r, extra: 1 })]) {
      const dir = fixture('ts-repo'); setRows(dir, rows(dir).map(mutate)); expectIncomplete(run(['check'], dir), 'baseline row');
    }
    const dup = fixture('ts-repo'); setRows(dup, [...rows(dup), rows(dup)[0]]); expectIncomplete(run(['check'], dup), 'duplicate');
    const never = fixture('ts-repo'); setRows(never, [{ tool: 'deptry', rule: 'DEP001', path: 'a.py', fingerprint: 'x', count: 1 }], 'deptry'); expectIncomplete(run(['check'], never), 'cannot be baselined');
  });

  test('[Fable 8, Neon 11, Astra 12] baseline --write refuses --only and leaves every baseline file untouched', () => {
    const dir = fixture('ts-repo'), before = read(dir, '.slop/baseline/slop-check.jsonl');
    assert.equal(run(['baseline', '--write', '--only', 'docs'], dir).status, 2);
    assert.equal(read(dir, '.slop/baseline/slop-check.jsonl'), before);
  });
});

describe('policy comparison against a protected snapshot, with no .git', () => {
  const policy = (cand, base) => run(['policy', ...(base ? ['--base-policy-dir', base] : [])], cand, { noGit: true });
  const pair = () => ({ base: fixture('ts-repo'), cand: fixture('ts-repo') });
  const weak = (res, message) => { assert.equal(res.status, 1, res.stdout + res.stderr); expectFinding(res, 'policy/weakening', { message }); };

  test('an unchanged policy passes with no output', () => {
    const { base, cand } = pair(), res = policy(cand, base);
    assert.equal(res.status, 0, res.stdout + res.stderr);
    assert.equal(res.stdout, '');
  });

  test('adding an inline suppression to the baseline, or raising a count, is policy-weakening', () => {
    const { base, cand } = pair();
    edit(cand, 'server/app.ts', '  const ms', '  // @ts-expect-error -- the request type omits the proxy header\n  const ms');
    assert.equal(run(['baseline', '--write'], cand, { noGit: true }).status, 0);
    expectClean(run(['check'], cand, { noGit: true }));
    weak(policy(cand, base), 'adds baseline row slop-check comments/suppression');
    const two = pair();
    setRows(two.cand, rows(two.cand).map((r) => ({ ...r, count: r.count + 1 })));
    weak(policy(two.cand, two.base), 'raises baseline count 1 -> 2');
  });

  test('editing a lint config or cleanup.toml is policy-weakening', () => {
    const { base, cand } = pair();
    write(cand, '.oxlintrc.json', '{ "rules": { "no-console": "off" } }\n');
    edit(cand, 'cleanup.toml', 'required = ["docs", "comments", "logs", "schema"]', 'required = ["docs", "comments", "logs"]');
    const res = policy(cand, base);
    expectFinding(res, 'policy/weakening', { path: '.oxlintrc.json' });
    expectFinding(res, 'policy/weakening', { path: 'cleanup.toml' });
  });

  test('[Fable 6, Muse M3, Astra 4] an unlisted tool config the linters would discover is policy-weakening', () => {
    for (const [file, text] of [['server/.oxlintrc.json', '{ "rules": { "no-console": "off" } }'], ['tsconfig.json', '{}'], ['server/ruff.toml', '[lint]\nselect = []\n']]) {
      const { base, cand } = pair();
      write(cand, file, text);
      weak(policy(cand, base), 'not a declared policy path');
    }
  });

  test('[Fable 13] in a pyproject.toml policy path only [tool.*] tables count; an undeclared one with lint tables is weakening', () => {
    const base = fixture('py-repo'), cand = fixture('py-repo');
    write(cand, 'pyproject.toml', '[project]\nname = "accounts"\ndependencies = ["psycopg==3.3.9"]\n');
    assert.equal(policy(cand, base).status, 0);
    append(cand, 'pyproject.toml', '\n[tool.ruff.lint]\nselect = []\n');
    weak(policy(cand, base), 'not a declared policy path');
    for (const d of [base, cand]) { edit(d, 'cleanup.toml', 'paths = ["cleanup.toml",', 'paths = ["pyproject.toml", "cleanup.toml",'); write(d, 'pyproject.toml', '[project]\nname = "accounts"\ndependencies = ["psycopg==3.3.8"]\n\n[tool.deptry]\nknown_first_party = ["app"]\n'); }
    edit(cand, 'pyproject.toml', 'psycopg==3.3.8', 'psycopg==3.3.9');
    assert.equal(policy(cand, base).status, 0, 'a dependency bump is not a policy edit');
    edit(cand, 'pyproject.toml', 'known_first_party = ["app"]', 'known_first_party = ["app"]\nignore = ["DEP001"]');
    weak(policy(cand, base), 'edits a policy file');
  });

  test('removing a published event, a field, making a field optional, or [Astra 8] letting it arrive by spread is weakening', () => {
    const { base, cand } = pair();
    const c = JSON.parse(read(cand, 'log-contract.json'));
    delete c.events['migrations current'];
    c.events.request.fields = ['method', 'route', 'status'];
    c.events['migrations applied'] = { fields: [], optional: ['count'] };
    c.events['scribe answer'].spread = ['model'];
    write(cand, 'log-contract.json', JSON.stringify(c, null, 2));
    const res = policy(cand, base);
    weak(res, 'removes published event "migrations current"');
    for (const m of ['stops requiring field "ms"', 'stops requiring field "count"', 'lets field "model" arrive through a spread']) expectFinding(res, 'policy/weakening', { message: m });
  });

  test('a pure contract addition is a reviewed diff, not weakening', () => {
    const { base, cand } = pair(), c = JSON.parse(read(cand, 'log-contract.json'));
    c.events['cache warmed'] = { fields: ['entries'] };
    write(cand, 'log-contract.json', JSON.stringify(c, null, 2));
    const res = policy(cand, base);
    assert.equal(res.status, 0);
    expectFinding(res, 'policy/addition', { message: 'cache warmed' });
  });

  test('pure removals and reason-only edits pass without approval', () => {
    const { base, cand } = pair();
    setRows(cand, rows(cand).map((r) => ({ ...r, reason: 'Reworded.' })));
    assert.equal(policy(cand, base).status, 0);
    setRows(cand, []);
    assert.equal(policy(cand, base).status, 0);
  });

  test('[Astra 1, Neon 1, Muse M4] only an exact single-file rename moves rows; extra rows, higher counts and merges are weakening', () => {
    const rename = (cand) => { fs.renameSync(path.join(cand, 'server/telemetry.ts'), path.join(cand, 'server/log.ts')); setRows(cand, rows(cand).map((r) => (r.path === 'server/telemetry.ts' ? { ...r, path: 'server/log.ts' } : r))); };
    const clean = pair(); rename(clean.cand);
    const moved = policy(clean.cand, clean.base);
    assert.equal(moved.status, 0, moved.stdout);
    expectFinding(moved, 'policy/moved', { path: 'server/log.ts' });
    const extra = pair(); rename(extra.cand);
    setRows(extra.cand, [...rows(extra.cand), { tool: 'slop-check', rule: 'comments/suppression', path: 'server/log.ts', fingerprint: 'write :: @ts-ignore -- smuggled :: x', count: 1 }]);
    weak(policy(extra.cand, extra.base), 'adds baseline row');
    const more = pair(); rename(more.cand);
    setRows(more.cand, rows(more.cand).map((r) => (r.path === 'server/log.ts' ? { ...r, count: 2 } : r)));
    weak(policy(more.cand, more.base), 'adds baseline row');
    const copy = pair(); rename(copy.cand);
    fs.copyFileSync(path.join(copy.cand, 'server/log.ts'), path.join(copy.cand, 'server/telemetry.ts'));
    weak(policy(copy.cand, copy.base), 'adds baseline row');
    const merged = pair();
    const extraRow = { tool: 'slop-check', rule: 'comments/suppression', path: 'server/app.ts', fingerprint: 'handle :: @ts-ignore -- old :: x', count: 1 };
    setRows(merged.base, [...rows(merged.base), extraRow]);
    fs.rmSync(path.join(merged.cand, 'server/telemetry.ts')); fs.rmSync(path.join(merged.cand, 'server/app.ts'));
    write(merged.cand, 'server/merged.ts', 'export {};\n');
    setRows(merged.cand, [...rows(merged.cand).filter((r) => r.path !== 'server/telemetry.ts'), ...rows(merged.base).filter((r) => r.rule === 'comments/suppression').map((r) => ({ ...r, path: 'server/merged.ts' }))]);
    weak(policy(merged.cand, merged.base), 'adds baseline row');
  });

  test('[Astra 4] a protected snapshot missing a declared policy path is incomplete; any .jsonl outside the baseline is a plain file', () => {
    const { base, cand } = pair();
    fs.rmSync(path.join(base, '.oxlintrc.json'));
    expectIncomplete(policy(cand, base), 'lacks its declared policy path .oxlintrc.json');
    const j = pair();
    for (const d of [j.base, j.cand]) { edit(d, 'cleanup.toml', '".oxlintrc.json"]', '".oxlintrc.json", "allow.jsonl"]'); write(d, 'allow.jsonl', '{"x":1}\n'); }
    write(j.cand, 'allow.jsonl', '{"x":2}\n');
    weak(policy(j.cand, j.base), 'edits a policy file');
  });

  test('[Neon 3, Muse H2] a comment inside a multi-line policy array keeps every path', () => {
    const { base, cand } = pair();
    for (const d of [base, cand]) edit(d, 'cleanup.toml', 'paths = ["cleanup.toml", ".slop/baseline", "log-contract.json", ".oxlintrc.json"]', 'paths = [\n  "cleanup.toml",  # framing\n  ".slop/baseline",\n  "log-contract.json",\n  ".oxlintrc.json",\n]');
    write(cand, '.oxlintrc.json', '{ "rules": {} }\n');
    weak(policy(cand, base), 'edits a policy file');
  });

  test('fail closed: no snapshot, a snapshot with no cleanup.toml, or a missing declared root is incomplete', () => {
    const { base, cand } = pair();
    for (const res of [policy(cand, null), policy(cand, path.join(base, 'missing'))]) expectIncomplete(res, 'no protected policy snapshot');
    fs.rmSync(path.join(base, 'cleanup.toml'));
    assert.equal(policy(cand, base).status, 3);
    fs.rmSync(path.join(cand, 'test'), { recursive: true });
    expectIncomplete(run(['check'], cand, { noGit: true }), 'declared root missing: test');
    assert.ok(!fs.existsSync(path.join(cand, '.git')) && !fs.existsSync(path.join(base, '.git')));
  });
});
