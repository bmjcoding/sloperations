// Fixture repos, CLI invocation and a throwaway PostgreSQL for the slop-check suite.
// Every test copies a fixture into a fresh temp directory outside any git checkout and drives the
// real launcher (or cli.mjs directly, with a PATH that holds no git) against it.
// SLOP_CHECK_SCRIPTS points the suite at another copy of skills/slop-cleanup/scripts.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(HERE, '..', '..', '..');
export const SCRIPTS = process.env.SLOP_CHECK_SCRIPTS || path.join(REPO_ROOT, 'skills', 'slop-cleanup', 'scripts');
export const LAUNCHER = path.join(SCRIPTS, 'slop-check');
export const CLI = path.join(SCRIPTS, 'slop_check', 'cli.mjs');
export const FIXTURES = path.join(HERE, 'fixtures');
export const PYTHON = process.env.SLOP_CHECK_PYTHON || 'python3';

const temps = [];
process.on('exit', () => { for (const d of temps) fs.rmSync(d, { recursive: true, force: true }); });
export function tempDir(tag) { const d = fs.mkdtempSync(path.join(os.tmpdir(), `slop-${tag}-`)); temps.push(d); return d; }

// A fixture's migrated catalog stands beside the copy, outside the tree, as a dump the reviewer made.
export const catalogFile = (dir) => `${dir}.catalog.json`;
export function fixture(name) {
  const dir = tempDir(name);
  fs.cpSync(path.join(FIXTURES, name), dir, { recursive: true });
  assert.ok(!fs.existsSync(path.join(dir, '.git')), 'fixtures never carry a .git directory');
  const cat = path.join(FIXTURES, 'catalogs', `${name}.json`);
  if (fs.existsSync(cat)) { fs.copyFileSync(cat, catalogFile(dir)); temps.push(catalogFile(dir)); }
  return dir;
}
export function edit(dir, file, from, to) {
  const p = path.join(dir, file), text = fs.readFileSync(p, 'utf8');
  assert.ok(text.includes(from), `${file} does not contain ${JSON.stringify(from)}`);
  fs.writeFileSync(p, text.replace(from, to));
}
export const write = (dir, file, text) => { fs.mkdirSync(path.dirname(path.join(dir, file)), { recursive: true }); fs.writeFileSync(path.join(dir, file), text); };
export const read = (dir, file) => fs.readFileSync(path.join(dir, file), 'utf8');
export const append = (dir, file, text) => fs.appendFileSync(path.join(dir, file), text);

// Runs slop-check. With noGit, runs cli.mjs under a PATH holding only node and python, so any git
// call would fail; the checker must not need one.
// check, baseline and inventory get --catalog-command "cat <the fixture's catalog>" unless catalog is false.
export function run(argv, dir, { noGit = false, env = {}, catalog = true } = {}) {
  const base = { ...process.env, SLOP_CHECK_PYTHON: PYTHON, ...env };
  const wants = catalog && ['check', 'baseline', 'inventory'].includes(argv[0]) && !argv.includes('--catalog-command') && fs.existsSync(catalogFile(dir));
  const args = wants ? [...argv, '--catalog-command', `cat '${catalogFile(dir)}'`] : argv;
  let r;
  if (noGit) {
    const bin = tempDir('bin'), py = spawnSync(PYTHON, ['-c', 'import sys; print(sys.executable)'], { encoding: 'utf8' }).stdout.trim();
    fs.symlinkSync(py, path.join(bin, 'python3'));
    fs.symlinkSync(spawnSync('sh', ['-c', 'command -v cat'], { encoding: 'utf8' }).stdout.trim(), path.join(bin, 'cat')); // for the test's catalog command; still no git
    r = spawnSync(process.execPath, [CLI, ...args, '--root', dir], { encoding: 'utf8', env: { ...base, PATH: bin, SLOP_CHECK_PYTHON: 'python3' } });
  } else {
    r = spawnSync(LAUNCHER, [...args, '--root', dir], { encoding: 'utf8', env: base });
  }
  const records = r.stdout.split('\n').filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return { rule: 'unparsed-output', message: l }; } });
  return { status: r.status, stdout: r.stdout, stderr: r.stderr, records };
}
export const rules = (res) => res.records.map((f) => f.rule);
export function hasFinding(res, rule, { path: p, message, status } = {}) {
  return res.records.some((f) => f.rule === rule && (!p || f.path === p) && (!message || f.message.includes(message)) && (!status || f.status === status));
}
export function expectFinding(res, rule, opts = {}) {
  assert.ok(hasFinding(res, rule, opts), `expected ${rule} ${JSON.stringify(opts)}; got exit ${res.status}:\n${res.stdout}${res.stderr}`);
}
// A run with no blocking finding: a full run exits 0; a --only run exits 3 with only its partial-run record.
export function expectClean(res) {
  const blocking = res.records.filter((f) => f.severity === 'error' && f.rule !== 'partial-run');
  assert.deepEqual(blocking, [], `expected no blocking findings; got exit ${res.status}:\n${res.stdout}${res.stderr}`);
  assert.ok(res.status === 0 || (res.status === 3 && hasFinding(res, 'partial-run')), `exit ${res.status}:\n${res.stdout}${res.stderr}`);
}
export function expectIncomplete(res, message) {
  assert.equal(res.status, 3, `expected incomplete; got exit ${res.status}:\n${res.stdout}${res.stderr}`);
  expectFinding(res, 'incomplete', message ? { message } : {});
}

