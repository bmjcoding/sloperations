// Drizzle schema helper: reads the declared tables from [schema] schema_files through oxc-parser.
// Output: { tables: { "schema.table": { orm, columns: { sqlColumn: { key, nullable } } } }, findings }.
// Covered: `const x = pgTable/sqliteTable/mysqlTable("name", columns, extra)` and `<pgSchema const>.table(...)`,
// columns as an object or a callback returning one, spreads of a const column object declared in any
// schema file, builders with or without an explicit SQL name, NOT NULL from .notNull(), .primaryKey(),
// serial builders, identity columns, and primaryKey({ columns }) in the extra config. A table this
// cannot read completely makes the check incomplete rather than comparing part of it.
import { Incomplete, keyName, parseJs, strOf, unwrap, walkAst } from './core.mjs';

const TABLE_FNS = new Set(['pgTable', 'sqliteTable', 'mysqlTable']);
const NOT_NULL_BUILDERS = new Set(['serial', 'smallserial', 'bigserial']);
const NOT_NULL_METHODS = new Set(['notNull', 'primaryKey', 'generatedAlwaysAsIdentity', 'generatedByDefaultAsIdentity']);
const snake = (s) => s.replace(/([a-z0-9])([A-Z])/g, '$1_$2').toLowerCase();
const nameOf = (n) => (n?.type === 'Identifier' ? n.name : n?.type === 'MemberExpression' && !n.computed ? n.property.name : null);

// Walks a chain such as integer("x").notNull().references(() => t.id) down to its builder call.
function column(value) {
  const methods = []; let n = unwrap(value), builder = null;
  while (n?.type === 'CallExpression') {
    builder = n;
    const callee = unwrap(n.callee);
    if (callee.type !== 'MemberExpression') break;
    methods.push(callee.property.name);
    n = unwrap(callee.object);
  }
  if (!builder) return null;
  const base = nameOf(unwrap(builder.callee));
  return { sql: strOf(builder.arguments[0]), notNull: methods.some((m) => NOT_NULL_METHODS.has(m)) || NOT_NULL_BUILDERS.has(base) };
}

// Column keys named in primaryKey({ columns: [t.a, t.b] }) or primaryKey(t.a, t.b), however imported.
function compositeKeys(extra) {
  const keys = new Set();
  walkAst(extra, (n) => {
    if (n.type !== 'CallExpression' || nameOf(unwrap(n.callee)) !== 'primaryKey') return;
    walkAst(n.arguments, (m) => { if (m.type === 'MemberExpression' && !m.computed) keys.add(m.property.name); });
    return false;
  });
  return keys;
}

export function declared(tree, cfg) {
  const tables = {}, findings = [], consts = new Map(), schemas = new Map(), decls = [], fallback = cfg.schema.schemas[0];
  if (!cfg.schema.schema_files.length) throw new Incomplete('schema: [schema] schema_files is empty for orm = "drizzle"');
  for (const file of cfg.schema.schema_files) {
    if (!tree.exists(file)) throw new Incomplete(`schema: declared schema file ${file} is missing`);
    const { program, lineAt } = parseJs(file, tree.read(file));
    walkAst(program, (n) => {
      if (n.type !== 'VariableDeclarator' || n.id.type !== 'Identifier') return;
      const init = unwrap(n.init);
      if (init?.type === 'ObjectExpression') consts.set(n.id.name, init);
      if (init?.type === 'CallExpression' && nameOf(unwrap(init.callee)) === 'pgSchema' && strOf(init.arguments[0])) schemas.set(n.id.name, strOf(init.arguments[0]));
      decls.push({ file, lineAt, n, init });
    });
  }
  for (const { file, lineAt, n, init } of decls) {
    if (init?.type !== 'CallExpression') continue;
    const fn = unwrap(init.callee), name = strOf(init.arguments[0]);
    const schema = TABLE_FNS.has(nameOf(fn)) ? fallback : fn.type === 'MemberExpression' && nameOf(fn) === 'table' && fn.object.type === 'Identifier' ? schemas.get(fn.object.name) : undefined;
    if (schema === undefined || name == null) continue;
    let body = unwrap(init.arguments[1]);
    if (body?.type === 'ArrowFunctionExpression') body = unwrap(body.body);
    if (body?.type !== 'ObjectExpression') throw new Incomplete(`schema: ${file}:${lineAt(init.start)}: table ${name} has no literal column object`);
    const props = [];
    for (const p of body.properties) {
      const spread = p.type === 'SpreadElement' && p.argument.type === 'Identifier' ? consts.get(p.argument.name) : null;
      if (p.type === 'SpreadElement' && !spread) throw new Incomplete(`schema: ${file}:${lineAt(p.start)}: table ${name} spreads columns from an expression the schema files do not declare`);
      props.push(...(spread ? spread.properties : [p]));
    }
    const pk = compositeKeys(init.arguments.slice(2)), table = (tables[`${schema}.${name}`] = { orm: n.id.name, columns: {} }), bySql = new Map();
    for (const p of props) {
      const key = p.type === 'Property' ? keyName(p) : null, col = key != null ? column(p.value) : null;
      if (!col) throw new Incomplete(`schema: ${file}:${lineAt(p.start)}: table ${name} has a column this reader cannot follow`);
      const sql = col.sql ?? (cfg.schema.casing === 'snake_case' ? snake(key) : key);
      if (bySql.has(sql)) {
        findings.push({ rule: 'schema/duplicate-column', path: file, line: lineAt(p.start), severity: 'error', fp: `${name}.${sql}`,
          message: `${name}: properties ${bySql.get(sql)} and ${key} both map to SQL column ${sql}` });
        continue;
      }
      bySql.set(sql, key);
      table.columns[sql] = { key, nullable: !(col.notNull || pk.has(key)) };
    }
  }
  return { tables, findings };
}
