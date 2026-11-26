// Check 3, schema: declaration hazards, declared-versus-migrated catalog, the Alembic revision graph,
// and the advisory column inventory. The PostgreSQL cases apply real migrations to a throwaway
// database; they skip, with the reason, when no container runtime or local PostgreSQL exists.
// Test names carry the critique finding ids they encode (reviewer and number).
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { after, before, describe, test } from 'node:test';
import { append, catalogFile, catalogQuery, edit, expectClean, expectFinding, expectIncomplete, fixture, freshDb, PYTHON, read, rewriteCatalog, run, startPostgres, tempDir, write } from './helpers.mjs';

const schema = (dir, extra = [], opts = {}) => run(['check', '--only', 'schema', ...extra], dir, opts);
const inv = (dir, opts) => { const r = run(['inventory'], dir, opts); assert.equal(r.status, 0, r.stdout + r.stderr); return r; };
const row = (res, t, c) => res.records.find((r) => r.table === t && r.column === c);

// A Python that can import alembic, sqlalchemy and psycopg, for the model and upgrade cases.
const ALEMBIC_PY = process.env.SLOP_CHECK_ALEMBIC_PYTHON || PYTHON;
const noAlembic = spawnSync(ALEMBIC_PY, ['-c', 'import alembic, sqlalchemy, psycopg'], { encoding: 'utf8' }).status === 0 ? false
  : `${ALEMBIC_PY} cannot import alembic, sqlalchemy and psycopg; set SLOP_CHECK_ALEMBIC_PYTHON`;
const MODELS = { SLOP_CHECK_MODELS_PYTHON: ALEMBIC_PY };

