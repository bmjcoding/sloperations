// Check 2, logs: dynamic event names, secret keys, and reconciliation with log-contract.json.
// Test names carry the critique finding ids they encode (reviewer and number).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { edit, expectClean, expectFinding, expectIncomplete, fixture, read, run, write } from './helpers.mjs';

const logs = (dir, extra = []) => run(['check', '--only', 'logs', ...extra], dir);
const only = (res, file) => res.records.filter((f) => f.path === file).map((f) => f.rule).sort();

test('clean fixtures pass: literal ternary event, ...usage spread, test-root synthetic event', () => {
  for (const name of ['ts-repo', 'py-repo']) expectClean(logs(fixture(name)));
});

test('logs/dynamic-event: an interpolated event name fails (TypeScript and Python)', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/app.ts', 'telemetry.info("request",', 'telemetry.info(`request ${routeName}`,');
  expectFinding(logs(dir), 'logs/dynamic-event', { path: 'server/app.ts' });
  const py = fixture('py-repo');
  edit(py, 'app/db.py', 'log.info("accounts listed",', 'log.info(f"accounts listed {conn}",');
  expectFinding(logs(py), 'logs/dynamic-event', { path: 'app/db.py' });
});

test('logs/secret-key: a literal password field fails; [Muse M8] apiKey, api_key and privateKey too; inputTokens does not', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/app.ts', 'ms });', 'ms, password: "x", apiKey: "k", api_key: "k", privateKey: "p", inputTokens: 3 });');
  const keys = logs(dir).records.filter((f) => f.rule === 'logs/secret-key').map((f) => f.message.match(/"(\w+)"/)[1]).sort();
  assert.deepEqual(keys, ['apiKey', 'api_key', 'password', 'privateKey']);
});

test('[Astra 9, Fable 3, Neon 4] a variable event still gets the secret-key check, and fails as unbounded', () => {
  const dir = fixture('ts-repo');
  write(dir, 'server/dyn.ts', 'import { telemetry } from "./telemetry.ts";\nexport const f = (ev: string, password: string, token: string) => telemetry.info(ev, { password, token });\n');
  assert.deepEqual(only(logs(dir), 'server/dyn.ts'), ['logs/dynamic-event', 'logs/secret-key', 'logs/secret-key']);
  const py = fixture('py-repo');
  write(py, 'app/reset.py', 'import logging\n\nlog = logging.getLogger(__name__)\n\n\ndef reset(event, password):\n    log.info(event, extra={"password": password})\n');
  assert.deepEqual(only(logs(py), 'app/reset.py'), ['logs/dynamic-event', 'logs/secret-key']);
});

test('[Astra 9, Fable 3] events resolve through the binding in scope: const, as const, imports and literal-union parameters', () => {
  const dir = fixture('ts-repo');
  write(dir, 'server/events.ts', 'export const Events = { Request: "request" } as const;\nexport const SCRIBE = "scribe answer";\n');
  write(dir, 'server/emit.ts', [
    'import { telemetry } from "./telemetry.ts";',
    'import { Events, SCRIBE } from "./events.ts";',
    'const request = "migrations applied";',
    'export const a = (method: string) => telemetry.info(Events.Request, { method, route: "/", status: 200, ms: 1 });',
    'export const b = (model: string) => telemetry.info(SCRIBE, { model });',
    'export const c = (kind: "migrations applied" | "migrations current") => telemetry.info(kind, { count: 1 });',
    'export function d(request: string) { telemetry.info(request, { count: 1 }); }', // the parameter shadows the module constant
    ''].join('\n'));
  const res = logs(dir);
  assert.deepEqual(res.records.filter((f) => f.path === 'server/emit.ts').map((f) => `${f.rule}:${f.line}`), ['logs/dynamic-event:7'], res.stdout);
  edit(dir, 'server/events.ts', '{ Request: "request" }', '{ Request: "http request" }');
  expectFinding(logs(dir), 'logs/contract', { path: 'server/emit.ts', message: '"http request" is not in log-contract.json' });
});

test('[Neon 13] Python resolves module constants and constants imported from a scanned module', () => {
  const py = fixture('py-repo');
  write(py, 'app/events.py', 'LISTED = "accounts listed"\n');
  write(py, 'app/more.py', 'import logging\n\nfrom app.events import LISTED\n\nlog = logging.getLogger(__name__)\nAUDIT = "audit read"\n\n\ndef f(n, c):\n    log.info(LISTED, extra={"count": n})\n    log.info(AUDIT, extra={"column": c})\n');
  expectClean(logs(py));
  edit(py, 'app/events.py', '"accounts listed"', '"accounts shown"'); // the imported constant is reconciled, not skipped
  expectFinding(logs(py), 'logs/contract', { path: 'app/more.py', message: '"accounts shown" is not in log-contract.json' });
});