// The catalog query this tree would send, and a catalog rewritten with this tree's migrations digest.
export function catalogQuery(dir) {
  const r = spawnSync(LAUNCHER, ['catalog-query', '--root', dir], { encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout;
}
export function rewriteCatalog(dir, mutate) {
  const cat = JSON.parse(fs.readFileSync(catalogFile(dir), 'utf8'));
  cat.migrations_digest = catalogQuery(dir).match(/'migrations_digest', '([0-9a-f]+)'/)[1];
  mutate?.(cat);
  fs.writeFileSync(catalogFile(dir), JSON.stringify(cat));
}

// A throwaway PostgreSQL: a container when a runtime is available, else local initdb/pg_ctl/psql,
// else { skip: reason }. psql(db, sql) runs SQL; dsn(db) is a URL for drivers; command(db) is a shell
// command that reads SQL on stdin, for --catalog-command.
export function startPostgres() {
  const image = process.env.SLOP_CHECK_PG_IMAGE || 'postgres:18';
  const sh = (cmd, args, opts = {}) => spawnSync(cmd, args, { encoding: 'utf8', timeout: 120000, ...opts });
  const flags = ['-X', '-At', '-q', '-v', 'ON_ERROR_STOP=1'];
  if (sh('docker', ['info'], { timeout: 20000 }).status === 0) {
    const started = sh('docker', ['run', '-d', '--rm', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-p', '127.0.0.1::5432', image]);
    if (started.status !== 0) return { skip: `docker could not start ${image}: ${started.stderr.trim()}` };
    const id = started.stdout.trim(), psqlArgs = (db) => ['exec', '-i', id, 'psql', ...flags, '-U', 'postgres', '-d', db];
    const psql = (db, sql) => sh('docker', psqlArgs(db), { input: sql });
    for (let i = 0; i < 60 && psql('postgres', 'select 1').status !== 0; i++) spawnSync('sleep', ['1']);
    spawnSync('sleep', ['1']); // the image restarts once after initdb
    for (let i = 0; i < 30 && psql('postgres', 'select 1').status !== 0; i++) spawnSync('sleep', ['1']);
    const port = sh('docker', ['port', id, '5432/tcp']).stdout.trim().split('\n')[0].split(':').pop();
    return { kind: `container ${image}`, psql, command: (db) => `docker ${psqlArgs(db).join(' ')}`,
      dsn: (db) => `postgresql+psycopg://postgres@127.0.0.1:${port}/${db}`, stop: () => sh('docker', ['rm', '-f', id]) };
  }
  const have = (b) => sh('sh', ['-c', `command -v ${b}`]).status === 0;
  if (have('initdb') && have('pg_ctl') && have('psql')) {
    const data = tempDir('pg'), sock = fs.mkdtempSync('/tmp/slop-sock-'); temps.push(sock);
    if (sh('initdb', ['-D', data, '-U', 'postgres', '-A', 'trust', '-E', 'UTF8', '--locale=C']).status !== 0) return { skip: 'local initdb failed' };
    if (sh('pg_ctl', ['-D', data, '-w', '-o', `-k ${sock} -c listen_addresses='' -p 54329`, 'start'], { stdio: 'ignore' }).status !== 0) return { skip: 'local pg_ctl start failed' };
    const psqlArgs = (db) => [...flags, '-h', sock, '-p', '54329', '-U', 'postgres', '-d', db];
    return { kind: 'local postgres', psql: (db, sql) => sh('psql', psqlArgs(db), { input: sql }), command: (db) => `psql ${psqlArgs(db).join(' ')}`,
      dsn: (db) => `postgresql+psycopg://postgres@/${db}?host=${sock}&port=54329`, stop: () => sh('pg_ctl', ['-D', data, '-m', 'fast', 'stop']) };
  }
  return { skip: 'no container runtime and no local initdb/pg_ctl/psql; PostgreSQL fixtures skipped' };
}
let dbSeq = 0;
export function freshDb(pg) {
  const name = `fixture_${process.pid}_${++dbSeq}`, r = pg.psql('postgres', `create database ${name};`);
  assert.equal(r.status, 0, r.stderr);
  return name;
}
