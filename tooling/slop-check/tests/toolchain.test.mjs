// Opt-in (SLOP_CHECK_TOOLCHAINS=1, needs npm and uv with registry access): runs the real, pinned lint
// tools with the config templates on probes, proving each template rule family catches its fault and
// that node:test registrations are not flagged; and installs a fresh copy of the skill from its lockfile.
// Each expected finding is marked in the probe source with E:code on its line.
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { describe, test } from 'node:test';
import { catalogFile, fixture, REPO_ROOT, SCRIPTS, tempDir } from './helpers.mjs';

export const PINS = { oxlint: '1.95.0', 'oxlint-tsgolint': '7.0.2008', typescript: '7.0.2', '@types/node': '26.6.10', 'eslint-plugin-jsdoc': '65.5.5', eslint: '10.15.0',
  ruff: '0.17.6', pyright: '1.1.414', pydoclint: '0.12.0', deptry: '0.25.1' };
const TEMPLATES = path.join(REPO_ROOT, 'skills', 'slop-cleanup', 'templates');
const have = (bin) => spawnSync('sh', ['-c', `command -v ${bin}`]).status === 0;
const skip = process.env.SLOP_CHECK_TOOLCHAINS !== '1' ? 'set SLOP_CHECK_TOOLCHAINS=1 to run the pinned lint tools' : false;
const sh = (cmd, args, cwd) => spawnSync(cmd, args, { cwd, encoding: 'utf8', timeout: 600000, maxBuffer: 1 << 26 });
function project(files) {
  const dir = tempDir('tool');
  for (const [f, text] of Object.entries(files)) { fs.mkdirSync(path.dirname(path.join(dir, f)), { recursive: true }); fs.writeFileSync(path.join(dir, f), text); }
  return dir;
}
const expected = (dir, files) => files.flatMap((f) => fs.readFileSync(path.join(dir, f), 'utf8').split('\n').flatMap((l, i) => [
  ...[...l.matchAll(/E:([a-z-]+\([\w-]+\)|unused-directive)/g)].map((m) => m[1]),
  ...(l.trim() === '// @ts-ignore' ? ['typescript(ban-ts-comment)'] : []), // a marker here would count as its description
].map((code) => `${f}:${i + 1}:${code}`))).sort();

