"""Account queries over psycopg; the schema comes from the revisions in migrations/versions/."""
import logging

from psycopg import sql

log = logging.getLogger(__name__)

LIST_ACCOUNTS = "SELECT id, display_name FROM accounts ORDER BY id"


def list_accounts(conn):
    """Return every account, oldest first."""
    with conn.cursor() as cur:
        cur.execute(LIST_ACCOUNTS)
        log.info("accounts listed", extra={"count": cur.rowcount})
        return cur.fetchall()


def time_zone_of(conn, account_id):
    """Return one account's time zone."""
    query = sql.SQL("SELECT {} FROM {} WHERE id = %s").format(sql.Identifier("time_zone"), sql.Identifier("accounts"))
    return conn.execute(query, (account_id,)).fetchone()


def audit_column(conn, column):
    """Read one audit column chosen by the caller."""
    log.info("audit read", extra={"column": column})  # TODO(#4): limit the columns a caller may pick
    return conn.execute(sql.SQL("SELECT {} FROM audit_events").format(sql.Identifier(column))).fetchall()