describe('Drizzle', () => {
  test('the clean fixture matches its migrated catalog', () => expectClean(schema(fixture('ts-repo'))));

  test('schema/duplicate-column: two properties mapped to one SQL column', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'server/db/schema.ts', '  title: text("title").notNull(),', '  title: text("title").notNull(),\n  heading: text("title"),');
    expectFinding(schema(dir), 'schema/duplicate-column', { path: 'server/db/schema.ts', message: 'heading' });
  });

  test('schema/missing-column, nullability and undeclared-column: schema.ts drifts from the migrated catalog', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'server/db/schema.ts', '  title: text("title").notNull(),', '  title: text("title").notNull(),\n  priority: integer("priority"),');
    edit(dir, 'server/db/schema.ts', 'body: text("body").notNull(),', 'body: text("body"),');
    edit(dir, 'server/db/schema.ts', '  nextDueAt: timestamp("next_due_at", { withTimezone: true }),\n', '');
    const res = schema(dir);
    expectFinding(res, 'schema/missing-column', { message: 'incidents.priority' });
    expectFinding(res, 'schema/nullability', { message: 'status_updates.body' });
    expectFinding(res, 'schema/undeclared-column', { message: 'status_updates.next_due_at' });
  });

  test('[Fable 1, Astra 7] module constants and column helpers that are not tables do not crash the reader', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'server/db/schema.ts', 'export const incidents', 'const PAGE_SIZE = 25;\nconst tz = { withTimezone: true };\nconst at = (name: string) => timestamp(name, tz);\nexport const incidents');
    edit(dir, 'server/db/schema.ts', 'sealedAt: timestamp("sealed_at", { withTimezone: true }).defaultNow()', 'sealedAt: at("sealed_at").defaultNow()');
    expectClean(schema(dir));
  });

  test('[Fable 1, Neon 10] serial builders are NOT NULL; namespace imports of pg-core work', () => {
    const dir = fixture('ts-repo');
    let s = read(dir, 'server/db/schema.ts').replace(/^import .*\n/, 'import * as pg from "drizzle-orm/pg-core";\n');
    s = s.replaceAll(/(?<![.\w])(pgTable|serial|text|timestamp|integer|primaryKey)\(/g, 'pg.$1(');
    assert.ok(s.includes('id: pg.serial("id").primaryKey(),\n  title'));
    write(dir, 'server/db/schema.ts', s.replace('id: pg.serial("id").primaryKey(),\n  title', 'id: pg.serial("id"),\n  title')); // serial alone is NOT NULL
    expectClean(schema(dir));
  });

  test('[Astra 7] shared columns spread from a const object are compared; a spread the reader cannot follow is incomplete', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'server/db/schema.ts', 'export const incidents', 'const audit = { createdBy: text("created_by") };\nexport const incidents');
    edit(dir, 'server/db/schema.ts', '  title: text("title").notNull(),', '  title: text("title").notNull(),\n  ...audit,');
    expectFinding(schema(dir), 'schema/missing-column', { message: 'incidents.created_by' });
    edit(dir, 'server/db/schema.ts', '  ...audit,', '  ...auditColumns(),');
    expectIncomplete(schema(dir), 'spreads columns');
  });

  test('[Astra 3] an empty, unmigrated or stale catalog is incomplete; [Fable 7, Grok 6] so is one older than the migrations', () => {
    const dir = fixture('ts-repo');
    fs.writeFileSync(catalogFile(dir), '{}');
    expectIncomplete(schema(dir), 'unknown shape');
    const stale = fixture('ts-repo');
    write(stale, 'server/db/migrations/0001_trust_and_updates.sql', '');
    expectIncomplete(schema(stale), 'not dumped from the current migrations');
  });

  test('[Grok 6, Astra 3, critique-2 row 3] the catalog comes only from --catalog-command; a catalog file in the tree is not evidence', () => {
    const dir = fixture('ts-repo'), forged = JSON.parse(fs.readFileSync(catalogFile(dir), 'utf8'));
    edit(dir, 'server/db/schema.ts', '  title: text("title").notNull(),', '  title: text("title").notNull(),\n  priority: integer("priority"),');
    forged.columns.push({ schema: 'public', table: 'incidents', column: 'priority', nullable: true, type: 'integer', default: null });
    write(dir, '.slop/catalog.json', JSON.stringify(forged)); // agrees with the edited schema, and carries the right digest
    expectIncomplete(schema(dir, [], { catalog: false }), 'no --catalog-command');
    expectFinding(schema(dir), 'schema/missing-column', { message: 'incidents.priority' }); // the database never got the column
  });

  test('[Astra 3, Neon 10] tables are qualified by schema, so same-named tables in two schemas stay apart', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'cleanup.toml', '[schema]\n', '[schema]\nschemas = ["public", "audit"]\n');
    append(dir, 'server/db/schema.ts', 'import { pgSchema } from "drizzle-orm/pg-core";\nconst auditSchema = pgSchema("audit");\nexport const auditIncidents = auditSchema.table("incidents", { id: serial("id").primaryKey(), title: text("title") });\n');
    rewriteCatalog(dir, (c) => {
      c.columns.push({ schema: 'audit', table: 'incidents', column: 'id', nullable: false, type: 'integer', default: null }, { schema: 'audit', table: 'incidents', column: 'title', nullable: false, type: 'text', default: null });
      c.indexes.push({ schema: 'audit', table: 'incidents', name: 'audit_incidents_pkey', primary: true, unique: true, columns: ['id'] });
    });
    const res = schema(dir);
    assert.deepEqual(res.records.filter((f) => f.rule !== 'partial-run').map((f) => f.message), ['audit.incidents.title is nullable in the schema but not null after migrating']);
  });

  test('a production dialect without a catalog reader is incomplete (manual review)', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'cleanup.toml', 'production_dialect = "postgresql"', 'production_dialect = "mysql"');
    expectIncomplete(schema(dir), 'manual-review-required');
  });

  test('schema/no-primary-key blocks only when require_physical_primary_key is set', () => {
    const dir = fixture('ts-repo');
    rewriteCatalog(dir, (c) => { c.indexes = c.indexes.filter((i) => i.table !== 'incident_tags'); });
    const advisory = schema(dir, ['--warnings']);
    expectClean(advisory);
    expectFinding(advisory, 'schema/no-primary-key', { message: 'incident_tags' });
    edit(dir, 'cleanup.toml', 'migrations = "server/db/migrations"', 'migrations = "server/db/migrations"\nrequire_physical_primary_key = true');
    expectFinding(schema(dir), 'schema/no-primary-key', { message: 'incident_tags' });
  });

  test('[Astra 12] an unknown orm is a usage error, not a pass', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'cleanup.toml', 'orm = "drizzle"', 'orm = "drizle"');
    assert.equal(schema(dir).status, 2);
  });

  test('[Fable 10, Astra 16] inventory: SELECT * is noted on every column, write-only columns and look-alike tables are noted', () => {
    const dir = fixture('ts-repo');
    const res = inv(dir);
    assert.equal(row(res, 'incidents', 'title').status, 'inconclusive');
    assert.ok(row(res, 'incidents', 'sealed_at').notes.includes('its table is read through SELECT * or RETURNING *'));
    assert.ok(row(res, 'status_updates', 'next_due_at').notes.includes('named only in INSERT statements'));
    rewriteCatalog(dir, (c) => { for (const col of ['incident_id', 'body', 'next_due_at']) c.columns.push({ schema: 'public', table: 'status_notes', column: col, nullable: false, type: 'text', default: null }); });
    assert.ok(row(inv(dir), 'status_notes', 'body').notes.some((n) => n.startsWith('same column shape as status_updates')));
  });

  test('[Muse H3, Fable 9, Neon 2, Grok 3] inventory: TypeScript SQL built with ${} or + is unresolved, never unreferenced', () => {
    for (const q of ['`INSERT INTO status_updates (incident_id, body, ${column}) VALUES ($1, $2, $3)`', '"INSERT INTO status_updates (incident_id, body, " + column + ") VALUES ($1, $2, $3)"']) {
      const dir = fixture('ts-repo');
      edit(dir, 'server/store.ts', 'export const addUpdate = `INSERT INTO status_updates (incident_id, body, next_due_at) VALUES ($1, $2, $3)`;', `export const addUpdate = (column: string) => ${q};`);
      const res = inv(dir);
      assert.equal(row(res, 'status_updates', 'next_due_at').status, 'unresolved', q);
      assert.ok(res.records.some((r) => r.unresolved?.sql.includes('status_updates')));
    }
  });

  test('[Grok 5, Astra 16] inventory: an unrelated same-named identifier is not an ORM reference; a table member access is', () => {
    const dir = fixture('ts-repo');
    edit(dir, 'server/store.ts', ', next_due_at) VALUES ($1, $2, $3)', ') VALUES ($1, $2)');
    write(dir, 'server/other.ts', 'export const nextDueAt = 1;\n');
    assert.equal(row(inv(dir), 'status_updates', 'next_due_at').status, 'unreferenced');
    write(dir, 'server/other.ts', 'import { statusUpdates } from "./db/schema.ts";\nexport const due = statusUpdates.nextDueAt;\n');
    assert.equal(row(inv(dir), 'status_updates', 'next_due_at').status, 'referenced');
  });
});

