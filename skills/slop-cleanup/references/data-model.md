# Data model

Two blocking layers, one advisory inventory, and owner-gated changes. The production dialect is
declared in `cleanup.toml`; the disposable database always matches it (PostgreSQL for PostgreSQL;
SQLite never stands in). The catalog reader supports PostgreSQL; any other dialect makes the
`schema` check incomplete and the migrations get a manual review.

## Layer 1: declarations and the revision graph (blocking)

- **Drizzle:** `drizzle-kit check` and a clean `drizzle-kit generate` (the repository's `db:check`),
  plus `slop-check`'s reader over `schema_files`: two properties mapped to one SQL column fail
  (`schema/duplicate-column`). The reader follows `pgTable`, `sqliteTable`, `mysqlTable` and
  `pgSchema(...).table`, column objects or callbacks, spreads of a `const` column object declared in
  a schema file, `serial` and identity columns (NOT NULL), and composite primary keys. A table it
  cannot read completely makes the check incomplete; list every file that declares tables or shared
  columns in `schema_files`.
- **Alembic:** the revision chain is the source of truth. `slop-check` reads the revision files with
  `ast` in an isolated interpreter that imports nothing: duplicate revision ids, a `down_revision` or
  `depends_on` naming a missing revision, cycles, and heads that differ from `expected_heads` (a
  count, or a list of head ids and branch labels; a revision inherits its ancestors' labels) fail.
- **ORM models are optional.** With `target_metadata` set, the model comparison runs in the
  interpreter `SLOP_CHECK_MODELS_PYTHON` names (the repository's test environment, chosen by
  whoever runs the check, never by `cleanup.toml`). It imports the metadata and `model_packages`,
  fails a model module the metadata does not import, runs `configure_mappers()`, and fails a mapper
  whose key is neither the migrated table's primary key nor a NOT NULL unique index. Without
  `target_metadata`, the model comparison is skipped and the output says so.
- A physical primary key on every table is opt-in (`require_physical_primary_key = true`); by
  default a keyless table is a warning.

## Layer 2: real migrations and the live catalog (blocking)

Apply every migration to a disposable database of the production dialect, then read its catalog
(tables, columns, nullability, defaults, primary keys, unique indexes) from `information_schema` and
`pg_catalog`, using the same database URL for both:

```sh
export DATABASE_URL=postgresql://...       # a disposable database
alembic upgrade heads                      # or the repository's Drizzle migrate command
slop-check check --root . --catalog-command "psql -X -At -v ON_ERROR_STOP=1 $DATABASE_URL"
```

`--catalog-command` is required for `check`, `baseline` and `inventory`: it runs in the repository
root, reads `slop-check catalog-query` on stdin, and its output is the catalog. No file in the tree is
read as one. The output carries a digest of the migrations folder and, for Alembic, the database's
`alembic_version`; a catalog from other migrations, an empty one, or one at revisions other than the
graph's heads is incomplete.

`slop-check` compares the declared schema (Drizzle `schema.ts`, or Alembic models when present) with
that catalog in both directions, table by table within each configured schema: a declared table or
column missing after migrating, a nullability mismatch, and a migrated column or table nothing
declares all fail. This catches a SQL-only migration edit that drops an `ADD COLUMN` while
`schema.ts` and the snapshots stay untouched. Where models exist, also run `alembic check`; it does
not see triggers, functions, unnamed `CHECK` constraints or reliable server defaults. Keep the
repository's own smoke tests for sequences, generated columns and adoption.

`squawk` is optional, for static PostgreSQL `.sql` migrations only. Imperative Python data
migrations get `manual-review-required` in the ledger.

## Advisory inventory (never blocking)

`slop-check inventory` maps every catalog column to references found in code and in `.sql` files
under the source roots, excluding the schema files, migrations and tests:

| Status | Meaning |
| --- | --- |
| `referenced` | A resolved SQL string names the table and the column (reads and writes alike), a SQL fragment names the column, a `psycopg.sql.Identifier` names it, or (Drizzle) code reads `<table>.<property>` |
| `inconclusive` | Otherwise, the table is read through `SELECT *` or `RETURNING *` |
| `unresolved` | Otherwise, SQL that names the table, or names no table, is built from run-time values: a TypeScript template with `${}` or a `+` concatenation, a Python f-string, `+` or `%`, a variable or parameter, `Identifier(variable)`, or a call the reader does not know |
| `unreferenced` | None of the above; a candidate for the evidence below, never a drop list |

Notes on each row: a database default, the primary key, a table read through `SELECT *`, a column
named only in `INSERT` statements (possibly write-only), an ORM association table, and a table with
the same column shape as another (compare them for a redundant fact). Python sources:
`execute`/`executemany`/`stream`/`copy` arguments, positional or `query=`, `psycopg.sql`
compositions, every string with SQL keywords, and names bound only to static SQL in their scope.
TypeScript sources: every string or template with SQL keywords. The unresolved sites are listed so
the lead can read them.

## Dead or write-only columns

Evidence to propose: the inventory row, a check of external consumers, and runtime query or value
evidence where production exists. Database defaults (`DEFAULT now()`) mean "never written by code"
is not "unused".

## Live redundant columns or tables

Two maintained copies of one fact pass every reference test. Surface them by schema shape and
`domain-modeling`'s canonical-fact check (same fact, row identity, cardinality, retention), then
prove them with a row equivalence and divergence report. Consolidate in a separate owner-approved
series: pick the canonical copy, reconcile or dual-write, backfill, switch readers, then contract.

## Contraction (every drop, after owner sign-off; never in a cleanup PR)

1. Drain every in-repository and external reader; wait past every rollback-eligible release.
2. Drop `NOT NULL` or add a safe default (and ignore the column in whole-row ORM selects) before or
   with the release that stops writing it; deploy and observe.
3. Drop the column or table in a later, separate migration, **only after every running and
   rollback-eligible release that reads or writes it (background workers and external writers
   included) is past the rollback window and works against the contracted schema.**