test('code-only event rename fails reconciliation with log-contract.json left untouched', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/app.ts', 'telemetry.info("request",', 'telemetry.info("http request",');
  const res = logs(dir);
  expectFinding(res, 'logs/contract', { path: 'server/app.ts', message: '"http request" is not in log-contract.json' });
  expectFinding(res, 'logs/contract', { path: 'log-contract.json', message: '"request" has no producer' });
});

test('producer-only field removal fails with log-contract.json left untouched', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/app.ts', ' status: response.statusCode,', '');
  expectFinding(logs(dir), 'logs/contract', { path: 'server/app.ts', message: 'missing required field "status"' });
  const py = fixture('py-repo');
  edit(py, 'app/db.py', 'extra={"count": cur.rowcount}', 'extra={}');
  expectFinding(logs(py), 'logs/contract', { path: 'app/db.py', message: 'missing required field "count"' });
});

test('[Grok 2] fields bound to a const object are reconciled like a literal', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/app.ts', '  telemetry.info("request", { method: request.method, route: routeName, status: response.statusCode, ms });',
    '  const fields = { method: request.method, route: routeName };\n  telemetry.info("request", fields);');
  const res = logs(dir);
  expectFinding(res, 'logs/contract', { message: 'missing required field "status"' });
  expectFinding(res, 'logs/contract', { message: 'missing required field "ms"' });
});

test('[Astra 8] a spread no longer covers a required field written literally; the contract names what a spread may carry', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/scribe.ts', '{ model, ...usage }', '{ ...usage }');
  expectFinding(logs(dir), 'logs/contract', { path: 'server/scribe.ts', message: 'missing required field "model"' });
  const ok = fixture('ts-repo');
  const c = JSON.parse(read(ok, 'log-contract.json'));
  c.events['scribe answer'] = { fields: ['model', 'inputTokens'], spread: ['inputTokens'], optional: ['outputTokens'] };
  write(ok, 'log-contract.json', JSON.stringify(c));
  expectClean(logs(ok));
});

test('[Neon 4] Python positional format arguments do not hide a missing required field', () => {
  const py = fixture('py-repo');
  edit(py, 'app/db.py', 'log.info("accounts listed", extra={"count": cur.rowcount})', 'log.info("accounts listed", cur, extra={})');
  expectFinding(logs(py), 'logs/contract', { message: 'missing required field "count"' });
});

test('[Muse M6] child and bound loggers are checked (TypeScript and Python)', () => {
  const dir = fixture('ts-repo');
  write(dir, 'server/child.ts', 'import { telemetry } from "./telemetry.ts";\nconst child = telemetry.child({});\nexport const f = (name: string) => { telemetry.child({}).info(`ran ${name}`, { password: "x" }); child.info(`ran ${name}`, {}); };\n');
  assert.deepEqual(only(logs(dir), 'server/child.ts'), ['logs/dynamic-event', 'logs/dynamic-event', 'logs/secret-key']);
  const py = fixture('py-repo');
  write(py, 'app/bound.py', 'import logging\n\nlog = logging.getLogger(__name__)\n\n\ndef f(name):\n    log.bind(job=1).info(f"ran {name}")\n');
  expectFinding(logs(py), 'logs/dynamic-event', { path: 'app/bound.py' });
});

test('a field the contract does not list fails', () => {
  const dir = fixture('ts-repo');
  edit(dir, 'server/app.ts', 'ms });', 'ms, userAgent: "x" });');
  expectFinding(logs(dir), 'logs/contract', { message: 'logs field "userAgent"' });
});

test('[Neon 5] a missing or malformed log contract is incomplete, never a pass', () => {
  const dir = fixture('py-repo');
  fs.rmSync(path.join(dir, 'log-contract.json'));
  expectIncomplete(logs(dir), 'log contract');
  write(dir, 'log-contract.json', '{ "events": ');
  expectIncomplete(logs(dir), 'not valid JSON');
});

test('a TypeScript file that does not parse is incomplete, never skipped', () => {
  const dir = fixture('ts-repo');
  write(dir, 'server/broken.ts', 'export function (\n');
  expectIncomplete(logs(dir), 'server/broken.ts');
});
