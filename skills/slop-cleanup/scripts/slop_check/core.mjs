// slop-check core: the slop-cleanup skill's deterministic checks (Node 24+; parsers pinned in ../package.json).
// Reads one frozen tree and never runs git. Symbolic links are never followed. Exit codes: 0 pass,
// 1 blocking findings, 2 usage or config error, 3 incomplete (a root, input, report or comparison is
// missing or unusable, a partial run, or an internal error).
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { fromMarkdown } from 'mdast-util-from-markdown';
import { gfmTableFromMarkdown } from 'mdast-util-gfm-table';
import { gfmTable } from 'micromark-extension-gfm-table';
import { parseSync } from 'oxc-parser';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export class Incomplete extends Error {}
class Usage extends Error {}
const norm = (s) => String(s ?? '').replace(/\s+/g, ' ').trim();
const clip = (s) => norm(s).slice(0, 200);
const sortBy = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const JS_EXT = /\.(?:[cm]?[jt]sx?)$/;
const CODE_EXT = /\.(?:[cm]?[jt]sx?|py)$/;
const CHECKS = ['docs', 'comments', 'logs', 'schema'];
const INDEX_SKIP = new Set(['.git', 'node_modules', '.venv', 'venv']);
const SCAN_SKIP = new Set(['__pycache__', '.slop']);
const under = (f, roots) => roots.some((r) => { const d = path.posix.normalize(r).replace(/\/+$/, ''); return d === '.' || f === d || f.startsWith(`${d}/`); });
const group = (items, key) => { const m = new Map(); for (const x of items) (m.get(key(x)) ?? m.set(key(x), []).get(key(x))).push(x); return m; };
const blankTrackers = (s) => s.replace(/\b(TODO|FIXME)\([^)]*\)/g, (m, w) => w + ' '.repeat(m.length - w.length)); // tracker IDs are not history

// ---------- config: the TOML subset cleanup.toml uses (tables, strings, numbers, booleans, arrays) ----------
function tomlValue(v) {
  let out = '', depth = 0;
  for (let i = 0; i < v.length; i++) {
    const c = v[i];
    if (c === "'") { const j = v.indexOf("'", i + 1); if (j < 0) return null; out += JSON.stringify(v.slice(i + 1, j)); i = j; }
    else if (c === '"') { let j = i + 1; while (j < v.length && v[j] !== '"') j += v[j] === '\\' ? 2 : 1; if (j >= v.length) return null; out += v.slice(i, j + 1); i = j; }
    else if (c === '#') { while (i + 1 < v.length && v[i + 1] !== '\n') i++; } // a comment runs to its line end
    else { if (c === '[') depth++; if (c === ']') depth--; out += c; }
  }
  return depth > 0 ? null : out.replace(/,\s*\]/g, ']').trim();
}
export function parseToml(text, file = 'cleanup.toml') {
  const root = {}; let cur = root; const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i].trim(), at = i + 1;
    if (!l || l.startsWith('#')) continue;
    let m = l.match(/^\[([\w.-]+)\]\s*(?:#.*)?$/);
    if (m) { cur = root; for (const k of m[1].split('.')) cur = cur[k] ??= {}; continue; }
    m = l.match(/^([\w-]+)\s*=\s*(.*)$/);
    if (!m) throw new Usage(`${file}:${at}: cannot parse "${l}"`);
    let raw = m[2], json = tomlValue(raw);
    while (json === null && i + 1 < lines.length) { raw += '\n' + lines[++i]; json = tomlValue(raw); }
    if (json === null || json === '') throw new Usage(`${file}:${at}: unterminated or empty value for ${m[1]}`);
    try { cur[m[1]] = JSON.parse(json); } catch { throw new Usage(`${file}:${at}: bad value for ${m[1]}`); }
  }
  return root;
}
const DEFAULTS = {
  repo: { stacks: [], source_roots: [], test_roots: [], exclude: [], vendor_payloads: [], build_outputs: [], escalation_owner: '', merge_authority: '', branch_authority: '' },
  checks: { required: CHECKS },
  docs: { roots: [], history_exclude: ['**/adr/**', '**/CHANGELOG.md', 'docs/debt-log.md'], external_prefixes: [] },
  comments: { tracker: '#\\d+|[A-Z][A-Z0-9]+-\\d+|https?://', markers: 'warning' },
  logs: { loggers: ['log', 'logger'], levels: ['trace', 'debug', 'info', 'warn', 'warning', 'error', 'exception', 'critical', 'fatal'],
    contract: 'log-contract.json', event_arg: 0, fields_arg: 1, secret_words: ['password', 'passwd', 'token', 'authorization', 'cookie', 'secret', 'apikey', 'privatekey'] },
  schema: { orm: 'none', production_dialect: 'postgresql', schemas: ['public'], schema_files: [], migrations: '',
    expected_heads: 1, require_physical_primary_key: false, casing: 'none', target_metadata: '', model_packages: [] },
  baseline: { dir: '.slop/baseline', reports: {} },
  policy: { paths: [] },
};
const CHOICES = { 'schema.orm': ['drizzle', 'alembic', 'none'], 'comments.markers': ['warning', 'error'], 'schema.casing': ['none', 'snake_case'] };
export function loadConfig(root) {
  const file = path.join(root, 'cleanup.toml');
  if (!fs.existsSync(file)) throw new Incomplete(`no cleanup.toml in ${root}: frame the run first`);
  const raw = parseToml(fs.readFileSync(file, 'utf8')), cfg = {};
  for (const sec of Object.keys(raw)) if (!(sec in DEFAULTS)) throw new Usage(`cleanup.toml: unknown section [${sec}]`);
  for (const [sec, defs] of Object.entries(DEFAULTS)) {
    const got = raw[sec] ?? {}; cfg[sec] = {};
    for (const k of Object.keys(got)) if (!(k in defs)) throw new Usage(`cleanup.toml: unknown key ${sec}.${k}`);
    for (const [k, d] of Object.entries(defs)) {
      const v = got[k] ?? structuredClone(d);
      const ok = k === 'expected_heads' ? Number.isInteger(v) || (Array.isArray(v) && v.every((x) => typeof x === 'string'))
        : k === 'reports' ? v && typeof v === 'object' && !Array.isArray(v)
          : Array.isArray(d) ? Array.isArray(v) && v.every((x) => typeof x === 'string') : typeof v === typeof d;
      if (!ok) throw new Usage(`cleanup.toml: ${sec}.${k} has the wrong type`);
      if (CHOICES[`${sec}.${k}`] && !CHOICES[`${sec}.${k}`].includes(v)) throw new Usage(`cleanup.toml: ${sec}.${k} must be one of ${CHOICES[`${sec}.${k}`].join(', ')}`);
      cfg[sec][k] = v;
    }
  }
  for (const c of cfg.checks.required) if (!CHECKS.includes(c)) throw new Usage(`cleanup.toml: unknown check "${c}" in [checks] required`);
  for (const [tool, v] of Object.entries(cfg.baseline.reports)) {
    const spec = typeof v === 'string' ? { file: v } : v;
    if (!CONVERT[tool]) throw new Usage(`cleanup.toml: no reader for report "${tool}" (known: ${Object.keys(CONVERT).join(', ')})`);
    if (typeof spec.file !== 'string' || Object.keys(spec).some((k) => !['file', 'cwd'].includes(k))) throw new Usage(`cleanup.toml: [baseline.reports.${tool}] takes file and cwd`);
    cfg.baseline.reports[tool] = { file: spec.file, cwd: spec.cwd ?? '.' };
  }
  if (!cfg.policy.paths.length) cfg.policy.paths = ['cleanup.toml', cfg.baseline.dir, cfg.logs.contract];
  return cfg;
}