describe('Alembic (revision chain as the source of truth)', () => {
  test('with migrations and no model classes: passes and says the model comparison was skipped', () => {
    const res = schema(fixture('py-repo'));
    expectClean(res);
    assert.match(res.stderr, /no target_metadata.*model-versus-migration comparison is skipped/);
  });

  test('schema/duplicate-revision, schema/broken-chain and schema/heads from the revision files alone', () => {
    const dir = fixture('py-repo');
    write(dir, 'migrations/versions/0002_copy.py', read(dir, 'migrations/versions/0002_audit.py'));
    write(dir, 'migrations/versions/0003_branch.py', 'revision = "0003"\ndown_revision = "0001"\n');
    write(dir, 'migrations/versions/0004_orphan.py', 'revision = "0004"\ndown_revision = "0009"\n');
    rewriteCatalog(dir);
    const res = schema(dir);
    expectFinding(res, 'schema/duplicate-revision', { message: '0002' });
    expectFinding(res, 'schema/broken-chain', { message: '0009' });
    expectFinding(res, 'schema/heads', { message: 'expects 1' });
  });

  test('[Astra 14, Neon 7] labeled heads are inherited by later revisions; merges pass; missing dependencies and cycles fail', () => {
    const dir = fixture('py-repo');
    write(dir, 'migrations/versions/0003_reports.py', 'revision = "0003"\ndown_revision = "0001"\nbranch_labels = ("reports",)\n');
    write(dir, 'migrations/versions/0004_reports_v2.py', 'revision = "0004"\ndown_revision = "0003"\nbranch_labels = None\n');
    edit(dir, 'cleanup.toml', 'expected_heads = 1', 'expected_heads = ["0002", "reports"]');
    rewriteCatalog(dir, (c) => { c.alembic_version = ['0002', '0004']; });
    expectClean(schema(dir));
    write(dir, 'migrations/versions/0005_merge.py', 'revision = "0005"\ndown_revision = ("0002", "0004")\n');
    edit(dir, 'cleanup.toml', 'expected_heads = ["0002", "reports"]', 'expected_heads = 1');
    rewriteCatalog(dir, (c) => { c.alembic_version = ['0005']; });
    expectClean(schema(dir));
    write(dir, 'migrations/versions/0006_dep.py', 'revision = "0006"\ndown_revision = "0005"\ndepends_on = "0099"\n');
    write(dir, 'migrations/versions/0007_a.py', 'revision = "0007"\ndown_revision = "0008"\n');
    write(dir, 'migrations/versions/0008_b.py', 'revision = "0008"\ndown_revision = "0007"\n');
    rewriteCatalog(dir, (c) => { c.alembic_version = ['0006']; });
    const res = schema(dir);
    expectFinding(res, 'schema/broken-chain', { message: '0099' });
    expectFinding(res, 'schema/cycle');
  });

  test('[Astra 3] a catalog dumped from a database at another revision is incomplete', () => {
    const dir = fixture('py-repo');
    rewriteCatalog(dir, (c) => { c.alembic_version = ['0001']; });
    expectIncomplete(schema(dir), 'not the heads');
  });

  test('[Astra 11, Neon 14] the graph check runs isolated and imports no application code', () => {
    const dir = fixture('py-repo'), marker = path.join(tempDir('marker'), 'imported');
    write(dir, 'app/__init__.py', `open(${JSON.stringify(marker)}, "w").write("x")\n`);
    expectClean(run(['check', '--only', 'comments,logs,schema'], dir));
    assert.ok(!fs.existsSync(marker), 'no application module was imported');
    edit(dir, 'cleanup.toml', 'expected_heads = 1', 'expected_heads = 1\ntarget_metadata = "app.models:Base.metadata"');
    expectIncomplete(schema(dir), 'SLOP_CHECK_MODELS_PYTHON');
    if (spawnSync(PYTHON, ['-c', 'import sqlalchemy']).status !== 0) { // [Neon 14] a models interpreter without SQLAlchemy is incomplete
      expectIncomplete(schema(dir, [], { env: { SLOP_CHECK_MODELS_PYTHON: PYTHON } }), 'cannot import sqlalchemy');
    }
    edit(dir, 'cleanup.toml', '[repo]\n', '[repo]\npython = ".venv/bin/python"\n');
    assert.equal(schema(dir).status, 2, 'the candidate cannot choose the interpreter');
  });

  test('inventory: a column read only through psycopg.sql.Identifier stays referenced; dynamic reads are unresolved', () => {
    const res = inv(fixture('py-repo'));
    assert.equal(row(res, 'accounts', 'time_zone').status, 'referenced');
    assert.equal(row(res, 'accounts', 'legacy_flag').status, 'unreferenced');
    assert.equal(row(res, 'audit_events', 'action').status, 'unresolved', 'sql.Identifier(column) is dynamic');
    assert.ok(!res.stdout.includes('"unused"'), 'the inventory never calls a column unused');
    const v = fixture('py-repo');
    edit(v, 'app/db.py', 'sql.Identifier("time_zone")', 'sql.Identifier(ZONE_COLUMN)');
    edit(v, 'app/db.py', 'LIST_ACCOUNTS =', 'ZONE_COLUMN = settings_column()\nLIST_ACCOUNTS =');
    assert.equal(row(inv(v), 'accounts', 'time_zone').status, 'unresolved');
  });

  test('[Fable 2] static queries held in local variables resolve in either function order', () => {
    const fns = ['def legacy_accounts(conn):\n    query = "SELECT id FROM accounts WHERE legacy_flag"\n    return conn.execute(query).fetchall()\n',
      'def audit_actions(conn):\n    query = "SELECT action FROM audit_events"\n    return conn.execute(query).fetchall()\n'];
    for (const order of [fns, [...fns].reverse()]) {
      const dir = fixture('py-repo');
      edit(dir, 'app/db.py', 'def audit_column(conn, column):', `${order.join('\n\n')}\n\ndef audit_column(conn, column):`);
      assert.equal(row(inv(dir), 'accounts', 'legacy_flag').status, 'referenced');
    }
  });

  test('[Astra 2, Neon 2, Fable 2] helper-supplied identifiers are unresolved; query= keywords, stream, copy and .sql files are read', () => {
    const dir = fixture('py-repo');
    edit(dir, 'app/db.py', 'sql.Identifier("time_zone")', 'sql.Identifier(column_name())');
    assert.equal(row(inv(dir), 'accounts', 'time_zone').status, 'unresolved');
    for (const [code, file] of [['    conn.execute(query="SELECT legacy_flag FROM accounts")\n', null], ['    cur.copy("COPY accounts (legacy_flag) TO STDOUT")\n', null], [null, 'app/queries/legacy.sql']]) {
      const d = fixture('py-repo');
      if (code) edit(d, 'app/db.py', '    """Read one audit column chosen by the caller."""\n', `    """Read one audit column chosen by the caller."""\n${code}`);
      if (file) write(d, file, 'SELECT legacy_flag FROM accounts;\n');
      assert.equal(row(inv(d), 'accounts', 'legacy_flag').status, 'referenced', code ?? file);
    }
    const s = fixture('py-repo');
    edit(s, 'app/db.py', 'def audit_column(conn, column):', 'def stream_audit(conn, col):\n    return conn.stream(f"SELECT {col} FROM audit_events")\n\n\ndef audit_column(conn, column):');
    edit(s, 'app/db.py', '    return conn.execute(sql.SQL("SELECT {} FROM audit_events").format(sql.Identifier(column))).fetchall()\n', '    return None\n');
    assert.equal(row(inv(s), 'audit_events', 'action').status, 'unresolved');
  });

  test('[Astra 13] a built-in format() call does not break the emitter', () => {
    const dir = fixture('py-repo');
    append(dir, 'app/db.py', '\n\ndef label(n):\n    """Format a count."""\n    return format(n, "d")\n');
    expectClean(run(['check', '--only', 'comments'], dir));
  });

  test('with models: declared columns, unimported model modules, row identity [Astra 15] and association tables', { skip: noAlembic }, () => {
    const dir = fixture('py-repo');
    write(dir, 'app/models/__init__.py', 'from sqlalchemy.orm import DeclarativeBase\n\n\nclass Base(DeclarativeBase):\n    pass\n\n\nfrom app.models import accounts  # noqa: E402,F401\n');
    write(dir, 'app/models/accounts.py', [
      'from sqlalchemy import Boolean, Integer, Text', 'from sqlalchemy.orm import Mapped, mapped_column', 'from app.models import Base', '', '',
      'class Account(Base):', '    __tablename__ = "accounts"', '    id: Mapped[int] = mapped_column(Integer, primary_key=True)',
      '    display_name: Mapped[str] = mapped_column(Text)', '    time_zone: Mapped[str] = mapped_column(Text)',
      '    legacy_flag: Mapped[bool] = mapped_column(Boolean, nullable=True)', '    nickname: Mapped[str] = mapped_column(Text, nullable=True)', ''].join('\n'));
    write(dir, 'app/models/audit.py', [
      'from sqlalchemy import BigInteger, Column, Integer, Table, Text', 'from app.models import Base', '',
      'audit_events = Table("audit_events", Base.metadata, Column("id", BigInteger, primary_key=True),',
      '    Column("account_id", Integer, nullable=False), Column("action", Text, nullable=False))', ''].join('\n'));
    edit(dir, 'cleanup.toml', 'expected_heads = 1', 'expected_heads = 1\ntarget_metadata = "app.models:Base.metadata"\nmodel_packages = ["app.models"]');
    const res = schema(dir, [], { env: MODELS });
    expectFinding(res, 'schema/missing-column', { message: 'accounts.nickname' });
    expectFinding(res, 'schema/unimported-model', { message: 'audit_events' });
    write(dir, 'app/models/accounts.py', read(dir, 'app/models/accounts.py').replace('    nickname: Mapped[str] = mapped_column(Text, nullable=True)\n', '')
      + '\n\nclass AccountByName(Base):\n    __table__ = Account.__table__\n    __mapper_args__ = {"primary_key": [Account.__table__.c.legacy_flag]}\n');
    expectFinding(schema(dir, [], { env: MODELS }), 'schema/row-identity', { message: 'AccountByName maps accounts by (legacy_flag)' });
    rewriteCatalog(dir, (c) => {
      c.indexes.push({ schema: 'public', table: 'accounts', name: 'accounts_display_name_key', primary: false, unique: true, columns: ['display_name'] });
    });
    write(dir, 'app/models/accounts.py', read(dir, 'app/models/accounts.py').replace('c.legacy_flag', 'c.display_name'));
    assert.ok(!schema(dir, [], { env: MODELS }).records.some((f) => f.rule === 'schema/row-identity'), 'a NOT NULL unique index is a usable candidate key');
    write(dir, 'app/models/accounts.py', read(dir, 'app/models/accounts.py') + '\n\nclass Keyless(Base):\n    __tablename__ = "keyless"\n    label: Mapped[str] = mapped_column(Text)\n');
    expectFinding(schema(dir, [], { env: MODELS }), 'schema/row-identity', { message: 'primary key' });
  });
});