export const TS = {
  'package.json': '{"name":"probe","version":"0.0.0","type":"module","private":true}\n',
  'tsconfig.json': '{"compilerOptions":{"strict":true,"target":"es2022","module":"nodenext","jsx":"react-jsx","noEmit":true,"types":["node"]},"include":["server","client","test"]}\n',
  'server/a.ts': [
    'async function save(): Promise<void> {}',
    'declare function takesNumber(n: number): void;',
    'declare const db: { delete(t: unknown): { where(x: unknown): void }; update(t: unknown): { set(v: unknown): { where(x: unknown): void } } };',
    'export async function handler(raw: string, x: number): Promise<number> {',
    '  save(); /*E:typescript(no-floating-promises)*/',
    '  const parsed = JSON.parse(raw); /*E:typescript(no-unsafe-assignment)*/',
    '  console.log(parsed.value); /*E:typescript(no-unsafe-member-access)*//*E:eslint(no-console)*/',
    '  parsed.fn(); /*E:typescript(no-unsafe-call)*/',
    '  takesNumber(parsed); /*E:typescript(no-unsafe-argument)*/',
    '  if (save()) { /*E:typescript(no-misused-promises)*/',
    '  }',
    '  await 1; /*E:typescript(await-thenable)*/',
    '  const loose: any = 1; /*E:typescript(no-explicit-any)*/',
    '  void loose;',
    '  void `${{}}`; /*E:typescript(restrict-template-expressions)*//*E:typescript(no-base-to-string)*/',
    '  for (const i in [1, 2]) void i; /*E:typescript(no-for-in-array)*/',
    '  void (x + {}); /*E:typescript(restrict-plus-operands)*/',
    '  void (x === x); /*E:eslint(no-self-compare)*/',
    '  void (+x == null); /*E:eslint(no-constant-binary-expression)*/',
    '  void /\\x00/; /*E:eslint(no-control-regex)*/',
    '  x + 1; /*E:eslint(no-unused-expressions)*/',
    '  eval(raw); /*E:eslint(no-eval)*/',
    '  db.delete(raw); /*E:slop(delete-needs-where)*/',
    '  db.update(raw).set({ a: 1 }).where(1);',
    '  return JSON.parse(raw); /*E:typescript(no-unsafe-return)*/',
    '}',
    '// eslint-disable-next-line no-eval -- orphaned /*E:unused-directive*/',
    'export const plain = 1;',
    '// @ts-ignore',
    'export const n: number = "x" as unknown as number;',
    'type Kind = "a" | "b";',
    'export function kind(k: Kind): number {',
    '  switch (k) { /*E:typescript(switch-exhaustiveness-check)*/',
    '    case "a": return 1;',
    '  }',
    '  return 0;',
    '}',
    'class Box { value = 1; get(): number { return this.value; } }',
    'export const unbound = new Box().get; /*E:typescript(unbound-method)*/',
    '/**',
    ' * Adds.',
    ' * @param a first',
    ' * @param c second, expected E:jsdoc-js(check-param-names)',
    ' */',
    'export function add(a: number, b: number): number { return a + b; }',
    'import { b } from "./b.ts"; /*E:import(no-cycle)*/',
    'export const useB = b;',
    ''].join('\n'),
  'server/b.ts': 'import { plain } from "./a.ts"; /*E:import(no-cycle)*/\nexport const b = plain;\n',
  'client/ui.tsx': [
    'import { useEffect } from "react";',
    'import { plain } from "../server/a.ts"; /*E:eslint(no-restricted-imports)*/',
    'export function Ui({ on, html }: { on: boolean; html: string }) {',
    '  if (on) useEffect(() => {}); /*E:react-hooks(rules-of-hooks)*/',
    '  return (<div>',
    '    <a href="#">{plain}</a> {/*E:jsx-a11y(anchor-is-valid)*/}',
    '    <div onClick={() => {}} /> {/*E:jsx-a11y(click-events-have-key-events)*//*E:jsx-a11y(no-static-element-interactions)*/}',
    '    <p dangerouslySetInnerHTML={{ __html: html }} /> {/*E:react(no-danger)*/}',
    '    <a href="https://example.com" target="_blank">x</a> {/*E:react(jsx-no-target-blank)*/}',
    '  </div>);',
    '}',
    ''].join('\n'),
  'test/a.test.ts': [
    'import { describe, test } from "node:test";',
    'describe("suite", () => {',
    '  test("case", () => {});',
    '});',
    'async function helper(): Promise<void> {}',
    'helper(); /*E:typescript(no-floating-promises)*/',
    'await test.only("focused", () => {}); /*E:slop(no-focused-test)*/',
    'test("focused too", { only: true }, () => {}); /*E:slop(no-focused-test)*/',
    ''].join('\n'),
};

describe('ts-node template with oxlint --type-aware', { skip: skip || (!have('npm') && 'npm not found') }, () => {
  test('[Astra 18] every rule family catches its fault; node:test registrations are not flagged', () => {
    const dir = project(TS);
    for (const f of ['.oxlintrc.json', 'slop-rules.mjs']) fs.copyFileSync(path.join(TEMPLATES, 'ts-node', f), path.join(dir, f));
    const pkgs = ['oxlint', 'oxlint-tsgolint', 'typescript', '@types/node', 'eslint-plugin-jsdoc', 'eslint'].map((p) => `${p}@${PINS[p]}`);
    const inst = sh('npm', ['install', '--no-save', '--no-audit', '--no-fund', '--legacy-peer-deps', '@types/react@19', 'react@19', ...pkgs], dir);
    assert.equal(inst.status, 0, inst.stderr);
    const r = sh(path.join(dir, 'node_modules', '.bin', 'oxlint'), ['--type-aware', '--report-unused-disable-directives-severity=error', '-f', 'json', 'server', 'client', 'test'], dir);
    const got = JSON.parse(r.stdout).diagnostics.filter((d) => d.severity === 'error').map((d) => `${d.filename}:${d.labels?.[0]?.span?.line}:${d.code ?? 'unused-directive'}`).sort();
    const want = expected(dir, Object.keys(TS).filter((f) => /\.(?:ts|tsx)$/.test(f)));
    const missing = want.filter((w) => !got.includes(w)), extra = got.filter((g) => /^test\/a\.test\.ts:[234]:/.test(g));
    assert.deepEqual({ missing, extra }, { missing: [], extra: [] }, `oxlint reported:\n${got.join('\n')}`);
  });
});