// ---------- the frozen tree: a case-exact index; symbolic links are absent, never followed ----------
export function indexTree(root) {
  const files = [], dirs = new Set();
  const visit = (rel) => {
    for (const name of fs.readdirSync(path.join(root, rel)).sort(sortBy)) {
      if (INDEX_SKIP.has(name)) continue;
      const r = rel ? `${rel}/${name}` : name, st = fs.lstatSync(path.join(root, r));
      if (st.isDirectory()) { dirs.add(r); visit(r); } else if (st.isFile()) files.push(r);
    }
  };
  visit('');
  const set = new Set(files);
  const exists = (p) => { const n = path.posix.normalize(p).replace(/\/+$/, ''); return !n.startsWith('..') && !n.startsWith('/') && (n === '.' || set.has(n) || dirs.has(n)); };
  return { files, dirs, exists };
}
export function globRe(glob) {
  const g = glob.replace(/\/+$/, ''); let re = '';
  for (let i = 0; i < g.length; i++) {
    const c = g[i];
    if (c === '*' && g[i + 1] === '*') { re += g[i + 2] === '/' ? '(?:.*/)?' : '.*'; i += g[i + 2] === '/' ? 2 : 1; }
    else if (c === '*') re += '[^/]*';
    else if (c === '?') re += '[^/]';
    else re += c.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${re}(?:/.*)?$`);
}
const matcher = (globs) => { const res = globs.map(globRe); return (p) => res.some((r) => r.test(p)); };
function makeTree(root, cfg, checks) {
  const idx = indexTree(root), skip = matcher([...cfg.repo.exclude, ...cfg.repo.vendor_payloads, ...cfg.repo.build_outputs]);
  const codeRoots = [...cfg.repo.source_roots, ...cfg.repo.test_roots], roots = [...codeRoots, ...(checks.includes('docs') ? cfg.docs.roots : [])];
  const missing = roots.filter((r) => !idx.exists(r));
  if (missing.length) throw new Incomplete(`declared root missing: ${missing.join(', ')}`);
  const scannable = (f) => !skip(f) && !f.split('/').some((s) => SCAN_SKIP.has(s));
  const migrations = cfg.schema.migrations ? [cfg.schema.migrations] : [];
  if (checks.includes('comments') || checks.includes('logs')) {
    const loose = idx.files.filter((f) => CODE_EXT.test(f) && scannable(f) && !under(f, [...codeRoots, ...cfg.schema.schema_files, ...migrations]));
    if (loose.length) throw new Incomplete(`code outside every declared root: ${loose.slice(0, 5).join(', ')}${loose.length > 5 ? ` and ${loose.length - 5} more` : ''}; add a root or an exclude`);
  }
  const pick = (re, rs) => idx.files.filter((f) => re.test(f) && scannable(f) && under(f, rs));
  return { root, skip, exists: idx.exists, files: idx.files, read: (f) => fs.readFileSync(path.join(root, f), 'utf8'),
    sources: pick(CODE_EXT, codeRoots), docs: pick(/\.mdx?$/i, cfg.docs.roots),
    sqlFiles: pick(/\.sql$/i, cfg.repo.source_roots).filter((f) => !under(f, migrations)),
    endsWith: (p) => idx.files.some((f) => f.endsWith(`/${p}`)) || [...idx.dirs].some((d) => d.endsWith(`/${p.replace(/\/+$/, '')}`)) };
}

// ---------- JS/TS through oxc-parser (offsets are UTF-16), with function scopes and cross-file constants ----------
export function parseJs(file, src) {
  const r = parseSync(file, src);
  if (r.errors.length) throw new Incomplete(`${file}: cannot parse: ${clip(r.errors[0].message)}`);
  const nl = [0]; for (let i = 0; i < src.length; i++) if (src[i] === '\n') nl.push(i + 1);
  const lineAt = (off) => { let lo = 0, hi = nl.length - 1; while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (nl[mid] <= off) lo = mid; else hi = mid - 1; } return lo + 1; };
  return { program: r.program, lineAt, offsetOf: (line) => nl[line - 1] ?? src.length,
    comments: r.comments.map((c) => ({ line: lineAt(c.start), endLine: lineAt(c.end), text: c.value, own: !src.slice(nl[lineAt(c.start) - 1], c.start).trim() })) };
}
export function walkAst(node, visit) {
  if (Array.isArray(node)) { for (const n of node) walkAst(n, visit); return; }
  if (!node || typeof node !== 'object') return;
  if (typeof node.type === 'string' && visit(node) === false) return;
  for (const k in node) if (k !== 'parent' && node[k] && typeof node[k] === 'object') walkAst(node[k], visit);
}
export const unwrap = (n) => { while (n && /^(?:TSAsExpression|TSSatisfiesExpression|TSNonNullExpression|ParenthesizedExpression|ChainExpression)$/.test(n.type)) n = n.expression; return n; };
export const strOf = (n) => (n?.type === 'Literal' && typeof n.value === 'string' ? n.value : n?.type === 'TemplateLiteral' && !n.expressions.length ? n.quasis[0].value.cooked : null);
export const keyName = (p) => (p.computed ? strOf(p.key) : p.key.type === 'Identifier' ? p.key.name : p.key.value != null ? String(p.key.value) : null);
const FN = /^(?:FunctionDeclaration|FunctionExpression|ArrowFunctionExpression)$/;
// Names one function (or the module) binds: const values, parameters, imports, and everything else.
function scopeOf(node, parent) {
  const s = { values: new Map(), params: new Map(), imports: new Map(), other: new Set(), parent };
  const bind = (id, value) => {
    if (id.type !== 'Identifier') { walkAst(id, (n) => { if (n.type === 'Identifier') s.other.add(n.name); }); return; }
    if (value && !s.values.has(id.name) && !s.other.has(id.name)) s.values.set(id.name, value); else { s.values.delete(id.name); s.other.add(id.name); }
  };
  for (const p of node.params ?? []) { const id = p.type === 'AssignmentPattern' ? p.left : p; if (id.type === 'Identifier') s.params.set(id.name, id); else bind(id); }
  walkAst(node.body, (n) => {
    if (FN.test(n.type)) { if (n.id) bind(n.id); return false; }
    if (n.type === 'VariableDeclaration') for (const d of n.declarations) bind(d.id, n.kind === 'const' ? d.init : null);
    if (n.type === 'ClassDeclaration' && n.id) bind(n.id);
    if (n.type === 'ImportDeclaration') for (const sp of n.specifiers) {
      if (sp.type === 'ImportSpecifier') s.imports.set(sp.local.name, { source: n.source.value, name: sp.imported.name ?? sp.imported.value }); else s.other.add(sp.local.name);
    }
  });
  return s;
}
const MODULES = new Map(); // tree path -> parsed module, for constants imported between scanned files
function lookup(name, scope, file) {
  for (let s = scope; s; s = s.parent) {
    if (s.other.has(name)) return null;
    if (s.params.has(name)) return { param: s.params.get(name) };
    if (s.values.has(name)) return { value: s.values.get(name), scope: s, file };
    if (s.imports.has(name)) {
      const { source, name: imported } = s.imports.get(name), base = path.posix.join(path.posix.dirname(file), source);
      const target = [base, base.replace(/\.[cm]?js$/, '.ts'), base.replace(/\.[cm]?js$/, '.tsx'), `${base}.ts`, `${base}.tsx`, `${base}.js`, `${base}/index.ts`].find((c) => MODULES.has(c));
      const mod = target && MODULES.get(target);
      return mod && mod.scope.values.has(imported) ? { value: mod.scope.values.get(imported), scope: mod.scope, file: target } : null;
    }
  }
  return null;
}
// The literal strings an expression can be, or null when that is not knowable statically.
function strings(node, scope, file, depth = 0) {
  const n = unwrap(node);
  if (!n || depth > 8) return null;
  if (strOf(n) != null) return [strOf(n)];
  if (n.type === 'ConditionalExpression') { const a = strings(n.consequent, scope, file, depth + 1), b = strings(n.alternate, scope, file, depth + 1); return a && b ? [...a, ...b] : null; }
  if (n.type === 'Identifier') {
    const b = lookup(n.name, scope, file);
    if (b?.value) return strings(b.value, b.scope, b.file, depth + 1);
    const t = b?.param?.typeAnnotation?.typeAnnotation, types = t?.type === 'TSUnionType' ? t.types : t ? [t] : [];
    const lits = types.map((x) => (x.type === 'TSLiteralType' ? strOf(x.literal) : null));
    return lits.length && lits.every((x) => x != null) ? lits : null; // a parameter typed as a union of string literals
  }
  if (n.type === 'MemberExpression' && !n.computed) {
    const o = objectOf(n.object, scope, file, depth + 1), p = o?.obj.properties.find((q) => q.type === 'Property' && keyName(q) === n.property.name);
    return p ? strings(p.value, o.scope, o.file, depth + 1) : null;
  }
  return null;
}
function objectOf(node, scope, file, depth = 0) {
  const n = unwrap(node);
  if (n?.type === 'ObjectExpression') return { obj: n, scope, file };
  const b = n?.type === 'Identifier' && depth < 8 ? lookup(n.name, scope, file) : null;
  return b?.value ? objectOf(b.value, b.scope, b.file, depth + 1) : null;
}
function rootIsLogger(node, scope, file, loggers, depth = 0) {
  const n = unwrap(node);
  if (!n || depth > 8) return false;
  if (n.type === 'CallExpression') return rootIsLogger(n.callee, scope, file, loggers, depth + 1); // telemetry.child({}).info
  if (n.type === 'MemberExpression') return (!n.computed && loggers.has(n.property.name)) || rootIsLogger(n.object, scope, file, loggers, depth + 1);
  if (n.type !== 'Identifier') return false;
  if (loggers.has(n.name)) return true;
  const b = lookup(n.name, scope, file); // const child = telemetry.child(...)
  return Boolean(b?.value) && rootIsLogger(b.value, b.scope, b.file, loggers, depth + 1);
}
const SQL_WORD = /\b(?:select|insert|update|delete|from|join|into|returning|where)\b/i;
const DYNAMIC = /^(?:TemplateLiteral|BinaryExpression|CallExpression|NewExpression|TaggedTemplateExpression|SpreadElement)$/;
function jsView(file, cfg) {
  const mod = MODULES.get(file), { program, comments, lineAt, offsetOf, src } = mod;
  const loggers = new Set(cfg.logs.loggers), levels = new Set(cfg.logs.levels), logs = [], sql = [], members = new Set(), symbols = [], consumed = new Set();
  const site = (n, text, resolved) => { if (SQL_WORD.test(text)) sql.push({ line: lineAt(n.start), text, idents: [], resolved }); };
  const visit = (n, scope, symbol) => {
    const named = (n.type === 'FunctionDeclaration' || n.type === 'ClassDeclaration') && n.id ? (symbol ? `${symbol}.${n.id.name}` : n.id.name) : symbol;
    if (FN.test(n.type)) { scope = scopeOf(n, scope); symbols.push({ start: n.start, end: n.end, name: named || '(anonymous)' }); }
    if (n.type === 'MemberExpression' && !n.computed && n.object.type === 'Identifier') members.add(`${n.object.name}.${n.property.name}`);
    if (!consumed.has(n)) {
      if (n.type === 'TaggedTemplateExpression') { // holes are bound parameters, unless a call builds an identifier there
        consumed.add(n.quasi);
        site(n, n.quasi.quasis.map((q) => q.value.cooked ?? q.value.raw).join(' $? '), !n.quasi.expressions.some((e) => unwrap(e).type === 'CallExpression'));
      }
      else if (n.type === 'TemplateLiteral') site(n, n.quasis.map((q) => q.value.cooked ?? q.value.raw).join(' $? '), !n.expressions.length);
      else if (n.type === 'BinaryExpression' && n.operator === '+') {
        const parts = []; walkAst(n, (m) => { consumed.add(m); if (m.type === 'BinaryExpression' && m.operator === '+') return; parts.push(m); return false; });
        site(n, parts.map((p) => strOf(p) ?? ' $? ').join(''), parts.every((p) => strOf(p) != null));
      } else if (n.type === 'Literal' && typeof n.value === 'string') site(n, n.value, true);
    }
    const callee = n.type === 'CallExpression' ? unwrap(n.callee) : null;
    if (callee?.type === 'MemberExpression' && !callee.computed && levels.has(callee.property.name) && rootIsLogger(callee.object, scope, file, loggers)) {
      const ev = n.arguments[cfg.logs.event_arg], fa = n.arguments[cfg.logs.fields_arg], vals = ev && strings(ev, scope, file);
      const obj = fa ? objectOf(fa, scope, file) : null, named_ = obj ? obj.obj.properties.filter((p) => p.type === 'Property' && keyName(p) != null) : [];
      if (ev) logs.push({ line: lineAt(n.start), event: vals ? { kind: 'literal', values: vals } : { kind: 'dynamic', variable: !DYNAMIC.test(unwrap(ev).type) },
        fields: named_.map(keyName), spread: Boolean(fa) && (!obj || named_.length !== obj.obj.properties.length) });
    }
    const hint = (c) => (n.type === 'VariableDeclarator' && c === n.init && n.id.type === 'Identifier' ? n.id.name
      : (n.type === 'Property' || n.type === 'MethodDefinition' || n.type === 'PropertyDefinition') && c === n.value ? keyName(n) : null);
    for (const k in n) {
      if (k === 'parent') continue;
      for (const c of [n[k]].flat()) if (c && typeof c.type === 'string') { const h = hint(c); visit(c, scope, h ? (named ? `${named}.${h}` : h) : named); }
    }
  };
  visit(program, mod.scope, '');
  const symbolAt = (line) => { const off = offsetOf(line); let best = null; for (const s of symbols) if (s.start <= off && off < s.end && (!best || s.start >= best.start)) best = s; return best?.name ?? ''; };
  return { src, comments, logs, sql, members, symbolAt };
}

// ---------- the Python emitter (stdlib ast + tokenize, isolated, the reviewer's interpreter) ----------
const PYTHON = () => process.env.SLOP_CHECK_PYTHON || 'python3';
function emitPython(tree, cfg, files) {
  if (!files.length) return new Map();
  const input = JSON.stringify({ root: tree.root, files, loggers: cfg.logs.loggers, levels: cfg.logs.levels, event_arg: cfg.logs.event_arg, fields_arg: cfg.logs.fields_arg });
  const r = spawnSync(PYTHON(), ['-I', '-S', '-B', path.join(HERE, 'emit_py.py')], { input, encoding: 'utf8', maxBuffer: 1 << 28 });
  if (r.status !== 0) throw new Incomplete(`python emitter failed (${PYTHON()}): ${clip(r.stderr || r.error?.message)}`);
  const by = new Map(files.map((f) => [f, { src: tree.read(f), comments: [], logs: [], sql: [], members: new Set(), symbols: [] }]));
  for (const l of r.stdout.split('\n').filter(Boolean)) {
    const rec = JSON.parse(l), slot = by.get(rec.path);
    if (rec.kind === 'error') throw new Incomplete(`${rec.path}: ${rec.message}`);
    ({ log: slot.logs, sql: slot.sql, symbol: slot.symbols }[rec.kind] ?? slot.comments).push(rec.kind === 'comment' ? { ...rec, own: rec.own_line } : rec);
  }
  for (const v of by.values()) v.symbolAt = (line) => v.symbols.filter((s) => s.line <= line && line <= s.end).sort((a, b) => b.line - a.line)[0]?.name ?? '';
  return by;
}
// One parsed view per source file: comments, log calls, SQL candidates and symbols, for both stacks.
function parseSources(tree, cfg) {
  const out = emitPython(tree, cfg, tree.sources.filter((f) => f.endsWith('.py'))), js = tree.sources.filter((f) => JS_EXT.test(f));
  MODULES.clear();
  for (const f of js) { const src = tree.read(f), p = parseJs(f, src); MODULES.set(f, { ...p, src, scope: scopeOf({ body: p.program.body }, null) }); }
  for (const f of js) out.set(f, jsView(f, cfg));
  return out;
}

// ---------- check 1: docs and comments (refs, history, markers, suppressions) ----------
const TIER1 = [
  [/\bpreviously\b[^.!?]*(?:\bnow\b|[.!?]\s+now\b)/gi, 'narrates a change ("previously ... now")'],
  [/\b(?:was|were)\s+(?:renamed|replaced|removed|rewritten)\b/gi, 'narrates a change ("was renamed/replaced/removed/rewritten")'],
  [/(?<!\b(?:is|are|was|were|be|been|being)(?:\s+not)?\s+)\bused to\s+[a-z]+/gi, 'narrates past behavior ("used to ...")'],
];
const TIER2 = /\b(?:no longer|deprecated|replaced by|as before|(?:round|lane|wave) \d+|R\d+-[A-Z]|(?:has|have|had) been (?:renamed|replaced|removed|deleted|dropped|moved|rewritten)|(?:was|were) (?:deleted|dropped|moved))\b/gi;
const HISTORY_HEADING = /^(?:version history|history|change ?log|changes|what'?s new|release notes|v?\d+\.\d+(?:\.\d+)?(?:\s*[-–(].*)?)$/i;
const PATHLIKE = /(?:^|[\s([`'"])((?:\.{1,2}\/)?[\w@.-]+(?:\/[\w@.-]+)+\.[A-Za-z]\w{0,5})(?=$|[\s)\]`'",;:#]|\.(?:\s|$))/g;
const sentenceAt = (text, i, j) => {
  const s = Math.max(...['.', '!', '?'].map((c) => text.lastIndexOf(c, i - 1))) + 1, e = text.slice(j).search(/[.!?](?:\s|$)/);
  return norm(text.slice(s, e < 0 ? text.length : j + e + 1));
};
function historyHits(text, lineAt, push, tier1Severity, where) {
  for (const [re, why] of TIER1) for (const m of text.matchAll(re)) push('history', lineAt(m.index), `${where} ${why}`, sentenceAt(text, m.index, m.index + m[0].length), tier1Severity);
  for (const m of text.matchAll(TIER2)) push('history-ambiguous', lineAt(m.index), `${where} may narrate history ("${m[0]}"); confirm it states current behavior`, sentenceAt(text, m.index, m.index + m[0].length), 'warning');
}
function markerHits(text, lineAt, push, cfg) {
  const tracker = new RegExp(cfg.comments.tracker);
  for (const m of text.matchAll(/\b(TODO|FIXME)\b/g)) { // each marker needs its own tracker, in its parentheses or its clause
    if (!tracker.test(text.slice(m.index + m[0].length).split(/[.!?](?:\s|$)|\n|\b(?:TODO|FIXME)\b/)[0])) push('markers', lineAt(m.index), `${m[1]} without a tracker reference`, sentenceAt(text, m.index, m.index), cfg.comments.markers);
  }
}
function refCandidate(raw) {
  const p = raw.replace(/#.*$/, '').replace(/(?::\d+)+$/, '');
  if (!p || /^[a-z][\w+.-]*:/i.test(p) || /[*<>{}$\s~=]/.test(p) || p.startsWith('@')) return null;
  return /^[^/.][^/]*\.[a-z]{2,}\//i.test(p) && !/\.(?:[cm]?[jt]sx?|py|md|json|toml|ya?ml|sql|css|html|sh)\//i.test(p) ? null : p; // a host name, not a path
}
const allowed = (tree, cfg, p) => { const q = p.replace(/^\.?\//, ''); return cfg.docs.external_prefixes.some((x) => q.startsWith(x)) || tree.skip(q); };
function strictRef(tree, cfg, from, p) { // link targets: relative to the document, or the root for "/"
  if (allowed(tree, cfg, p)) return true;
  const target = p.startsWith('/') ? p.slice(1) : path.posix.join(path.posix.dirname(from), p);
  return tree.exists(target) || tree.skip(path.posix.normalize(target));
}
function looseRef(tree, cfg, from, p) { // prose and comments: the folder, its ancestors, the root, or a tail of some path
  if (allowed(tree, cfg, p)) return true;
  if (/^\.{1,2}\//.test(p) || p.startsWith('/')) return strictRef(tree, cfg, from, p);
  for (let d = path.posix.dirname(from); ; d = path.posix.dirname(d)) { if (tree.exists(path.posix.join(d, p))) return true; if (d === '.' || d === '/') break; }
  return tree.endsWith(p);
}
const decode = (u) => { try { return decodeURI(u); } catch { return u; } };
const codeSpanPath = (p, tree) => (p.includes('/')
  ? /\.[A-Za-z]\w{0,5}$|\/$/.test(p) || /^\.{1,2}\//.test(p) || ((p.match(/\//g).length >= 2 || /^[A-Z]/.test(p.split('/').at(-1))) && tree.exists(p.split('/')[0]))
  : /^[\w.-]+\.(?:md|toml|ya?ml|sql|sh|txt)$/i.test(p));
function blockText(node) {
  let text = ''; const marks = [];
  const add = (t, line, sep) => { if (sep) text += ' '; marks.push([text.length, line]); text += t; };
  const visit = (n) => {
    if (n.type === 'text') n.value.split('\n').forEach((t, i) => add(t, n.position.start.line + i, i > 0));
    else if (/^(?:inlineCode|html|break|image)$/.test(n.type)) add(' ', n.position.start.line);
    else (n.children ?? []).forEach(visit);
  };
  visit(node);
  return { text: text.replace(/"[^"]*"|“[^”]*”/g, (m) => ' '.repeat(m.length)), lineAt: (i) => marks.findLast(([o]) => o <= i)?.[1] ?? node.position.start.line };
}
export function checkDocs(tree, cfg) {
  const out = [], exclude = matcher(cfg.docs.history_exclude);
  for (const f of tree.docs) {
    const push = (rule, line, message, fp, severity) => out.push({ rule: `docs/${rule}`, path: f, line, message, fp: norm(fp), severity });
    const visit = (n, quote, inLink) => {
      const line = n.position?.start.line ?? 0;
      if (n.type === 'code' || n.type === 'html') return;
      if (/^(?:link|definition|image)$/.test(n.type)) { const p = refCandidate(decode(n.url)); if (p && !strictRef(tree, cfg, f, p)) push('refs', line, `link target ${p} does not exist`, p, 'error'); }
      if (n.type === 'text') {
        for (const m of n.value.matchAll(/\[([^\]\n]+)\]\[([^\]\n]*)\]/g)) if (!(m[2] || m[1]).startsWith('^')) push('refs', line, `reference link [${m[2] || m[1]}] has no definition`, m[0], 'error');
        for (const m of n.value.matchAll(PATHLIKE)) { const p = refCandidate(m[1]); if (p && !looseRef(tree, cfg, f, p)) push('refs', line, `path ${p} does not exist`, p, 'error'); }
      }
      if (n.type === 'inlineCode' && !inLink) { // link text is not a path; its target is
        const p = refCandidate(n.value);
        if (p && !p.startsWith('/') && codeSpanPath(p, tree) && !looseRef(tree, cfg, f, p)) push('refs', line, `path ${p} does not exist`, p, 'error');
      }
      if (/^(?:paragraph|heading|tableCell)$/.test(n.type) && !quote) {
        const { text, lineAt } = blockText(n);
        if (n.type === 'heading' && HISTORY_HEADING.test(text.trim()) && !exclude(f)) push('history', line, 'version-history heading in a reference doc', text, 'error');
        markerHits(text, lineAt, push, cfg);
        if (!exclude(f)) historyHits(blankTrackers(text), lineAt, push, 'error', 'reference doc');
      }
      for (const c of n.children ?? []) visit(c, quote || n.type === 'blockquote', inLink || n.type === 'link');
    };
    visit(fromMarkdown(tree.read(f), { extensions: [gfmTable()], mdastExtensions: [gfmTableFromMarkdown()] }), false, false);
  }
  return out;
}
// Inline suppressions by directive grammar, per language; each entry says whether it gives a reason or codes.
function directives(text, isPy) {
  const out = [];
  if (isPy) {
    for (const seg of text.split('#').map((s) => s.trim())) {
      let m;
      if ((m = seg.match(/^noqa\b(\s*:\s*[A-Z]+\d+)?/i))) out.push(Boolean(m[1]));
      else if ((m = seg.match(/^type:\s*ignore\b(\[[^\]]+\])?/))) out.push(Boolean(m[1]));
      else if ((m = seg.match(/^pyright:\s*(.+)$/))) { // options are comma-separated; "strict" alone suppresses nothing
        const opts = m[1].split(',').map((o) => o.trim()).filter((o) => o && o !== 'strict');
        if (opts.length) out.push(opts.every((o) => /^ignore\[[^\]]+\]|^report\w+\s*=/.test(o)));
      }
      else if ((m = seg.match(/^ruff:\s*noqa\b(\s*:\s*\S+)?/))) out.push(Boolean(m[1]));
    }
    return out;
  }
  const lines = text.split('\n').map((l) => l.replace(/^\s*(?:\*|\/+)?\s*/, '').trim()), t = lines.join('\n').trim();
  let m;
  if ((m = t.match(/^(?:eslint|oxlint)-disable(?:-next-line|-line)?\b(.*)$/s))) out.push(/\s--\s*\S/.test(m[1]));
  else if (/^eslint\s+["']?[@\w/-]+["']?\s*:/.test(t)) out.push(/\s--\s*\S/.test(t));
  else for (const l of lines) if ((m = l.match(/^@ts-(?:ignore|expect-error|nocheck)\b(.*)$/))) out.push(/\S/.test(m[1].replace(/^[\s:–—-]+/, ''))); // TypeScript reads any line
  return out;
}
export function checkComments(tree, cfg, parsed) {
  const out = [];
  for (const [f, view] of parsed) {
    const lines = view.src.split('\n'), isPy = f.endsWith('.py');
    const push = (rule, line, message, fp, severity) => out.push({ rule: `comments/${rule}`, path: f, line, message, fp: norm(fp), severity });
    for (const c of view.comments) {
      const lineAt = (idx) => c.line + (c.text.slice(0, idx).match(/\n/g)?.length ?? 0);
      for (const m of c.text.matchAll(PATHLIKE)) { const p = refCandidate(m[1]); if (p && !looseRef(tree, cfg, f, p)) push('refs', lineAt(m.index), `comment names ${p}, which does not exist`, p, 'error'); }
      historyHits(blankTrackers(c.text), lineAt, push, 'warning', 'comment');
      markerHits(c.text, lineAt, push, cfg);
      if (c.kind === 'docstring') continue;
      const next = lines.slice(c.endLine ?? c.line).find((l) => l.trim()) ?? '';
      const fp = `${view.symbolAt(c.line)} :: ${norm(c.text)} :: ${norm(c.own ? next : lines[c.line - 1])}`; // binds to the code it silences
      for (const explained of directives(c.text, isPy)) {
        push('suppression', c.line, `inline suppression: ${clip(c.text)}`, fp, 'error');
        if (!explained) push('unexplained-suppression', c.line, 'suppression gives no reason or rule codes', fp, 'error');
      }
    }
  }
  return out;
}

// ---------- check 2: logs (dynamic event names, secret keys, contract reconciliation) ----------
const words = (k) => k.replace(/([a-z0-9])([A-Z])/g, '$1 $2').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
function loadContract(tree, cfg) {
  if (!tree.exists(cfg.logs.contract)) throw new Incomplete(`log contract ${cfg.logs.contract} is missing`);
  let c; try { c = JSON.parse(tree.read(cfg.logs.contract)); } catch { throw new Incomplete(`log contract ${cfg.logs.contract} is not valid JSON`); }
  const ok = c && c.events && typeof c.events === 'object' && Object.values(c.events).every((e) => e && ['fields', 'optional', 'spread'].every((k) => e[k] === undefined || (Array.isArray(e[k]) && e[k].every((x) => typeof x === 'string'))));
  if (!ok) throw new Incomplete(`log contract ${cfg.logs.contract} must be {"events": {name: {fields, optional, spread}}}`);
  return c.events;
}
export function checkLogs(tree, cfg, parsed) {
  const out = [], secret = new Set(cfg.logs.secret_words), seen = new Set(), contract = loadContract(tree, cfg), isTest = matcher(cfg.repo.test_roots);
  const push = (f, line, rule, message, fp) => out.push({ rule: `logs/${rule}`, path: f, line, message, fp: norm(fp), severity: 'error' });
  for (const [f, view] of parsed) if (!isTest(f)) for (const call of view.logs) {
    const { line, event } = call;
    for (const k of call.fields) if (words(String(k)).some((w) => secret.has(w)) || secret.has(String(k).toLowerCase().replace(/[^a-z0-9]/g, ''))) push(f, line, 'secret-key', `field "${k}" looks secret-bearing`, `${f}:${k}`);
    if (event.kind !== 'literal') { push(f, line, 'dynamic-event', event.variable ? 'event name is a variable that does not resolve to literals' : 'event name is built at run time; use a literal or a bounded literal set', `${f}:${line}`); continue; }
    for (const ev of event.values) {
      seen.add(ev);
      const spec = contract[ev];
      if (!spec) { push(f, line, 'contract', `event "${ev}" is not in ${cfg.logs.contract}`, ev); continue; }
      const listed = new Set([...(spec.fields ?? []), ...(spec.optional ?? []), ...(spec.spread ?? [])]);
      for (const k of call.fields) if (!listed.has(String(k))) push(f, line, 'contract', `event "${ev}" logs field "${k}", which ${cfg.logs.contract} does not list`, `${ev}.${k}`);
      for (const k of spec.fields ?? []) if (!call.fields.map(String).includes(k) && !(call.spread && (spec.spread ?? []).includes(k))) push(f, line, 'contract', `event "${ev}" is missing required field "${k}" here`, `${ev}.${k}`);
    }
  }
  for (const ev of Object.keys(contract).sort(sortBy)) if (!seen.has(ev)) push(cfg.logs.contract, 0, 'contract', `contract event "${ev}" has no producer in the source roots`, ev);
  return out;
}

// ---------- check 3: schema (declared vs migrated catalog, hazards, revision graph, inventory) ----------
export function migrationsDigest(tree, cfg) {
  const dir = cfg.schema.migrations;
  if (!dir || !tree.exists(dir)) throw new Incomplete(`schema: [schema] migrations must name the migrations folder (got "${dir}")`);
  const h = crypto.createHash('sha256');
  for (const f of tree.files.filter((x) => under(x, [dir]) && !x.split('/').some((s) => SCAN_SKIP.has(s)))) h.update(`${f}\0${tree.read(f)}\0`);
  return h.digest('hex');
}
export function catalogQuery(tree, cfg) {
  const lit = (s) => `'${String(s).replace(/'/g, "''")}'`, schemas = `array[${cfg.schema.schemas.map(lit).join(', ')}]::text[]`;
  const version = cfg.schema.orm === 'alembic' ? `,\n 'alembic_version', (select coalesce(json_agg(version_num order by version_num), '[]'::json) from "${cfg.schema.schemas[0].replace(/"/g, '""')}".alembic_version)` : '';
  return `select json_build_object(
 'migrations_digest', ${lit(migrationsDigest(tree, cfg))},
 'columns', coalesce((select json_agg(json_build_object('schema', c.table_schema, 'table', c.table_name, 'column', c.column_name, 'nullable', c.is_nullable = 'YES', 'type', c.data_type, 'default', c.column_default) order by c.table_schema, c.table_name, c.column_name)
   from information_schema.columns c join information_schema.tables t on t.table_schema = c.table_schema and t.table_name = c.table_name
   where t.table_type = 'BASE TABLE' and c.table_schema = any(${schemas})), '[]'::json),
 'indexes', coalesce((select json_agg(json_build_object('schema', n.nspname, 'table', r.relname, 'name', ix.relname, 'primary', i.indisprimary, 'unique', i.indisunique,
     'columns', (select json_agg(a.attname order by a.attname) from pg_attribute a where a.attrelid = r.oid and a.attnum = any(i.indkey))) order by n.nspname, r.relname, ix.relname)
   from pg_index i join pg_class r on r.oid = i.indrelid join pg_class ix on ix.oid = i.indexrelid join pg_namespace n on n.oid = r.relnamespace where n.nspname = any(${schemas})), '[]'::json)${version});`;
}
const BOOKKEEPING = new Set(['alembic_version', '__drizzle_migrations']);
function loadCatalog(tree, cfg, heads, command) {
  if (cfg.schema.production_dialect !== 'postgresql') throw new Incomplete(`schema: production dialect ${cfg.schema.production_dialect} has no catalog reader; review it by hand (manual-review-required)`);
  const file = 'the --catalog-command output', redo = 'apply the migrations to a disposable PostgreSQL and pass --catalog-command "psql ... $DATABASE_URL"';
  if (!command) throw new Incomplete(`schema: no --catalog-command; ${redo}`);
  // The reviewer's command reads the catalog query on stdin, in the repository root; no file in the tree is evidence.
  const r = spawnSync(command, { shell: true, cwd: tree.root, input: catalogQuery(tree, cfg), encoding: 'utf8', maxBuffer: 1 << 28, timeout: 300000 });
  if (r.status !== 0) throw new Incomplete(`schema: --catalog-command failed (exit ${r.status}): ${clip(r.stderr || r.error?.message)}`);
  const text = r.stdout;
  let raw; try { raw = JSON.parse(text); } catch { throw new Incomplete(`schema: ${file} is not valid JSON; ${redo}`); }
  const good = raw && Array.isArray(raw.columns) && raw.columns.length && Array.isArray(raw.indexes)
    && raw.columns.every((c) => typeof c.schema === 'string' && typeof c.table === 'string' && typeof c.column === 'string' && typeof c.nullable === 'boolean');
  if (!good) throw new Incomplete(`schema: ${file} has no migrated columns or an unknown shape; ${redo}`);
  if (raw.migrations_digest !== migrationsDigest(tree, cfg)) throw new Incomplete(`schema: ${file} was not dumped from the current migrations; ${redo}`);
  if (heads && JSON.stringify([...(raw.alembic_version ?? [])].sort()) !== JSON.stringify([...heads].sort())) throw new Incomplete(`schema: the dumped database is at ${JSON.stringify(raw.alembic_version ?? null)}, not the heads ${JSON.stringify(heads)}; ${redo}`);
  const tables = new Map();
  for (const c of raw.columns) if (!BOOKKEEPING.has(c.table)) { const k = `${c.schema}.${c.table}`; (tables.get(k) ?? tables.set(k, { name: c.table, columns: new Map(), pk: [], unique: [] }).get(k)).columns.set(c.column, c); }
  for (const i of raw.indexes) { const t = tables.get(`${i.schema}.${i.table}`); if (t && i.primary) t.pk = i.columns ?? []; else if (t && i.unique) t.unique.push(i.columns ?? []); }
  return tables;
}
function runHelper(tree, cfg, models) {
  const py = models ? process.env.SLOP_CHECK_MODELS_PYTHON : PYTHON(), args = [...(models ? ['-B'] : ['-I', '-S', '-B']), path.join(HERE, 'alembic_helper.py'), ...(models ? ['--models'] : [])];
  if (!py) throw new Incomplete('schema: target_metadata is set; point SLOP_CHECK_MODELS_PYTHON at the repository test environment interpreter to compare models');
  const r = spawnSync(py, args, { cwd: tree.root, input: JSON.stringify(cfg.schema), encoding: 'utf8' });
  if (r.status !== 0) throw new Incomplete(`alembic helper failed (${py}): ${clip(r.stderr || r.error?.message)}`);
  return JSON.parse(r.stdout);
}
async function declaredSchema(tree, cfg) {
  if (cfg.schema.orm === 'drizzle') return (await import('./drizzle.mjs')).declared(tree, cfg);
  const graph = runHelper(tree, cfg, false);
  if (!cfg.schema.target_metadata) {
    return { tables: null, heads: graph.heads, findings: graph.findings,
      notes: ['schema: no target_metadata in cleanup.toml, so the model-versus-migration comparison is skipped; the migrated catalog is the schema of record'] };
  }
  const m = runHelper(tree, cfg, true);
  return { tables: m.tables ?? null, mappers: m.mappers ?? [], associations: m.associations ?? [], heads: graph.heads, findings: [...graph.findings, ...m.findings], notes: [] };
}
export async function checkSchema(tree, cfg, command) {
  if (cfg.schema.orm === 'none') throw new Incomplete('schema is a required check but [schema] orm is "none"; set orm or drop schema from [checks] required');
  const decl = await declaredSchema(tree, cfg), graphOk = !decl.findings.some((f) => /heads|chain|cycle|duplicate-revision/.test(f.rule)); // a broken graph is the finding
  const catalog = loadCatalog(tree, cfg, cfg.schema.orm === 'alembic' && graphOk ? decl.heads : null, command);
  const out = decl.findings.map((f) => ({ ...f, fp: norm(f.fp ?? f.message) })), multi = cfg.schema.schemas.length > 1;
  const show = (k) => (multi ? k : k.slice(k.indexOf('.') + 1));
  const where = cfg.schema.schema_files[0] || cfg.schema.target_metadata || cfg.schema.catalog;
  const push = (rule, message, fp, severity = 'error') => out.push({ rule: `schema/${rule}`, path: where, line: 0, message, fp, severity });
  for (const [t, info] of catalog) if (!info.pk.length) push('no-primary-key', `table ${show(t)} has no physical primary key`, t, cfg.schema.require_physical_primary_key ? 'error' : 'warning');
  if (decl.tables) {
    for (const [t, d] of Object.entries(decl.tables)) {
      const live = catalog.get(t);
      if (!live) { push('missing-table', `declared table ${show(t)} is not in the migrated catalog`, t); continue; }
      for (const [c, col] of Object.entries(d.columns)) {
        const lc = live.columns.get(c);
        if (!lc) push('missing-column', `declared column ${show(t)}.${c} is not in the migrated catalog`, `${t}.${c}`);
        else if (lc.nullable !== col.nullable) push('nullability', `${show(t)}.${c} is ${col.nullable ? 'nullable' : 'not null'} in the schema but ${lc.nullable ? 'nullable' : 'not null'} after migrating`, `${t}.${c}`);
      }
      for (const c of live.columns.keys()) if (!(c in d.columns)) push('undeclared-column', `migrated column ${show(t)}.${c} is not declared in the schema`, `${t}.${c}`);
    }
    for (const t of catalog.keys()) if (!(t in decl.tables)) push('undeclared-table', `migrated table ${show(t)} is not declared in the schema`, t);
  }
  for (const m of decl.mappers ?? []) { // a mapper key needs a primary key or a NOT NULL unique index behind it
    const live = catalog.get(m.table), same = (cols) => JSON.stringify([...cols].sort()) === JSON.stringify([...m.key].sort());
    const backed = live && (same(live.pk) || live.unique.some((u) => same(u) && u.every((c) => live.columns.get(c)?.nullable === false)));
    if (!backed) push('row-identity', `${m.class} maps ${show(m.table)} by (${m.key.join(', ')}), which is neither its primary key nor a NOT NULL unique index`, `${m.table}:${m.class}`);
  }
  return { findings: out, notes: decl.notes ?? [], decl, catalog, show };
}
function sqlIdents(text) {
  const clean = text.replace(/--[^\n]*|\/\*[\s\S]*?\*\/|'(?:[^']|'')*'/g, ' ');
  const ids = [...clean.matchAll(/"((?:[^"]|"")+)"|([A-Za-z_][\w$]*)/g)].map((m) => (m[1] ? m[1].replace(/""/g, '"') : m[2].toLowerCase()));
  return { ids: new Set(ids), star: /\b(?:select|returning)\s+(?:distinct\s+)?\*|\.\*/i.test(clean), insert: /^\s*insert\b/i.test(clean) && !/\b(?:select|returning)\b/i.test(clean) };
}
export async function inventory(tree, cfg, command) {
  const { catalog, decl, show } = await checkSchema(tree, cfg, command), parsed = parseSources(tree, cfg);
  const skip = matcher([...cfg.repo.test_roots, ...cfg.schema.schema_files, ...(cfg.schema.migrations ? [cfg.schema.migrations] : [])]);
  const sites = [], members = new Set();
  const add = (f, s) => sites.push({ f, ...s, ...sqlIdents([s.text, ...s.idents.map((i) => `"${i}"`)].join(' ')) });
  for (const [f, v] of parsed) if (!skip(f)) { for (const s of v.sql) add(f, s); for (const m of v.members) members.add(m); }
  for (const f of tree.sqlFiles) add(f, { line: 1, text: tree.read(f), idents: [], resolved: true });
  const names = new Set([...catalog.values()].map((t) => t.name)), shape = (t) => [...t.columns.keys()].filter((c) => !t.pk.includes(c)).sort().join(',');
  const shapes = group([...catalog].filter(([, t]) => shape(t).includes(',')), ([, t]) => shape(t)), rows = [];
  for (const [k, info] of [...catalog].sort(([a], [b]) => sortBy(a, b))) {
    const t = info.name, inTable = (s) => s.ids.has(t) || ![...names].some((x) => s.ids.has(x)), orm = decl.tables?.[k]?.orm;
    const twins = (shapes.get(shape(info)) ?? []).filter(([o]) => o !== k).map(([o]) => show(o)), star = sites.some((s) => s.star && s.ids.has(t));
    for (const [c, col] of [...info.columns].sort(([a], [b]) => sortBy(a, b))) {
      const refs = sites.filter((s) => s.resolved && inTable(s) && s.ids.has(c)), key = decl.tables?.[k]?.columns?.[c]?.key, viaOrm = Boolean(orm && key && members.has(`${orm}.${key}`));
      const notes = [col.default != null && 'has a database default', info.pk.includes(c) && 'primary key', star && 'its table is read through SELECT * or RETURNING *',
        refs.length && refs.every((s) => s.insert) && !viaOrm && 'named only in INSERT statements', (decl.associations ?? []).includes(k) && 'association table (ORM-managed)',
        twins.length && `same column shape as ${twins.join(', ')}; compare for a redundant fact`].filter(Boolean);
      const status = refs.length || viaOrm ? 'referenced' : star ? 'inconclusive' : sites.some((s) => !s.resolved && inTable(s)) ? 'unresolved' : 'unreferenced';
      rows.push({ table: show(k), column: c, status, notes });
    }
  }
  return { rows, unresolved: sites.filter((s) => !s.resolved).map((s) => ({ path: s.f, line: s.line, sql: clip(s.text) })) };
}

// ---------- check 4: finding-fingerprinted baseline, tool reports, and the policy comparison ----------
const NEVER_BASELINED = new Set(['knip:unlisted', 'knip:unresolved', 'deptry:DEP001']);
const rowKey = (r) => JSON.stringify([r.tool, r.rule, r.path, r.fingerprint]);
const ROW_KEYS = ['tool', 'rule', 'path', 'fingerprint', 'count', 'reason'];
const rowLine = (r) => JSON.stringify(Object.fromEntries(ROW_KEYS.filter((k) => r[k] !== undefined).map((k) => [k, r[k]])));
export function readRows(file) {
  if (!fs.existsSync(file)) return [];
  const rows = [], seen = new Set();
  fs.readFileSync(file, 'utf8').split('\n').forEach((l, i) => {
    if (!l.trim()) return;
    let r; try { r = JSON.parse(l); } catch { throw new Incomplete(`${file}:${i + 1}: baseline row is not JSON`); }
    const valid = r && typeof r === 'object' && Object.keys(r).every((k) => ROW_KEYS.includes(k)) && ['tool', 'rule', 'path', 'fingerprint'].every((k) => typeof r[k] === 'string' && r[k])
      && Number.isInteger(r.count) && r.count >= 1 && (r.reason === undefined || typeof r.reason === 'string');
    if (!valid) throw new Incomplete(`${file}:${i + 1}: baseline row needs tool, rule, path, fingerprint and a positive integer count`);
    if (seen.has(rowKey(r))) throw new Incomplete(`${file}:${i + 1}: duplicate baseline row`);
    if (NEVER_BASELINED.has(`${r.tool}:${r.rule}`)) throw new Incomplete(`${file}:${i + 1}: ${r.tool} ${r.rule} always fails and cannot be baselined`);
    seen.add(rowKey(r)); rows.push(r);
  });
  return rows;
}
const CONVERT = {
  ruff: (j) => j.map((d) => ({ rule: d.code ?? 'syntax-error', path: d.filename, line: d.location?.row, message: d.message })),
  oxlint: (j) => j.diagnostics.filter((d) => d.severity === 'error').map((d) => ({ rule: d.code ?? (/^Unused .*directive/.test(d.message) ? 'unused-disable-directive' : 'oxlint'), path: d.filename, line: d.labels?.[0]?.span?.line, message: d.message })),
  pyright: (j) => j.generalDiagnostics.filter((d) => d.severity === 'error').map((d) => ({ rule: d.rule ?? d.severity, path: d.file, line: d.range.start.line + 1, message: d.message })),
  knip: (j) => [...j.issues.flatMap((i) => Object.entries(i).filter(([, v]) => Array.isArray(v)).flatMap(([rule, v]) => v.flat().filter((x) => x?.name).map((x) => ({ rule, path: i.file, line: x.line, fp: x.name, message: `${rule}: ${x.name}` }))))],
  deptry: (j) => j.map((d) => ({ rule: d.error.code, path: d.location.file, line: d.location.line, fp: d.module, message: d.error.message })),
  pydoclint: (text) => { // file lines, then indented "N: DOCnnn: message" lines; anything else means it did not run cleanly
    let cur = null;
    return text.split('\n').filter((l) => l.trim()).map((l) => {
      const m = l.match(/^\s+(\d+): (DOC\d+): (.*)$/);
      if (m && cur) return { rule: m[2], path: cur, line: +m[1], message: m[3] };
      if (/^\S+$/.test(l) && /\.pyi?$/.test(l)) { cur = l; return null; }
      throw new Error(`unrecognized line "${clip(l)}"`);
    }).filter(Boolean);
  },
};
function readReport(tree, tool, spec, views) {
  const exitFile = `${spec.file}.exit`;
  if (!tree.exists(spec.file) || !tree.exists(exitFile)) throw new Incomplete(`report for ${tool} is missing: write ${spec.file} and its exit status to ${exitFile} on every run`);
  const code = tree.read(exitFile).trim();
  if (code !== '0' && code !== '1') throw new Incomplete(`${tool} did not complete (exit ${code || 'unknown'}); its report is not evidence`);
  let items;
  try { const text = tree.read(spec.file); items = CONVERT[tool](tool === 'pydoclint' ? text : JSON.parse(text)); } catch (e) { throw new Incomplete(`${tool} report ${spec.file} is unreadable: ${clip(e.message)}`); }
  if ((code === '0') !== (items.length === 0)) throw new Incomplete(`${tool} exited ${code} but its report has ${items.length} error finding(s); regenerate it`);
  const real = fs.realpathSync(tree.root);
  return items.map((it) => {
    const raw = String(it.path ?? '.'), abs = path.isAbsolute(raw) ? raw : path.join(real, spec.cwd, raw);
    const p = path.relative(real, fs.existsSync(abs) ? fs.realpathSync(abs) : abs).split(path.sep).join('/');
    if (p.startsWith('..') || path.isAbsolute(p)) throw new Incomplete(`${tool} reports a path outside the tree: ${raw}`);
    const src = it.line && tree.exists(p) ? tree.read(p).split('\n')[it.line - 1] : null, sym = views.get(p)?.symbolAt(it.line ?? 0) ?? '';
    return { tool, rule: String(it.rule), path: p, line: it.line ?? 0, message: it.message ?? it.rule, fp: norm(it.fp ?? (src != null ? `${sym} :: ${src}` : it.message)), severity: 'error' };
  });
}
export function compareBaseline(findings, rows) {
  const cur = group(findings, (f) => rowKey({ ...f, fingerprint: f.fp })), out = [], base = new Map(rows.map((r) => [rowKey(r), r]));
  for (const [k, list] of cur) list.slice(NEVER_BASELINED.has(`${list[0].tool}:${list[0].rule}`) ? 0 : (base.get(k)?.count ?? 0)).forEach((f) => out.push({ ...f, status: 'new' }));
  for (const [k, r] of base) { const n = cur.get(k)?.length ?? 0; if (n < r.count) out.push({ tool: r.tool, rule: r.rule, path: r.path, line: 0, severity: 'error', status: 'stale', fp: r.fingerprint, message: `baseline expects ${r.count}, found ${n}: shrink the baseline (${clip(r.fingerprint)})` }); }
  return out;
}
const baselinable = (f) => f.severity === 'error' && !NEVER_BASELINED.has(`${f.tool}:${f.rule}`) && (f.tool !== 'slop-check' || /^(?:docs|comments)\//.test(f.rule));
function rowsFor(findings, old) {
  const counts = new Map(), reasons = new Map(old.map((r) => [rowKey(r), r.reason]));
  for (const f of findings) { const r = { tool: f.tool, rule: f.rule, path: f.path, fingerprint: f.fp }, k = rowKey(r); counts.set(k, { ...r, count: (counts.get(k)?.count ?? 0) + 1, reason: reasons.get(k) }); }
  return [...counts.values()].sort((a, b) => sortBy(rowKey(a), rowKey(b)));
}
const LINT_TABLE = /^\[\[?tool\.(?:ruff|pyright|pydoclint|deptry|mypy)\b/m;
const toolTables = (text) => text.split(/^(?=\[)/m).filter((t) => LINT_TABLE.test(t)).join(''); // a pyproject.toml's lint tables
// Configuration the linters discover on their own; every such file must be a declared policy path.
const TOOL_CONFIG = /^(?:\.?oxlintrc\.jsonc?|eslint\.config\.[cm]?[jt]s|\.eslintrc(?:\.\w+)?|tsconfig(?:\.[\w-]+)?\.json|jsconfig\.json|\.?knip\.jsonc?|knip\.config\.[cm]?[jt]s|\.?ruff\.toml|pyrightconfig\.json|setup\.cfg|tox\.ini|\.flake8|\.?mypy\.ini|biome\.jsonc?)$/;
export function comparePolicy(tree, baseDir, cfg) {
  if (!baseDir || !fs.existsSync(baseDir) || !fs.statSync(baseDir).isDirectory()) throw new Incomplete('policy: no protected policy snapshot (--base-policy-dir); comparison is incomplete');
  const baseCfg = loadConfig(baseDir), base = indexTree(baseDir), out = [];
  for (const p of baseCfg.policy.paths) if (!base.exists(p)) throw new Incomplete(`policy: the protected snapshot lacks its declared policy path ${p}`);
  const push = (rule, p, message, severity = 'error') => out.push({ tool: 'slop-check', rule: `policy/${rule}`, path: p, line: 0, message, severity, fp: message });
  const declared = [...new Set([...baseCfg.policy.paths, ...cfg.policy.paths])], baselineDirs = [cfg.baseline.dir, baseCfg.baseline.dir], contracts = [cfg.logs.contract, baseCfg.logs.contract];
  const files = [...new Set([...base.files, ...tree.files].filter((f) => under(f, declared)))].sort(sortBy), baseRows = [], candRows = [];
  for (const f of files) {
    const a = path.join(baseDir, f), b = path.join(tree.root, f), inA = base.exists(f), inB = tree.exists(f);
    if (under(f, baselineDirs) && f.endsWith('.jsonl')) { if (inA) baseRows.push(...readRows(a)); if (inB) candRows.push(...readRows(b)); continue; }
    if (contracts.includes(f) && inA && inB) {
      const ev = (p) => { try { return JSON.parse(fs.readFileSync(p, 'utf8')).events ?? {}; } catch { throw new Incomplete(`policy: ${p} is not valid JSON`); } }, A = ev(a), B = ev(b);
      for (const [name, spec] of Object.entries(A)) {
        const now = B[name];
        if (!now) { push('weakening', f, `removes published event "${name}"`); continue; }
        for (const k of spec.fields ?? []) if (!(now.fields ?? []).includes(k)) push('weakening', f, `event "${name}" stops requiring field "${k}"`);
        for (const k of spec.optional ?? []) if (![...(now.fields ?? []), ...(now.optional ?? [])].includes(k)) push('weakening', f, `event "${name}" removes field "${k}"`);
        for (const k of now.spread ?? []) if (!(spec.spread ?? []).includes(k)) push('weakening', f, `event "${name}" lets field "${k}" arrive through a spread`);
      }
      for (const name of Object.keys(B)) if (!A[name]) push('addition', f, `adds event "${name}" (review it)`, 'warning');
      continue;
    }
    const body = (p) => (f.split('/').at(-1) === 'pyproject.toml' ? toolTables(fs.readFileSync(p, 'utf8')) : fs.readFileSync(p)); // dependencies are not policy
    if (!inA || !inB || !Buffer.from(body(a)).equals(Buffer.from(body(b)))) push('weakening', f, !inA ? 'adds a policy file' : !inB ? 'removes a policy file' : 'edits a policy file (config, rule or allowance change)');
  }
  for (const f of tree.files) {
    const name = f.split('/').at(-1), tools = name === 'pyproject.toml' && LINT_TABLE.test(tree.read(f));
    if ((TOOL_CONFIG.test(name) || tools) && !under(f, declared) && !tree.skip(f)) push('weakening', f, 'a tool reads this configuration, but it is not a declared policy path');
  }
  const A = new Map(baseRows.map((r) => [rowKey(r), r])), B = new Map(candRows.map((r) => [rowKey(r), r]));
  const byA = group(baseRows, (r) => r.path), byB = group(candRows, (r) => r.path), id = (r) => JSON.stringify([r.tool, r.rule, r.fingerprint]);
  const within = (np, op) => byB.get(np).every((r) => byA.get(op).some((o) => id(o) === id(r) && r.count <= o.count)); // nothing new, nothing grew
  const olds = [...byA.keys()].filter((p) => !byB.has(p) && !tree.exists(p)), news = [...byB.keys()].filter((p) => !byA.has(p) && tree.exists(p));
  const moved = new Set(news.filter((np) => { const m = olds.filter((op) => within(np, op)); return m.length === 1 && news.filter((n2) => within(n2, m[0])).length === 1; }));
  for (const [k, r] of B) {
    if (!A.has(k)) moved.has(r.path) ? push('moved', r.path, `baseline row moved with its file: ${r.rule} ${clip(r.fingerprint)}`, 'warning') : push('weakening', r.path, `adds baseline row ${r.tool} ${r.rule} (${clip(r.fingerprint)})`);
    else if (r.count > A.get(k).count) push('weakening', r.path, `raises baseline count ${A.get(k).count} -> ${r.count} for ${r.rule} (${clip(r.fingerprint)})`);
  }
  return out;
}

// ---------- CLI ----------
const HELP = `usage: slop-check <command> --root DIR [options]
  check [--only docs,comments,logs,schema] [--warnings] [--catalog-command CMD]
                                                       the deterministic checks through the baseline
  baseline --write [--catalog-command CMD]             rewrite baseline files from a full run (keeps reasons)
  policy --base-policy-dir DIR                          compare policy paths with a protected snapshot
  inventory [--catalog-command CMD]                    advisory column inventory (never blocking)
  catalog-query                                        print the PostgreSQL catalog query for this tree`;
const OPTIONS = { check: ['only', 'warnings', 'catalog-command'], baseline: ['write', 'catalog-command'], policy: ['base-policy-dir'], inventory: ['catalog-command'], 'catalog-query': [] };
const FLAGS = new Set(['warnings', 'write', 'help']);
function parseArgs(argv) {
  const o = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { o._.push(a); continue; }
    const k = a.slice(2);
    if (FLAGS.has(k)) o[k] = true;
    else if (argv[i + 1] === undefined || argv[i + 1].startsWith('--')) throw new Usage(`--${k} needs a value`);
    else o[k] = argv[++i];
  }
  return o;
}
const fmt = (f) => JSON.stringify(Object.fromEntries(['status', 'severity', 'tool', 'rule', 'path', 'line', 'message'].filter((k) => f[k] !== undefined).map((k) => [k, f[k]])));
function emit(records) { const lines = records.map(fmt).sort(sortBy); if (lines.length) process.stdout.write(lines.join('\n') + '\n'); }
async function collect(tree, cfg, checks, withReports, command) {
  const want = (c) => checks.includes(c), out = [], notes = [];
  const parsed = want('comments') || want('logs') || withReports ? parseSources(tree, cfg) : new Map();
  if (want('docs')) out.push(...checkDocs(tree, cfg));
  if (want('comments')) out.push(...checkComments(tree, cfg, parsed));
  if (want('logs')) out.push(...checkLogs(tree, cfg, parsed));
  if (want('schema')) { const s = await checkSchema(tree, cfg, command); out.push(...s.findings); notes.push(...s.notes); }
  const own = out.map((f) => ({ tool: 'slop-check', ...f }));
  if (withReports) for (const [tool, spec] of Object.entries(cfg.baseline.reports)) own.push(...readReport(tree, tool, spec, parsed));
  return { findings: own, notes };
}
const incomplete = (message, rule = 'incomplete') => ({ status: 'incomplete', severity: 'error', tool: 'slop-check', rule, path: '.', line: 0, message });
export async function main(argv) {
  try {
    const o = parseArgs(argv), cmd = o._[0];
    if (!cmd || o.help) { console.log(HELP); return cmd ? 0 : 2; }
    if (!OPTIONS[cmd] || o._.length > 1) throw new Usage(`unknown command or argument: ${o._.join(' ')}\n${HELP}`);
    for (const k of Object.keys(o)) if (k !== '_' && k !== 'root' && !OPTIONS[cmd].includes(k)) throw new Usage(`${cmd} does not take --${k}`);
    const root = path.resolve(o.root ?? '.'), cfg = loadConfig(root);
    const only = typeof o.only === 'string' ? o.only.split(',').map((s) => s.trim()) : null;
    for (const c of only ?? []) if (!CHECKS.includes(c)) throw new Usage(`unknown check "${c}" in --only (known: ${CHECKS.join(', ')})`);
    const checks = only ?? cfg.checks.required, tree = makeTree(root, cfg, cmd === 'check' || cmd === 'baseline' ? checks : []);
    if (cmd === 'catalog-query') { console.log(catalogQuery(tree, cfg)); return 0; }
    if (cmd === 'policy') { const r = comparePolicy(tree, o['base-policy-dir'] && path.resolve(o['base-policy-dir']), cfg); emit(r); return r.some((f) => f.severity === 'error') ? 1 : 0; }
    if (cmd === 'inventory') { const inv = await inventory(tree, cfg, o['catalog-command']); for (const l of [...inv.rows.map((r) => JSON.stringify(r)), ...inv.unresolved.map((u) => JSON.stringify({ unresolved: u }))]) console.log(l); return 0; }
    const { findings, notes } = await collect(tree, cfg, checks, !only, o['catalog-command']);
    const errors = findings.filter(baselinable), byTool = group(errors, (f) => f.tool), dir = path.join(root, cfg.baseline.dir);
    if (cmd === 'baseline') {
      if (!o.write) throw new Usage('baseline needs --write');
      const tools = new Set(['slop-check', ...byTool.keys(), ...(fs.existsSync(dir) ? fs.readdirSync(dir).filter((n) => n.endsWith('.jsonl')).map((n) => n.slice(0, -6)) : [])]);
      const old = new Map([...tools].map((t) => [t, readRows(path.join(dir, `${t}.jsonl`))])); // validate every file before writing any
      fs.mkdirSync(dir, { recursive: true });
      for (const t of [...tools].sort(sortBy)) { const rows = rowsFor(byTool.get(t) ?? [], old.get(t)); fs.writeFileSync(path.join(dir, `${t}.jsonl`), rows.map(rowLine).join('\n') + (rows.length ? '\n' : '')); }
      return 0;
    }
    const rows = fs.existsSync(dir) ? fs.readdirSync(dir).filter((n) => n.endsWith('.jsonl')).sort(sortBy).flatMap((n) => readRows(path.join(dir, n))) : [];
    const result = [...compareBaseline(errors, only ? rows.filter((r) => r.tool === 'slop-check' && only.some((c) => r.rule.startsWith(`${c}/`))) : rows),
      ...findings.filter((f) => !baselinable(f) && (f.severity === 'error' || o.warnings))];
    const failed = result.filter((f) => f.severity === 'error').length, skipped = [...cfg.checks.required.filter((c) => !checks.includes(c)), ...(only && Object.keys(cfg.baseline.reports).length ? ['tool reports'] : [])];
    if (!failed && only && skipped.length) result.push(incomplete(`partial run: --only skips ${skipped.join(', ')}; a partial run is never a pass`, 'partial-run'));
    emit(result);
    for (const n of notes) process.stderr.write(`slop-check: ${n}\n`);
    const partial = result.some((f) => f.rule === 'partial-run');
    process.stderr.write(`slop-check: ${failed ? `${failed} blocking finding(s)` : partial ? 'partial run, no findings' : 'pass'}\n`);
    return failed ? 1 : partial ? 3 : 0;
  } catch (e) {
    if (e instanceof Usage) { process.stderr.write(`slop-check: ${e.message}\n`); return 2; }
    const message = e instanceof Incomplete ? e.message : `internal error: ${e?.stack ?? e}`;
    emit([incomplete(clip(message))]); process.stderr.write(`slop-check: incomplete: ${message}\n`); return 3;
  }
}
