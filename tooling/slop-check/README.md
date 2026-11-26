# slop-check tests

Behavior tests for `skills/slop-cleanup/scripts/slop-check` (reference:
`skills/slop-cleanup/references/gates.md`). Each test copies a fixture repository from
`tests/fixtures/` into a fresh temp directory, which has no `.git`, and drives the real launcher
against it. The policy and fail-closed probes also run with no `git` on `PATH`. Test names carry the
critique finding ids they encode. The suite uses `node:test` only.

```sh
# from the repo root: Node >= 24, once per Python >= 3.9 found (the emitter runs under each);
# installs the checker's pinned parsers with (cd skills/slop-cleanup/scripts && npm ci) if missing
sh tooling/slop-check/run-tests.sh

# one interpreter
SLOP_CHECK_PYTHON=python3 node --test tooling/slop-check/tests/*.test.mjs

# opt-in: the templates against the pinned lint tools, and a fresh install of the skill
# (network: npm and uv, about 1 min)
SLOP_CHECK_TOOLCHAINS=1 node --test tooling/slop-check/tests/toolchain.test.mjs
```

- **Fixtures:** `ts-repo` (TypeScript, Drizzle, a structured logger, a vendored UI kit) and
  `py-repo` (psycopg with raw SQL, Alembic migrations, no ORM models). Each fixture's catalog,
  `tests/fixtures/catalogs/<fixture>.json`, comes from applying its migrations to PostgreSQL 18 and
  running `slop-check catalog-query`; tests copy it beside the fixture copy, outside the tree, and
  pass `--catalog-command "cat <that file>"`. `tests/fixtures/reports/` holds real output from ruff 0.17.6, pyright
  1.1.414, pydoclint 0.12.0, deptry 0.25.1, oxlint 1.95.0 and knip 6.48.0, with paths rewritten to
  the fixtures, and each tool's exit status beside its report.
- **PostgreSQL cases** apply real migrations to a throwaway database and read it through
  `--catalog-command`. They use a `postgres:18` container (`SLOP_CHECK_PG_IMAGE` overrides it) when
  Docker works, or a local `initdb`/`pg_ctl`/`psql`. With neither, they skip and print the reason.
- **Alembic cases** (`alembic upgrade heads`, and the case with ORM models) need a Python that
  imports `alembic`, `sqlalchemy` and `psycopg`. Point `SLOP_CHECK_ALEMBIC_PYTHON` at one, for
  example a venv with `alembic==1.20.1 sqlalchemy==2.1.9 'psycopg[binary]==3.3.9'`. Without one,
  they skip and print the reason.
- **Before and after:** `SLOP_CHECK_SCRIPTS` points the suite at another copy of the checker's
  `scripts/` folder.