describe('python-node template with ruff, pyright, pydoclint and deptry', { skip: skip || (!have('uvx') && 'uvx not found') }, () => {
  test('each selected rule catches its fault; module loggers and missing docstrings pass', () => {
    const dir = project({
      'pyproject.toml': '[project]\nname = "probe"\nversion = "0"\ndependencies = ["requests"]\n',
      'app/__init__.py': '',
      'app/log.py': 'import logging\n\nlog = logging.getLogger(__name__)\n',
      'app/jobs.py': [
        'import asyncio', 'import os', '', 'import requests', 'from app.log import log', '', '',
        'def run(job, unused):', //                                     8: ARG001
        '    try:', '        job()', '    except:', '        pass', //  11: E722, 12: S110
        '    print("done")', //                                         13: T201
        '    log.info(f"ran {job}")', //                                14: G004
        '    return 1  # type: ignore', //                              15: PGH003
        '', '',
        'async def spawn():', '    asyncio.create_task(asyncio.sleep(0))', //  19: RUF006
        '', '',
        'def total(a, b):',
        '    """Add two numbers.', '', '    Args:', '        a: the first.', '        c: the second.', '', '    Returns:', '        The sum.', '    """',
        '    return a + b', '', '',
        'def undocumented(a):', '    return requests.get(a)', '',
      ].join('\n'),
      'tests/test_jobs.py': 'import time\n\n\ndef test_wait():\n    time.sleep(1)\n',
    });
    for (const f of ['ruff.toml', 'pyrightconfig.json', 'tests/ruff.toml']) fs.copyFileSync(path.join(TEMPLATES, 'python-node', f), path.join(dir, f));
    fs.appendFileSync(path.join(dir, 'pyproject.toml'), `\n${fs.readFileSync(path.join(TEMPLATES, 'python-node', 'pyproject-tools.toml'), 'utf8')}`);
    const ruff = sh('uvx', [`ruff@${PINS.ruff}`, 'check', '--no-cache', '--output-format', 'json', '.'], dir);
    const codes = JSON.parse(ruff.stdout).map((d) => `${path.relative(fs.realpathSync(dir), d.filename)}:${d.location.row}:${d.code}`).sort();
    for (const want of ['app/jobs.py:2:F401', 'app/jobs.py:8:ARG001', 'app/jobs.py:11:E722', 'app/jobs.py:11:S110', 'app/jobs.py:13:T201', 'app/jobs.py:14:G004', 'app/jobs.py:15:PGH003', 'app/jobs.py:19:RUF006', 'tests/test_jobs.py:5:TID251']) {
      assert.ok(codes.includes(want), `${want} missing from:\n${codes.join('\n')}`);
    }
    assert.ok(!codes.some((c) => c.startsWith('app/log.py')), 'logging.getLogger(__name__) is allowed');
    const pyright = sh('uvx', [`pyright@${PINS.pyright}`, '--outputjson'], dir);
    assert.ok(JSON.parse(pyright.stdout).generalDiagnostics.some((d) => d.severity === 'error' && d.rule === 'reportUnusedImport'), pyright.stdout);
    const doc = sh('uvx', [`pydoclint@${PINS.pydoclint}`, 'app'], dir), docOut = doc.stdout + doc.stderr;
    assert.match(docOut, /2\d: DOC103/, docOut);
    assert.doesNotMatch(docOut, /undocumented/, 'no docstring presence rule');
    const dep = sh('uvx', [`deptry@${PINS.deptry}`, '.'], dir);
    assert.equal(dep.status, 0, `deptry reads [tool.deptry] and the project's dependencies:\n${dep.stdout}${dep.stderr}`);
  });
});

describe('a fresh copy of the skill', { skip: skip || (!have('npm') && 'npm not found') }, () => {
  test('[Muse M9] npm ci from the lockfile, then the checker runs', () => {
    const copy = tempDir('fresh');
    fs.cpSync(SCRIPTS, copy, { recursive: true, filter: (src) => !src.includes(`${path.sep}node_modules`) });
    const inst = sh('npm', ['ci', '--no-audit', '--no-fund'], copy); // npm 11 rejects `npm ci --prefix`; run it in the folder
    assert.equal(inst.status, 0, inst.stderr);
    const dir = fixture('ts-repo'), r = sh(path.join(copy, 'slop-check'), ['check', '--root', dir, '--catalog-command', `cat '${catalogFile(dir)}'`], copy);
    assert.equal(r.status, 0, r.stdout + r.stderr);
  });
});
