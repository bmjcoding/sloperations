# Accounts service

Queries live in `app/db.py` and go through psycopg. Migrations are Alembic revisions under
`migrations/versions/`; apply them with `alembic upgrade heads`.