describe('real migrations on a throwaway PostgreSQL', () => {
  let pg;
  before(() => { pg = startPostgres(); if (pg.skip) console.log(`# skipped: ${pg.skip}`); });
  after(() => pg?.stop?.());
  const applyDrizzle = (dir, db) => {
    for (const f of fs.readdirSync(path.join(dir, 'server/db/migrations')).sort()) {
      const r = pg.psql(db, read(dir, `server/db/migrations/${f}`).replaceAll('--> statement-breakpoint', ''));
      assert.equal(r.status, 0, r.stderr);
    }
  };

  test('Drizzle: applied migrations match schema.ts, read through --catalog-command', (t) => {
    if (pg.skip) return t.skip(pg.skip);
    const dir = fixture('ts-repo'), db = freshDb(pg);
    applyDrizzle(dir, db);
    expectClean(schema(dir, ['--catalog-command', pg.command(db)]));
  });

  test('Drizzle: a SQL-only edit that drops a declared ADD COLUMN fails naming the column', (t) => {
    if (pg.skip) return t.skip(pg.skip);
    const dir = fixture('ts-repo'), db = freshDb(pg);
    write(dir, 'server/db/migrations/0001_trust_and_updates.sql', 'SELECT 1;\n'); // schema.ts and snapshots untouched
    applyDrizzle(dir, db);
    expectFinding(schema(dir, ['--catalog-command', pg.command(db)]), 'schema/missing-column', { path: 'server/db/schema.ts', message: 'status_updates.next_due_at' });
  });

  test('[Astra 3] Alembic: one database for upgrade, applied-head check and dump; an unmigrated database is incomplete', (t) => {
    if (pg.skip) return t.skip(pg.skip);
    if (noAlembic) return t.skip(noAlembic);
    const dir = fixture('py-repo'), db = freshDb(pg);
    expectIncomplete(schema(dir, ['--catalog-command', pg.command(db)]), '--catalog-command failed');
    const up = spawnSync(ALEMBIC_PY, ['-m', 'alembic', 'upgrade', 'heads'], { cwd: dir, encoding: 'utf8', env: { ...process.env, DATABASE_URL: pg.dsn(db) } });
    assert.equal(up.status, 0, up.stderr);
    fs.rmSync(path.join(dir, 'migrations/versions/__pycache__'), { recursive: true, force: true });
    const res = schema(dir, ['--catalog-command', pg.command(db)]);
    expectClean(res);
    assert.ok(catalogQuery(dir).includes('alembic_version'));
  });
});
